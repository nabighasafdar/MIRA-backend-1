import asyncio
import time
from typing import TYPE_CHECKING, ClassVar
import psutil
from bubus import BaseEvent
from cdp_use.cdp.target import SessionID, TargetID
from cdp_use.cdp.target.events import TargetCrashedEvent
from pydantic import Field, PrivateAttr
from system.browser.events import BrowserConnectedEvent, BrowserErrorEvent, BrowserStoppedEvent, TabClosedEvent, TabCreatedEvent
from system.browser.watchdog_base import BaseWatchdog
from system.utils import create_task_with_error_handling
if TYPE_CHECKING:
    pass

class NetworkRequestTracker:

    def __init__(self, request_id: str, start_time: float, url: str, method: str, resource_type: str | None=None):
        self.request_id = request_id
        self.start_time = start_time
        self.url = url
        self.method = method
        self.resource_type = resource_type

class CrashWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [BrowserConnectedEvent, BrowserStoppedEvent, TabCreatedEvent, TabClosedEvent]
    EMITS: ClassVar[list[type[BaseEvent]]] = [BrowserErrorEvent]
    network_timeout_seconds: float = Field(default=10.0)
    check_interval_seconds: float = Field(default=5.0)
    _active_requests: dict[str, NetworkRequestTracker] = PrivateAttr(default_factory=dict)
    _monitoring_task: asyncio.Task | None = PrivateAttr(default=None)
    _last_responsive_checks: dict[str, float] = PrivateAttr(default_factory=dict)
    _cdp_event_tasks: set[asyncio.Task] = PrivateAttr(default_factory=set)
    _targets_with_listeners: set[str] = PrivateAttr(default_factory=set)

    async def on_BrowserConnectedEvent(self, event: BrowserConnectedEvent) -> None:
        create_task_with_error_handling(self._start_monitoring(), name='start_crash_monitoring', logger_instance=self.logger, suppress_exceptions=True)

    async def on_BrowserStoppedEvent(self, event: BrowserStoppedEvent) -> None:
        await self._stop_monitoring()

    async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
        assert self.browser_session.agent_focus_target_id is not None, 'No current target ID'
        await self.attach_to_target(self.browser_session.agent_focus_target_id)

    async def on_TabClosedEvent(self, event: TabClosedEvent) -> None:
        if event.target_id in self._targets_with_listeners:
            self._targets_with_listeners.discard(event.target_id)
            self.logger.debug(f'[CrashWatchdog] Removed target {event.target_id[:8]}... from monitoring')

    async def attach_to_target(self, target_id: TargetID) -> None:
        try:
            if target_id in self._targets_with_listeners:
                self.logger.debug(f'[CrashWatchdog] Event listeners already exist for target: {target_id[:8]}...')
                return
            cdp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)

            def on_target_crashed(event: TargetCrashedEvent, session_id: SessionID | None=None):
                task = create_task_with_error_handling(self._on_target_crash_cdp(target_id), name='handle_target_crash', logger_instance=self.logger, suppress_exceptions=True)
                self._cdp_event_tasks.add(task)
                task.add_done_callback(lambda t: self._cdp_event_tasks.discard(t))
            cdp_session.cdp_client.register.Target.targetCrashed(on_target_crashed)
            self._targets_with_listeners.add(target_id)
            target = self.browser_session.session_manager.get_target(target_id)
            if target:
                self.logger.debug(f'[CrashWatchdog] Added target to monitoring: {target.url}')
        except Exception as e:
            self.logger.warning(f'[CrashWatchdog] Failed to attach to target {target_id}: {e}')

    async def _on_request_cdp(self, event: dict) -> None:
        request_id = event.get('requestId', '')
        request = event.get('request', {})
        self._active_requests[request_id] = NetworkRequestTracker(request_id=request_id, start_time=time.time(), url=request.get('url', ''), method=request.get('method', ''), resource_type=event.get('type'))

    def _on_response_cdp(self, event: dict) -> None:
        request_id = event.get('requestId', '')
        if request_id in self._active_requests:
            elapsed = time.time() - self._active_requests[request_id].start_time
            response = event.get('response', {})
            self.logger.debug(f"[CrashWatchdog] Request completed in {elapsed:.2f}s: {response.get('url', '')[:50]}...")

    def _on_request_failed_cdp(self, event: dict) -> None:
        request_id = event.get('requestId', '')
        if request_id in self._active_requests:
            elapsed = time.time() - self._active_requests[request_id].start_time
            self.logger.debug(f'[CrashWatchdog] Request failed after {elapsed:.2f}s: {self._active_requests[request_id].url[:50]}...')
            del self._active_requests[request_id]

    def _on_request_finished_cdp(self, event: dict) -> None:
        request_id = event.get('requestId', '')
        self._active_requests.pop(request_id, None)

    async def _on_target_crash_cdp(self, target_id: TargetID) -> None:
        self.logger.debug(f'[CrashWatchdog] Target crashed: {target_id[:8]}..., waiting for detach event')
        target = self.browser_session.session_manager.get_target(target_id)
        is_agent_focus = target and self.browser_session.agent_focus_target_id and (target.target_id == self.browser_session.agent_focus_target_id)
        if is_agent_focus:
            self.logger.error(f'[CrashWatchdog] 💥 Agent focus tab crashed: {target.url} (SessionManager will auto-recover)')
        self.event_bus.dispatch(BrowserErrorEvent(error_type='TargetCrash', message=f'Target crashed: {target_id}', details={'url': target.url if target else None, 'target_id': target_id, 'was_agent_focus': is_agent_focus}))

    async def _start_monitoring(self) -> None:
        assert self.browser_session.cdp_client is not None, 'Root CDP client not initialized - browser may not be connected yet'
        if self._monitoring_task and (not self._monitoring_task.done()):
            return
        self._monitoring_task = create_task_with_error_handling(self._monitoring_loop(), name='crash_monitoring_loop', logger_instance=self.logger, suppress_exceptions=True)

    async def _stop_monitoring(self) -> None:
        if self._monitoring_task and (not self._monitoring_task.done()):
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
            self.logger.debug('[CrashWatchdog] Monitoring loop stopped')
        for task in list(self._cdp_event_tasks):
            if not task.done():
                task.cancel()
        if self._cdp_event_tasks:
            await asyncio.gather(*self._cdp_event_tasks, return_exceptions=True)
        self._cdp_event_tasks.clear()
        self._active_requests.clear()
        self._targets_with_listeners.clear()
        self._last_responsive_checks.clear()

    async def _monitoring_loop(self) -> None:
        await asyncio.sleep(10)
        while True:
            try:
                await self._check_network_timeouts()
                await self._check_browser_health()
                await asyncio.sleep(self.check_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f'[CrashWatchdog] Error in monitoring loop: {e}')

    async def _check_network_timeouts(self) -> None:
        current_time = time.time()
        timed_out_requests = []
        if self._active_requests:
            self.logger.debug(f'[CrashWatchdog] Checking {len(self._active_requests)} active requests for timeouts (threshold: {self.network_timeout_seconds}s)')
        for request_id, tracker in self._active_requests.items():
            elapsed = current_time - tracker.start_time
            self.logger.debug(f'[CrashWatchdog] Request {tracker.url[:30]}... elapsed: {elapsed:.1f}s, timeout: {self.network_timeout_seconds}s')
            if elapsed >= self.network_timeout_seconds:
                timed_out_requests.append((request_id, tracker))
        for request_id, tracker in timed_out_requests:
            self.logger.warning(f'[CrashWatchdog] Network request timeout after {self.network_timeout_seconds}s: {tracker.method} {tracker.url[:100]}...')
            self.event_bus.dispatch(BrowserErrorEvent(error_type='NetworkTimeout', message=f'Network request timed out after {self.network_timeout_seconds}s', details={'url': tracker.url, 'method': tracker.method, 'resource_type': tracker.resource_type, 'elapsed_seconds': current_time - tracker.start_time}))
            del self._active_requests[request_id]

    async def _check_browser_health(self) -> None:
        try:
            self.logger.debug(f'[CrashWatchdog] Checking browser health for target {self.browser_session.agent_focus_target_id}')
            cdp_session = await self.browser_session.get_or_create_cdp_session()
            for target in self.browser_session.session_manager.get_all_page_targets():
                if self._is_new_tab_page(target.url) and target.url != 'about:blank':
                    self.logger.debug(f'[CrashWatchdog] Redirecting chrome://new-tab-page/ to about:blank {target.url}')
                    cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=target.target_id)
                    await cdp_session.cdp_client.send.Page.navigate(params={'url': 'about:blank'}, session_id=cdp_session.session_id)
            self.logger.debug(f'[CrashWatchdog] Attempting to run simple JS test expression in session {cdp_session} 1+1')
            await asyncio.wait_for(cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': '1+1'}, session_id=cdp_session.session_id), timeout=1.0)
            self.logger.debug(f'[CrashWatchdog] Browser health check passed for target {self.browser_session.agent_focus_target_id}')
        except Exception as e:
            self.logger.error(f'[CrashWatchdog] ❌ Crashed/unresponsive session detected for target {self.browser_session.agent_focus_target_id} error: {type(e).__name__}: {e} (Chrome will send detach event, SessionManager will auto-recover)')
        if self.browser_session._local_browser_watchdog and (proc := self.browser_session._local_browser_watchdog._subprocess):
            try:
                if proc.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                    self.logger.error(f'[CrashWatchdog] Browser process {proc.pid} has crashed')
                    self.event_bus.dispatch(BrowserErrorEvent(error_type='BrowserProcessCrashed', message=f'Browser process {proc.pid} has crashed', details={'pid': proc.pid, 'status': proc.status()}))
                    self.logger.warning('[CrashWatchdog] Browser process dead - stopping health monitoring')
                    await self._stop_monitoring()
                    return
            except Exception:
                pass

    @staticmethod
    def _is_new_tab_page(url: str) -> bool:
        return url in ['about:blank', 'chrome://new-tab-page/', 'chrome://newtab/']