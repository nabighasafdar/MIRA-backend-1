import asyncio
from pathlib import Path
from typing import Any, ClassVar
from bubus import BaseEvent
from cdp_use.cdp.page.events import ScreencastFrameEvent
from pydantic import PrivateAttr
from uuid_extensions import uuid7str
from system.browser.events import AgentFocusChangedEvent, BrowserConnectedEvent, BrowserStopEvent
from system.browser.profile import ViewportSize
from system.browser.video_recorder import VideoRecorderService
from system.browser.watchdog_base import BaseWatchdog
from system.utils import create_task_with_error_handling

class RecordingWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [BrowserConnectedEvent, BrowserStopEvent, AgentFocusChangedEvent]
    EMITS: ClassVar[list[type[BaseEvent]]] = []
    _recorder: VideoRecorderService | None = PrivateAttr(default=None)
    _current_session_id: str | None = PrivateAttr(default=None)
    _screencast_params: dict[str, Any] | None = PrivateAttr(default=None)

    async def on_BrowserConnectedEvent(self, event: BrowserConnectedEvent) -> None:
        profile = self.browser_session.browser_profile
        if not profile.record_video_dir:
            return
        size = profile.record_video_size
        if not size:
            self.logger.debug('record_video_size not specified, detecting viewport size...')
            size = await self._get_current_viewport_size()
        if not size:
            self.logger.warning('Cannot start video recording: viewport size could not be determined.')
            return
        video_format = getattr(profile, 'record_video_format', 'mp4').strip('.')
        output_path = Path(profile.record_video_dir) / f'{uuid7str()}.{video_format}'
        self.logger.debug(f'Initializing video recorder for format: {video_format}')
        self._recorder = VideoRecorderService(output_path=output_path, size=size, framerate=profile.record_video_framerate)
        self._recorder.start()
        if not self._recorder._is_active:
            self._recorder = None
            return
        self.browser_session.cdp_client.register.Page.screencastFrame(self.on_screencastFrame)
        self._screencast_params = {'format': 'png', 'quality': 90, 'maxWidth': size['width'], 'maxHeight': size['height'], 'everyNthFrame': 1}
        await self._start_screencast()

    async def on_AgentFocusChangedEvent(self, event: AgentFocusChangedEvent) -> None:
        if self._recorder:
            self.logger.debug(f'Agent focus changed to {event.target_id}, switching screencast...')
            await self._start_screencast()

    async def _start_screencast(self) -> None:
        if not self._recorder or not self._screencast_params:
            return
        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
            if self._current_session_id == cdp_session.session_id:
                return
            if self._current_session_id:
                try:
                    await self.browser_session.cdp_client.send.Page.stopScreencast(session_id=self._current_session_id)
                except Exception as e:
                    self.logger.debug(f'Failed to stop screencast on old session {self._current_session_id}: {e}')
            self._current_session_id = cdp_session.session_id
            await cdp_session.cdp_client.send.Page.startScreencast(params=self._screencast_params, session_id=cdp_session.session_id)
            self.logger.info(f'📹 Started/Switched video recording to target {cdp_session.target_id}')
        except Exception as e:
            self.logger.error(f'Failed to switch screencast via CDP: {e}')
            self._current_session_id = None

    async def _get_current_viewport_size(self) -> ViewportSize | None:
        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
            metrics = await cdp_session.cdp_client.send.Page.getLayoutMetrics(session_id=cdp_session.session_id)
            viewport = metrics.get('cssVisualViewport', {})
            width = viewport.get('clientWidth')
            height = viewport.get('clientHeight')
            if width and height:
                self.logger.debug(f'Detected viewport size: {width}x{height}')
                return ViewportSize(width=int(width), height=int(height))
        except Exception as e:
            self.logger.warning(f'Failed to get viewport size from browser: {e}')
        return None

    def on_screencastFrame(self, event: ScreencastFrameEvent, session_id: str | None) -> None:
        if self._current_session_id and session_id != self._current_session_id:
            return
        if not self._recorder:
            return
        self._recorder.add_frame(event['data'])
        create_task_with_error_handling(self._ack_screencast_frame(event, session_id), name='ack_screencast_frame', logger_instance=self.logger, suppress_exceptions=True)

    async def _ack_screencast_frame(self, event: ScreencastFrameEvent, session_id: str | None) -> None:
        try:
            await self.browser_session.cdp_client.send.Page.screencastFrameAck(params={'sessionId': event['sessionId']}, session_id=session_id)
        except Exception as e:
            self.logger.debug(f'Failed to acknowledge screencast frame: {e}')

    async def on_BrowserStopEvent(self, event: BrowserStopEvent) -> None:
        if self._recorder:
            recorder = self._recorder
            self._recorder = None
            self._current_session_id = None
            self._screencast_params = None
            self.logger.debug('Stopping video recording and saving file...')
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, recorder.stop_and_save)