from typing import TYPE_CHECKING, Any, ClassVar
from bubus import BaseEvent
from cdp_use.cdp.page import CaptureScreenshotParameters
from system.browser.events import ScreenshotEvent
from system.browser.views import BrowserError
from system.browser.watchdog_base import BaseWatchdog
from system.observability import observe_debug
if TYPE_CHECKING:
    pass

class ScreenshotWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[list[type[BaseEvent[Any]]]] = [ScreenshotEvent]
    EMITS: ClassVar[list[type[BaseEvent[Any]]]] = []

    @observe_debug(ignore_input=True, ignore_output=True, name='screenshot_event_handler')
    async def on_ScreenshotEvent(self, event: ScreenshotEvent) -> str:
        self.logger.debug('[ScreenshotWatchdog] Handler START - on_ScreenshotEvent called')
        try:
            focused_target = self.browser_session.get_focused_target()
            if focused_target and focused_target.target_type in ('page', 'tab'):
                target_id = focused_target.target_id
            else:
                target_type_str = focused_target.target_type if focused_target else 'None'
                self.logger.warning(f'[ScreenshotWatchdog] Focused target is {target_type_str}, falling back to page target')
                page_targets = self.browser_session.get_page_targets()
                if not page_targets:
                    raise BrowserError('[ScreenshotWatchdog] No page targets available for screenshot')
                target_id = page_targets[-1].target_id
            cdp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=True)
            params = CaptureScreenshotParameters(format='png', captureBeyondViewport=False)
            self.logger.debug(f'[ScreenshotWatchdog] Taking screenshot with params: {params}')
            result = await cdp_session.cdp_client.send.Page.captureScreenshot(params=params, session_id=cdp_session.session_id)
            if result and 'data' in result:
                self.logger.debug('[ScreenshotWatchdog] Screenshot captured successfully')
                return result['data']
            raise BrowserError('[ScreenshotWatchdog] Screenshot result missing data')
        except Exception as e:
            self.logger.error(f'[ScreenshotWatchdog] Screenshot failed: {e}')
            raise
        finally:
            try:
                await self.browser_session.remove_highlights()
            except Exception:
                pass