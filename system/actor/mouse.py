from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from cdp_use.cdp.input.commands import DispatchMouseEventParameters, SynthesizeScrollGestureParameters
    from cdp_use.cdp.input.types import MouseButton
    from system.browser.session import BrowserSession

class Mouse:

    def __init__(self, browser_session: 'BrowserSession', session_id: str | None=None, target_id: str | None=None):
        self._browser_session = browser_session
        self._client = browser_session.cdp_client
        self._session_id = session_id
        self._target_id = target_id

    async def click(self, x: int, y: int, button: 'MouseButton'='left', click_count: int=1) -> None:
        press_params: 'DispatchMouseEventParameters' = {'type': 'mousePressed', 'x': x, 'y': y, 'button': button, 'clickCount': click_count}
        await self._client.send.Input.dispatchMouseEvent(press_params, session_id=self._session_id)
        release_params: 'DispatchMouseEventParameters' = {'type': 'mouseReleased', 'x': x, 'y': y, 'button': button, 'clickCount': click_count}
        await self._client.send.Input.dispatchMouseEvent(release_params, session_id=self._session_id)

    async def down(self, button: 'MouseButton'='left', click_count: int=1) -> None:
        params: 'DispatchMouseEventParameters' = {'type': 'mousePressed', 'x': 0, 'y': 0, 'button': button, 'clickCount': click_count}
        await self._client.send.Input.dispatchMouseEvent(params, session_id=self._session_id)

    async def up(self, button: 'MouseButton'='left', click_count: int=1) -> None:
        params: 'DispatchMouseEventParameters' = {'type': 'mouseReleased', 'x': 0, 'y': 0, 'button': button, 'clickCount': click_count}
        await self._client.send.Input.dispatchMouseEvent(params, session_id=self._session_id)

    async def move(self, x: int, y: int, steps: int=1) -> None:
        _ = steps
        params: 'DispatchMouseEventParameters' = {'type': 'mouseMoved', 'x': x, 'y': y}
        await self._client.send.Input.dispatchMouseEvent(params, session_id=self._session_id)

    async def scroll(self, x: int=0, y: int=0, delta_x: int | None=None, delta_y: int | None=None) -> None:
        if not self._session_id:
            raise RuntimeError('Session ID is required for scroll operations')
        try:
            layout_metrics = await self._client.send.Page.getLayoutMetrics(session_id=self._session_id)
            viewport_width = layout_metrics['layoutViewport']['clientWidth']
            viewport_height = layout_metrics['layoutViewport']['clientHeight']
            scroll_x = x if x > 0 else viewport_width / 2
            scroll_y = y if y > 0 else viewport_height / 2
            scroll_delta_x = delta_x or 0
            scroll_delta_y = delta_y or 0
            await self._client.send.Input.dispatchMouseEvent(params={'type': 'mouseWheel', 'x': scroll_x, 'y': scroll_y, 'deltaX': scroll_delta_x, 'deltaY': scroll_delta_y}, session_id=self._session_id)
            return
        except Exception:
            pass
        try:
            params: 'SynthesizeScrollGestureParameters' = {'x': x, 'y': y, 'xDistance': delta_x or 0, 'yDistance': delta_y or 0}
            await self._client.send.Input.synthesizeScrollGesture(params, session_id=self._session_id)
        except Exception:
            scroll_js = f'window.scrollBy({delta_x or 0}, {delta_y or 0})'
            await self._client.send.Runtime.evaluate(params={'expression': scroll_js, 'returnByValue': True}, session_id=self._session_id)