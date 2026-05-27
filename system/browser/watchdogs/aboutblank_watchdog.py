from typing import TYPE_CHECKING, ClassVar
from bubus import BaseEvent
from cdp_use.cdp.target import TargetID
from pydantic import PrivateAttr
from system.browser.events import AboutBlankDVDScreensaverShownEvent, BrowserStopEvent, BrowserStoppedEvent, CloseTabEvent, NavigateToUrlEvent, TabClosedEvent, TabCreatedEvent
from system.browser.watchdog_base import BaseWatchdog
if TYPE_CHECKING:
    pass

class AboutBlankWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [BrowserStopEvent, BrowserStoppedEvent, TabCreatedEvent, TabClosedEvent]
    EMITS: ClassVar[list[type[BaseEvent]]] = [NavigateToUrlEvent, CloseTabEvent, AboutBlankDVDScreensaverShownEvent]
    _stopping: bool = PrivateAttr(default=False)

    async def on_BrowserStopEvent(self, event: BrowserStopEvent) -> None:
        self._stopping = True

    async def on_BrowserStoppedEvent(self, event: BrowserStoppedEvent) -> None:
        self._stopping = True

    async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
        if event.url == 'about:blank':
            await self._show_dvd_screensaver_on_about_blank_tabs()

    async def on_TabClosedEvent(self, event: TabClosedEvent) -> None:
        if self._stopping:
            return
        if not self.browser_session.is_cdp_connected:
            self.logger.debug('[AboutBlankWatchdog] CDP not connected, skipping tab recovery')
            return
        page_targets = await self.browser_session._cdp_get_all_pages()
        if len(page_targets) < 1:
            self.logger.debug('[AboutBlankWatchdog] Last tab closing, creating new about:blank tab to avoid closing entire browser')
            navigate_event = self.event_bus.dispatch(NavigateToUrlEvent(url='about:blank', new_tab=True))
            await navigate_event
            await self._show_dvd_screensaver_on_about_blank_tabs()
        else:
            await self._check_and_ensure_about_blank_tab()

    async def attach_to_target(self, target_id: TargetID) -> None:
        pass

    async def _check_and_ensure_about_blank_tab(self) -> None:
        try:
            if not self.browser_session.is_cdp_connected:
                return
            page_targets = await self.browser_session._cdp_get_all_pages()
            if len(page_targets) == 0:
                self.logger.debug('[AboutBlankWatchdog] No tabs exist, creating new about:blank DVD screensaver tab')
                navigate_event = self.event_bus.dispatch(NavigateToUrlEvent(url='about:blank', new_tab=True))
                await navigate_event
                await self._show_dvd_screensaver_on_about_blank_tabs()
        except Exception as e:
            self.logger.error(f'[AboutBlankWatchdog] Error ensuring about:blank tab: {e}')

    async def _show_dvd_screensaver_on_about_blank_tabs(self) -> None:
        try:
            page_targets = await self.browser_session._cdp_get_all_pages()
            browser_session_label = str(self.browser_session.id)[-4:]
            for page_target in page_targets:
                target_id = page_target['targetId']
                url = page_target['url']
                if url == 'about:blank':
                    await self._show_dvd_screensaver_loading_animation_cdp(target_id, browser_session_label)
        except Exception as e:
            self.logger.error(f'[AboutBlankWatchdog] Error showing DVD screensaver: {e}')

    async def _show_dvd_screensaver_loading_animation_cdp(self, target_id: TargetID, browser_session_label: str) -> None:
        try:
            temp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
            script = f"\n\t\t\t\t(function(browser_session_label) {{\n\t\t\t\t\t// Idempotency check\n\t\t\t\t\tif (window.__dvdAnimationRunning) {{\n\t\t\t\t\t\treturn; // Already running, don't add another\n\t\t\t\t\t}}\n\t\t\t\t\twindow.__dvdAnimationRunning = true;\n\t\t\t\t\t\n\t\t\t\t\t// Ensure document.body exists before proceeding\n\t\t\t\t\tif (!document.body) {{\n\t\t\t\t\t\t// Try again after DOM is ready\n\t\t\t\t\t\twindow.__dvdAnimationRunning = false; // Reset flag to retry\n\t\t\t\t\t\tif (document.readyState === 'loading') {{\n\t\t\t\t\t\t\tdocument.addEventListener('DOMContentLoaded', () => arguments.callee(browser_session_label));\n\t\t\t\t\t\t}}\n\t\t\t\t\t\treturn;\n\t\t\t\t\t}}\n\t\t\t\t\t\n\t\t\t\t\tconst animated_title = `Starting agent ${{browser_session_label}}...`;\n\t\t\t\t\tif (document.title === animated_title) {{\n\t\t\t\t\t\treturn;      // already run on this tab, dont run again\n\t\t\t\t\t}}\n\t\t\t\t\tdocument.title = animated_title;\n\n\t\t\t\t\t// Create the main overlay\n\t\t\t\t\tconst loadingOverlay = document.createElement('div');\n\t\t\t\t\tloadingOverlay.id = 'pretty-loading-animation';\n\t\t\t\t\tloadingOverlay.style.position = 'fixed';\n\t\t\t\t\tloadingOverlay.style.top = '0';\n\t\t\t\t\tloadingOverlay.style.left = '0';\n\t\t\t\t\tloadingOverlay.style.width = '100vw';\n\t\t\t\t\tloadingOverlay.style.height = '100vh';\n\t\t\t\t\tloadingOverlay.style.background = '#000';\n\t\t\t\t\tloadingOverlay.style.zIndex = '99999';\n\t\t\t\t\tloadingOverlay.style.overflow = 'hidden';\n\n\t\t\t\t\t// Create the image element\n\t\t\t\t\tconst img = document.createElement('img');\n\t\t\t\t\timg.src = 'https://cf.browser-use.com/logo.svg';\n\t\t\t\t\timg.alt = 'Browser-Use';\n\t\t\t\t\timg.style.width = '200px';\n\t\t\t\t\timg.style.height = 'auto';\n\t\t\t\t\timg.style.position = 'absolute';\n\t\t\t\t\timg.style.left = '0px';\n\t\t\t\t\timg.style.top = '0px';\n\t\t\t\t\timg.style.zIndex = '2';\n\t\t\t\t\timg.style.opacity = '0.8';\n\n\t\t\t\t\tloadingOverlay.appendChild(img);\n\t\t\t\t\tdocument.body.appendChild(loadingOverlay);\n\n\t\t\t\t\t// DVD screensaver bounce logic\n\t\t\t\t\tlet x = Math.random() * (window.innerWidth - 300);\n\t\t\t\t\tlet y = Math.random() * (window.innerHeight - 300);\n\t\t\t\t\tlet dx = 1.2 + Math.random() * 0.4; // px per frame\n\t\t\t\t\tlet dy = 1.2 + Math.random() * 0.4;\n\t\t\t\t\t// Randomize direction\n\t\t\t\t\tif (Math.random() > 0.5) dx = -dx;\n\t\t\t\t\tif (Math.random() > 0.5) dy = -dy;\n\n\t\t\t\t\tfunction animate() {{\n\t\t\t\t\t\tconst imgWidth = img.offsetWidth || 300;\n\t\t\t\t\t\tconst imgHeight = img.offsetHeight || 300;\n\t\t\t\t\t\tx += dx;\n\t\t\t\t\t\ty += dy;\n\n\t\t\t\t\t\tif (x <= 0) {{\n\t\t\t\t\t\t\tx = 0;\n\t\t\t\t\t\t\tdx = Math.abs(dx);\n\t\t\t\t\t\t}} else if (x + imgWidth >= window.innerWidth) {{\n\t\t\t\t\t\t\tx = window.innerWidth - imgWidth;\n\t\t\t\t\t\t\tdx = -Math.abs(dx);\n\t\t\t\t\t\t}}\n\t\t\t\t\t\tif (y <= 0) {{\n\t\t\t\t\t\t\ty = 0;\n\t\t\t\t\t\t\tdy = Math.abs(dy);\n\t\t\t\t\t\t}} else if (y + imgHeight >= window.innerHeight) {{\n\t\t\t\t\t\t\ty = window.innerHeight - imgHeight;\n\t\t\t\t\t\t\tdy = -Math.abs(dy);\n\t\t\t\t\t\t}}\n\n\t\t\t\t\t\timg.style.left = `${{x}}px`;\n\t\t\t\t\t\timg.style.top = `${{y}}px`;\n\n\t\t\t\t\t\trequestAnimationFrame(animate);\n\t\t\t\t\t}}\n\t\t\t\t\tanimate();\n\n\t\t\t\t\t// Responsive: update bounds on resize\n\t\t\t\t\twindow.addEventListener('resize', () => {{\n\t\t\t\t\t\tx = Math.min(x, window.innerWidth - img.offsetWidth);\n\t\t\t\t\t\ty = Math.min(y, window.innerHeight - img.offsetHeight);\n\t\t\t\t\t}});\n\n\t\t\t\t\t// Add a little CSS for smoothness\n\t\t\t\t\tconst style = document.createElement('style');\n\t\t\t\t\tstyle.textContent = `\n\t\t\t\t\t\t#pretty-loading-animation {{\n\t\t\t\t\t\t\t/*backdrop-filter: blur(2px) brightness(0.9);*/\n\t\t\t\t\t\t}}\n\t\t\t\t\t\t#pretty-loading-animation img {{\n\t\t\t\t\t\t\tuser-select: none;\n\t\t\t\t\t\t\tpointer-events: none;\n\t\t\t\t\t\t}}\n\t\t\t\t\t`;\n\t\t\t\t\tdocument.head.appendChild(style);\n\t\t\t\t}})('{browser_session_label}');\n\t\t\t"
            await temp_session.cdp_client.send.Runtime.evaluate(params={'expression': script}, session_id=temp_session.session_id)
            self.event_bus.dispatch(AboutBlankDVDScreensaverShownEvent(target_id=target_id))
        except Exception as e:
            self.logger.error(f'[AboutBlankWatchdog] Error injecting DVD screensaver: {e}')