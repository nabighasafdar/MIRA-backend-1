import asyncio
from dataclasses import dataclass
from typing import Any, ClassVar, Literal
from bubus import BaseEvent
from cdp_use.cdp.browseruse.events import CaptchaSolverFinishedEvent as CDPCaptchaSolverFinishedEvent
from cdp_use.cdp.browseruse.events import CaptchaSolverStartedEvent as CDPCaptchaSolverStartedEvent
from pydantic import PrivateAttr
from system.browser.events import BrowserConnectedEvent, BrowserStoppedEvent, CaptchaSolverFinishedEvent, CaptchaSolverStartedEvent, _get_timeout
from system.browser.watchdog_base import BaseWatchdog
CaptchaResultType = Literal['success', 'failed', 'timeout', 'unknown']

@dataclass
class CaptchaWaitResult:
    waited: bool
    vendor: str
    url: str
    duration_ms: int
    result: CaptchaResultType

class CaptchaWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [BrowserConnectedEvent, BrowserStoppedEvent]
    EMITS: ClassVar[list[type[BaseEvent]]] = [CaptchaSolverStartedEvent, CaptchaSolverFinishedEvent]
    _captcha_solving: bool = PrivateAttr(default=False)
    _captcha_solved_event: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
    _captcha_info: dict[str, Any] = PrivateAttr(default_factory=dict)
    _captcha_result: CaptchaResultType = PrivateAttr(default='unknown')
    _captcha_duration_ms: int = PrivateAttr(default=0)
    _cdp_handlers_registered: bool = PrivateAttr(default=False)

    def model_post_init(self, __context: Any) -> None:
        self._captcha_solved_event.set()

    async def on_BrowserConnectedEvent(self, event: BrowserConnectedEvent) -> None:
        if self._cdp_handlers_registered:
            self.logger.debug('CaptchaWatchdog: CDP handlers already registered, skipping')
            return
        cdp_client = self.browser_session.cdp_client

        def _on_captcha_started(event_data: CDPCaptchaSolverStartedEvent, session_id: str | None) -> None:
            try:
                self._captcha_solving = True
                self._captcha_result = 'unknown'
                self._captcha_duration_ms = 0
                self._captcha_info = {'vendor': event_data.get('vendor', 'unknown'), 'url': event_data.get('url', ''), 'targetId': event_data.get('targetId', ''), 'startedAt': event_data.get('startedAt', 0)}
                self._captcha_solved_event.clear()
                vendor = self._captcha_info['vendor']
                url = self._captcha_info['url']
                self.logger.info(f'🔒 Captcha solving started: {vendor} on {url}')
                self.event_bus.dispatch(CaptchaSolverStartedEvent(target_id=event_data.get('targetId', ''), vendor=vendor, url=url, started_at=event_data.get('startedAt', 0)))
            except Exception:
                self.logger.exception('Error handling captchaSolverStarted CDP event')
                self._captcha_solving = False
                self._captcha_solved_event.set()

        def _on_captcha_finished(event_data: CDPCaptchaSolverFinishedEvent, session_id: str | None) -> None:
            try:
                success = event_data.get('success', False)
                self._captcha_solving = False
                self._captcha_duration_ms = event_data.get('durationMs', 0)
                self._captcha_result = 'success' if success else 'failed'
                vendor = event_data.get('vendor', self._captcha_info.get('vendor', 'unknown'))
                url = event_data.get('url', self._captcha_info.get('url', ''))
                duration_s = self._captcha_duration_ms / 1000
                self.logger.info(f'🔓 Captcha solving finished: {self._captcha_result} — {vendor} on {url} ({duration_s:.1f}s)')
                self._captcha_solved_event.set()
                self.event_bus.dispatch(CaptchaSolverFinishedEvent(target_id=event_data.get('targetId', ''), vendor=vendor, url=url, duration_ms=self._captcha_duration_ms, finished_at=event_data.get('finishedAt', 0), success=success))
            except Exception:
                self.logger.exception('Error handling captchaSolverFinished CDP event')
                self._captcha_solving = False
                self._captcha_solved_event.set()
        cdp_client.register.BrowserUse.captchaSolverStarted(_on_captcha_started)
        cdp_client.register.BrowserUse.captchaSolverFinished(_on_captcha_finished)
        self._cdp_handlers_registered = True
        self.logger.debug('🔒 CaptchaWatchdog: registered CDP event handlers for BrowserUse captcha events')

    async def on_BrowserStoppedEvent(self, event: BrowserStoppedEvent) -> None:
        self._captcha_solving = False
        self._captcha_result = 'unknown'
        self._captcha_duration_ms = 0
        self._captcha_info = {}
        self._captcha_solved_event.set()
        self._cdp_handlers_registered = False

    async def wait_if_captcha_solving(self, timeout: float | None=None) -> CaptchaWaitResult | None:
        if not self._captcha_solving:
            return None
        if timeout is None:
            timeout = _get_timeout('TIMEOUT_CaptchaSolverWait', 120.0)
        assert timeout is not None
        vendor = self._captcha_info.get('vendor', 'unknown')
        url = self._captcha_info.get('url', '')
        self.logger.info(f'⏳ Waiting for {vendor} captcha to be solved on {url} (timeout={timeout}s)...')
        try:
            await asyncio.wait_for(self._captcha_solved_event.wait(), timeout=timeout)
            return CaptchaWaitResult(waited=True, vendor=vendor, url=url, duration_ms=self._captcha_duration_ms, result=self._captcha_result)
        except TimeoutError:
            self._captcha_solving = False
            self._captcha_solved_event.set()
            self.logger.warning(f'⏰ Captcha wait timed out after {timeout}s for {vendor} on {url}')
            return CaptchaWaitResult(waited=True, vendor=vendor, url=url, duration_ms=int(timeout * 1000), result='timeout')