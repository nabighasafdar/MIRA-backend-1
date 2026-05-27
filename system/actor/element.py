import asyncio
from typing import TYPE_CHECKING, Literal, Union
from cdp_use.client import logger
from typing_extensions import TypedDict
if TYPE_CHECKING:
    from cdp_use.cdp.dom.commands import DescribeNodeParameters, FocusParameters, GetAttributesParameters, GetBoxModelParameters, PushNodesByBackendIdsToFrontendParameters, RequestChildNodesParameters, ResolveNodeParameters
    from cdp_use.cdp.input.commands import DispatchMouseEventParameters
    from cdp_use.cdp.input.types import MouseButton
    from cdp_use.cdp.page.commands import CaptureScreenshotParameters
    from cdp_use.cdp.page.types import Viewport
    from cdp_use.cdp.runtime.commands import CallFunctionOnParameters
    from system.browser.session import BrowserSession
ModifierType = Literal['Alt', 'Control', 'Meta', 'Shift']

class Position(TypedDict):
    x: float
    y: float

class BoundingBox(TypedDict):
    x: float
    y: float
    width: float
    height: float

class ElementInfo(TypedDict):
    backendNodeId: int
    nodeId: int | None
    nodeName: str
    nodeType: int
    nodeValue: str | None
    attributes: dict[str, str]
    boundingBox: BoundingBox | None
    error: str | None

class Element:

    def __init__(self, browser_session: 'BrowserSession', backend_node_id: int, session_id: str | None=None):
        self._browser_session = browser_session
        self._client = browser_session.cdp_client
        self._backend_node_id = backend_node_id
        self._session_id = session_id

    async def _get_node_id(self) -> int:
        params: 'PushNodesByBackendIdsToFrontendParameters' = {'backendNodeIds': [self._backend_node_id]}
        result = await self._client.send.DOM.pushNodesByBackendIdsToFrontend(params, session_id=self._session_id)
        return result['nodeIds'][0]

    async def _get_remote_object_id(self) -> str | None:
        node_id = await self._get_node_id()
        params: 'ResolveNodeParameters' = {'nodeId': node_id}
        result = await self._client.send.DOM.resolveNode(params, session_id=self._session_id)
        object_id = result['object'].get('objectId', None)
        if not object_id:
            return None
        return object_id

    async def click(self, button: 'MouseButton'='left', click_count: int=1, modifiers: list[ModifierType] | None=None) -> None:
        try:
            layout_metrics = await self._client.send.Page.getLayoutMetrics(session_id=self._session_id)
            viewport_width = layout_metrics['layoutViewport']['clientWidth']
            viewport_height = layout_metrics['layoutViewport']['clientHeight']
            quads = []
            try:
                content_quads_result = await self._client.send.DOM.getContentQuads(params={'backendNodeId': self._backend_node_id}, session_id=self._session_id)
                if 'quads' in content_quads_result and content_quads_result['quads']:
                    quads = content_quads_result['quads']
            except Exception:
                pass
            if not quads:
                try:
                    box_model = await self._client.send.DOM.getBoxModel(params={'backendNodeId': self._backend_node_id}, session_id=self._session_id)
                    if 'model' in box_model and 'content' in box_model['model']:
                        content_quad = box_model['model']['content']
                        if len(content_quad) >= 8:
                            quads = [[content_quad[0], content_quad[1], content_quad[2], content_quad[3], content_quad[4], content_quad[5], content_quad[6], content_quad[7]]]
                except Exception:
                    pass
            if not quads:
                try:
                    result = await self._client.send.DOM.resolveNode(params={'backendNodeId': self._backend_node_id}, session_id=self._session_id)
                    if 'object' in result and 'objectId' in result['object']:
                        object_id = result['object']['objectId']
                        bounds_result = await self._client.send.Runtime.callFunctionOn(params={'functionDeclaration': '\n\t\t\t\t\t\t\t\t\tfunction() {\n\t\t\t\t\t\t\t\t\t\tconst rect = this.getBoundingClientRect();\n\t\t\t\t\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\t\t\t\t\tx: rect.left,\n\t\t\t\t\t\t\t\t\t\t\ty: rect.top,\n\t\t\t\t\t\t\t\t\t\t\twidth: rect.width,\n\t\t\t\t\t\t\t\t\t\t\theight: rect.height\n\t\t\t\t\t\t\t\t\t\t};\n\t\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t\t', 'objectId': object_id, 'returnByValue': True}, session_id=self._session_id)
                        if 'result' in bounds_result and 'value' in bounds_result['result']:
                            rect = bounds_result['result']['value']
                            x, y, w, h = (rect['x'], rect['y'], rect['width'], rect['height'])
                            quads = [[x, y, x + w, y, x + w, y + h, x, y + h]]
                except Exception:
                    pass
            if not quads:
                try:
                    result = await self._client.send.DOM.resolveNode(params={'backendNodeId': self._backend_node_id}, session_id=self._session_id)
                    if 'object' not in result or 'objectId' not in result['object']:
                        raise Exception('Failed to find DOM element based on backendNodeId, maybe page content changed?')
                    object_id = result['object']['objectId']
                    await self._client.send.Runtime.callFunctionOn(params={'functionDeclaration': 'function() { this.click(); }', 'objectId': object_id}, session_id=self._session_id)
                    await asyncio.sleep(0.05)
                    return
                except Exception as js_e:
                    raise Exception(f'Failed to click element: {js_e}')
            best_quad = None
            best_area = 0
            for quad in quads:
                if len(quad) < 8:
                    continue
                xs = [quad[i] for i in range(0, 8, 2)]
                ys = [quad[i] for i in range(1, 8, 2)]
                min_x, max_x = (min(xs), max(xs))
                min_y, max_y = (min(ys), max(ys))
                if max_x < 0 or max_y < 0 or min_x > viewport_width or (min_y > viewport_height):
                    continue
                visible_min_x = max(0, min_x)
                visible_max_x = min(viewport_width, max_x)
                visible_min_y = max(0, min_y)
                visible_max_y = min(viewport_height, max_y)
                visible_width = visible_max_x - visible_min_x
                visible_height = visible_max_y - visible_min_y
                visible_area = visible_width * visible_height
                if visible_area > best_area:
                    best_area = visible_area
                    best_quad = quad
            if not best_quad:
                best_quad = quads[0]
            center_x = sum((best_quad[i] for i in range(0, 8, 2))) / 4
            center_y = sum((best_quad[i] for i in range(1, 8, 2))) / 4
            center_x = max(0, min(viewport_width - 1, center_x))
            center_y = max(0, min(viewport_height - 1, center_y))
            try:
                await self._client.send.DOM.scrollIntoViewIfNeeded(params={'backendNodeId': self._backend_node_id}, session_id=self._session_id)
                await asyncio.sleep(0.05)
            except Exception:
                pass
            modifier_value = 0
            if modifiers:
                modifier_map = {'Alt': 1, 'Control': 2, 'Meta': 4, 'Shift': 8}
                for mod in modifiers:
                    modifier_value |= modifier_map.get(mod, 0)
            try:
                await self._client.send.Input.dispatchMouseEvent(params={'type': 'mouseMoved', 'x': center_x, 'y': center_y}, session_id=self._session_id)
                await asyncio.sleep(0.05)
                try:
                    await asyncio.wait_for(self._client.send.Input.dispatchMouseEvent(params={'type': 'mousePressed', 'x': center_x, 'y': center_y, 'button': button, 'clickCount': click_count, 'modifiers': modifier_value}, session_id=self._session_id), timeout=1.0)
                    await asyncio.sleep(0.08)
                except TimeoutError:
                    pass
                try:
                    await asyncio.wait_for(self._client.send.Input.dispatchMouseEvent(params={'type': 'mouseReleased', 'x': center_x, 'y': center_y, 'button': button, 'clickCount': click_count, 'modifiers': modifier_value}, session_id=self._session_id), timeout=3.0)
                except TimeoutError:
                    pass
            except Exception as e:
                try:
                    result = await self._client.send.DOM.resolveNode(params={'backendNodeId': self._backend_node_id}, session_id=self._session_id)
                    if 'object' not in result or 'objectId' not in result['object']:
                        raise Exception('Failed to find DOM element based on backendNodeId, maybe page content changed?')
                    object_id = result['object']['objectId']
                    await self._client.send.Runtime.callFunctionOn(params={'functionDeclaration': 'function() { this.click(); }', 'objectId': object_id}, session_id=self._session_id)
                    await asyncio.sleep(0.1)
                    return
                except Exception as js_e:
                    raise Exception(f'Failed to click element: {e}')
        except Exception as e:
            raise RuntimeError(f'Failed to click element: {e}')

    async def fill(self, value: str, clear: bool=True) -> None:
        try:
            cdp_client = self._client
            session_id = self._session_id
            backend_node_id = self._backend_node_id
            input_coordinates = None
            try:
                await cdp_client.send.DOM.scrollIntoViewIfNeeded(params={'backendNodeId': backend_node_id}, session_id=session_id)
                await asyncio.sleep(0.01)
            except Exception as e:
                logger.warning(f'Failed to scroll element into view: {e}')
            result = await cdp_client.send.DOM.resolveNode(params={'backendNodeId': backend_node_id}, session_id=session_id)
            if 'object' not in result or 'objectId' not in result['object']:
                raise RuntimeError('Failed to get object ID for element')
            object_id = result['object']['objectId']
            try:
                bounds_result = await cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': 'function() { return this.getBoundingClientRect(); }', 'objectId': object_id, 'returnByValue': True}, session_id=session_id)
                if bounds_result.get('result', {}).get('value'):
                    bounds = bounds_result['result']['value']
                    center_x = bounds['x'] + bounds['width'] / 2
                    center_y = bounds['y'] + bounds['height'] / 2
                    input_coordinates = {'input_x': center_x, 'input_y': center_y}
                    logger.debug(f'Using element coordinates: x={center_x:.1f}, y={center_y:.1f}')
            except Exception as e:
                logger.debug(f'Could not get element coordinates: {e}')
            if session_id is None:
                raise RuntimeError('Session ID is required for fill operation')
            focused_successfully = await self._focus_element_simple(backend_node_id=backend_node_id, object_id=object_id, cdp_client=cdp_client, session_id=session_id, input_coordinates=input_coordinates)
            if clear:
                cleared_successfully = await self._clear_text_field(object_id=object_id, cdp_client=cdp_client, session_id=session_id)
                if not cleared_successfully:
                    logger.warning('Text field clearing failed, typing may append to existing text')
            logger.debug(f'Typing text character by character: "{value}"')
            for i, char in enumerate(value):
                if char == '\n':
                    await cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyDown', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13}, session_id=session_id)
                    await asyncio.sleep(0.001)
                    await cdp_client.send.Input.dispatchKeyEvent(params={'type': 'char', 'text': '\r', 'key': 'Enter'}, session_id=session_id)
                    await cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13}, session_id=session_id)
                else:
                    modifiers, vk_code, base_key = self._get_char_modifiers_and_vk(char)
                    key_code = self._get_key_code_for_char(base_key)
                    await cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyDown', 'key': base_key, 'code': key_code, 'modifiers': modifiers, 'windowsVirtualKeyCode': vk_code}, session_id=session_id)
                    await asyncio.sleep(0.001)
                    await cdp_client.send.Input.dispatchKeyEvent(params={'type': 'char', 'text': char, 'key': char}, session_id=session_id)
                    await cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'key': base_key, 'code': key_code, 'modifiers': modifiers, 'windowsVirtualKeyCode': vk_code}, session_id=session_id)
                await asyncio.sleep(0.018)
        except Exception as e:
            raise Exception(f'Failed to fill element: {str(e)}')

    async def hover(self) -> None:
        box = await self.get_bounding_box()
        if not box:
            raise RuntimeError('Element is not visible or has no bounding box')
        x = box['x'] + box['width'] / 2
        y = box['y'] + box['height'] / 2
        params: 'DispatchMouseEventParameters' = {'type': 'mouseMoved', 'x': x, 'y': y}
        await self._client.send.Input.dispatchMouseEvent(params, session_id=self._session_id)

    async def focus(self) -> None:
        node_id = await self._get_node_id()
        params: 'FocusParameters' = {'nodeId': node_id}
        await self._client.send.DOM.focus(params, session_id=self._session_id)

    async def check(self) -> None:
        await self.click()

    async def select_option(self, values: str | list[str]) -> None:
        if isinstance(values, str):
            values = [values]
        try:
            await self.focus()
        except Exception:
            logger.warning('Failed to focus element')
        node_id = await self._get_node_id()
        params: 'RequestChildNodesParameters' = {'nodeId': node_id, 'depth': 1}
        await self._client.send.DOM.requestChildNodes(params, session_id=self._session_id)
        describe_params: 'DescribeNodeParameters' = {'nodeId': node_id, 'depth': 1}
        describe_result = await self._client.send.DOM.describeNode(describe_params, session_id=self._session_id)
        select_node = describe_result['node']
        for child in select_node.get('children', []):
            if child.get('nodeName', '').lower() == 'option':
                attrs = child.get('attributes', [])
                option_attrs = {}
                for i in range(0, len(attrs), 2):
                    if i + 1 < len(attrs):
                        option_attrs[attrs[i]] = attrs[i + 1]
                option_value = option_attrs.get('value', '')
                option_text = child.get('nodeValue', '')
                should_select = option_value in values or option_text in values
                if should_select:
                    option_node_id = child.get('nodeId')
                    if option_node_id:
                        option_describe_params: 'DescribeNodeParameters' = {'nodeId': option_node_id}
                        option_backend_result = await self._client.send.DOM.describeNode(option_describe_params, session_id=self._session_id)
                        option_backend_id = option_backend_result['node']['backendNodeId']
                        option_element = Element(self._browser_session, option_backend_id, self._session_id)
                        await option_element.click()

    async def drag_to(self, target: Union['Element', Position], source_position: Position | None=None, target_position: Position | None=None) -> None:
        if source_position:
            source_x = source_position['x']
            source_y = source_position['y']
        else:
            source_box = await self.get_bounding_box()
            if not source_box:
                raise RuntimeError('Source element is not visible')
            source_x = source_box['x'] + source_box['width'] / 2
            source_y = source_box['y'] + source_box['height'] / 2
        if isinstance(target, dict) and 'x' in target and ('y' in target):
            target_x = target['x']
            target_y = target['y']
        elif target_position:
            target_box = await target.get_bounding_box()
            if not target_box:
                raise RuntimeError('Target element is not visible')
            target_x = target_box['x'] + target_position['x']
            target_y = target_box['y'] + target_position['y']
        else:
            target_box = await target.get_bounding_box()
            if not target_box:
                raise RuntimeError('Target element is not visible')
            target_x = target_box['x'] + target_box['width'] / 2
            target_y = target_box['y'] + target_box['height'] / 2
        await self._client.send.Input.dispatchMouseEvent({'type': 'mousePressed', 'x': source_x, 'y': source_y, 'button': 'left'}, session_id=self._session_id)
        await self._client.send.Input.dispatchMouseEvent({'type': 'mouseMoved', 'x': target_x, 'y': target_y}, session_id=self._session_id)
        await self._client.send.Input.dispatchMouseEvent({'type': 'mouseReleased', 'x': target_x, 'y': target_y, 'button': 'left'}, session_id=self._session_id)

    async def get_attribute(self, name: str) -> str | None:
        node_id = await self._get_node_id()
        params: 'GetAttributesParameters' = {'nodeId': node_id}
        result = await self._client.send.DOM.getAttributes(params, session_id=self._session_id)
        attributes = result['attributes']
        for i in range(0, len(attributes), 2):
            if attributes[i] == name:
                return attributes[i + 1]
        return None

    async def get_bounding_box(self) -> BoundingBox | None:
        try:
            node_id = await self._get_node_id()
            params: 'GetBoxModelParameters' = {'nodeId': node_id}
            result = await self._client.send.DOM.getBoxModel(params, session_id=self._session_id)
            if 'model' not in result:
                return None
            content = result['model']['content']
            if len(content) < 8:
                return None
            x_coords = [content[i] for i in range(0, 8, 2)]
            y_coords = [content[i] for i in range(1, 8, 2)]
            x = min(x_coords)
            y = min(y_coords)
            width = max(x_coords) - x
            height = max(y_coords) - y
            return BoundingBox(x=x, y=y, width=width, height=height)
        except Exception:
            return None

    async def screenshot(self, format: str='png', quality: int | None=None) -> str:
        box = await self.get_bounding_box()
        if not box:
            raise RuntimeError('Element is not visible or has no bounding box')
        viewport: 'Viewport' = {'x': box['x'], 'y': box['y'], 'width': box['width'], 'height': box['height'], 'scale': 1.0}
        params: 'CaptureScreenshotParameters' = {'format': format, 'clip': viewport}
        if quality is not None and format.lower() == 'jpeg':
            params['quality'] = quality
        result = await self._client.send.Page.captureScreenshot(params, session_id=self._session_id)
        return result['data']

    async def evaluate(self, page_function: str, *args) -> str:
        object_id = await self._get_remote_object_id()
        if not object_id:
            raise RuntimeError('Element has no remote object ID (element may be detached from DOM)')
        page_function = page_function.strip()
        if not ('=>' in page_function and (page_function.startswith('(') or page_function.startswith('async'))):
            raise ValueError(f'JavaScript code must start with (...args) => or async (...args) => format. Got: {page_function[:50]}...')
        import re
        is_async = page_function.strip().startswith('async')
        async_prefix = 'async ' if is_async else ''
        func_to_parse = page_function.strip()
        if is_async:
            func_to_parse = func_to_parse[5:].strip()
        arrow_match = re.match('\\s*\\(([^)]*)\\)\\s*=>\\s*(.+)', func_to_parse, re.DOTALL)
        if not arrow_match:
            raise ValueError(f'Could not parse arrow function: {page_function[:50]}...')
        params_str = arrow_match.group(1).strip()
        body = arrow_match.group(2).strip()
        if not body.startswith('{'):
            function_declaration = f'{async_prefix}function({params_str}) {{ return {body}; }}'
        else:
            function_declaration = f'{async_prefix}function({params_str}) {body}'
        call_arguments = []
        if args:
            from cdp_use.cdp.runtime.types import CallArgument
            for arg in args:
                call_arguments.append(CallArgument(value=arg))
        params: 'CallFunctionOnParameters' = {'functionDeclaration': function_declaration, 'objectId': object_id, 'returnByValue': True, 'awaitPromise': True}
        if call_arguments:
            params['arguments'] = call_arguments
        result = await self._client.send.Runtime.callFunctionOn(params, session_id=self._session_id)
        if 'exceptionDetails' in result:
            raise RuntimeError(f"JavaScript evaluation failed: {result['exceptionDetails']}")
        value = result.get('result', {}).get('value')
        if value is None:
            return ''
        elif isinstance(value, str):
            return value
        else:
            import json
            try:
                return json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            except (TypeError, ValueError):
                return str(value)

    def _get_char_modifiers_and_vk(self, char: str) -> tuple[int, int, str]:
        shift_chars = {'!': ('1', 49), '@': ('2', 50), '#': ('3', 51), '$': ('4', 52), '%': ('5', 53), '^': ('6', 54), '&': ('7', 55), '*': ('8', 56), '(': ('9', 57), ')': ('0', 48), '_': ('-', 189), '+': ('=', 187), '{': ('[', 219), '}': (']', 221), '|': ('\\', 220), ':': (';', 186), '"': ("'", 222), '<': (',', 188), '>': ('.', 190), '?': ('/', 191), '~': ('`', 192)}
        if char in shift_chars:
            base_key, vk_code = shift_chars[char]
            return (8, vk_code, base_key)
        if char.isupper():
            return (8, ord(char), char.lower())
        if char.islower():
            return (0, ord(char.upper()), char)
        if char.isdigit():
            return (0, ord(char), char)
        no_shift_chars = {' ': 32, '-': 189, '=': 187, '[': 219, ']': 221, '\\': 220, ';': 186, "'": 222, ',': 188, '.': 190, '/': 191, '`': 192}
        if char in no_shift_chars:
            return (0, no_shift_chars[char], char)
        return (0, ord(char.upper()) if char.isalpha() else ord(char), char)

    def _get_key_code_for_char(self, char: str) -> str:
        key_codes = {' ': 'Space', '.': 'Period', ',': 'Comma', '-': 'Minus', '_': 'Minus', '@': 'Digit2', '!': 'Digit1', '?': 'Slash', ':': 'Semicolon', ';': 'Semicolon', '(': 'Digit9', ')': 'Digit0', '[': 'BracketLeft', ']': 'BracketRight', '{': 'BracketLeft', '}': 'BracketRight', '/': 'Slash', '\\': 'Backslash', '=': 'Equal', '+': 'Equal', '*': 'Digit8', '&': 'Digit7', '%': 'Digit5', '$': 'Digit4', '#': 'Digit3', '^': 'Digit6', '~': 'Backquote', '`': 'Backquote', '"': 'Quote', "'": 'Quote', '<': 'Comma', '>': 'Period', '|': 'Backslash'}
        if char in key_codes:
            return key_codes[char]
        elif char.isalpha():
            return f'Key{char.upper()}'
        elif char.isdigit():
            return f'Digit{char}'
        else:
            return f'Key{char.upper()}' if char.isascii() and char.isalpha() else 'Unidentified'

    async def _clear_text_field(self, object_id: str, cdp_client, session_id: str) -> bool:
        try:
            logger.debug('Clearing text field using JavaScript value setting')
            await cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': '\n\t\t\t\t\t\tfunction() {\n\t\t\t\t\t\t\t// Try to select all text first (only works on text-like inputs)\n\t\t\t\t\t\t\t// This handles cases where cursor is in the middle of text\n\t\t\t\t\t\t\ttry {\n\t\t\t\t\t\t\t\tthis.select();\n\t\t\t\t\t\t\t} catch (e) {\n\t\t\t\t\t\t\t\t// Some input types (date, color, number, etc.) don\'t support select()\n\t\t\t\t\t\t\t\t// That\'s fine, we\'ll just clear the value directly\n\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t// Set value to empty\n\t\t\t\t\t\t\tthis.value = "";\n\t\t\t\t\t\t\t// Dispatch events to notify frameworks like React\n\t\t\t\t\t\t\tthis.dispatchEvent(new Event("input", { bubbles: true }));\n\t\t\t\t\t\t\tthis.dispatchEvent(new Event("change", { bubbles: true }));\n\t\t\t\t\t\t\treturn this.value;\n\t\t\t\t\t\t}\n\t\t\t\t\t', 'objectId': object_id, 'returnByValue': True}, session_id=session_id)
            verify_result = await cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': 'function() { return this.value; }', 'objectId': object_id, 'returnByValue': True}, session_id=session_id)
            current_value = verify_result.get('result', {}).get('value', '')
            if not current_value:
                logger.debug('Text field cleared successfully using JavaScript')
                return True
            else:
                logger.debug(f'JavaScript clear partially failed, field still contains: "{current_value}"')
        except Exception as e:
            logger.debug(f'JavaScript clear failed: {e}')
        try:
            logger.debug('Fallback: Clearing using triple-click + Delete')
            bounds_result = await cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': 'function() { return this.getBoundingClientRect(); }', 'objectId': object_id, 'returnByValue': True}, session_id=session_id)
            if bounds_result.get('result', {}).get('value'):
                bounds = bounds_result['result']['value']
                center_x = bounds['x'] + bounds['width'] / 2
                center_y = bounds['y'] + bounds['height'] / 2
                await cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mousePressed', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 3}, session_id=session_id)
                await cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mouseReleased', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 3}, session_id=session_id)
                await cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyDown', 'key': 'Delete', 'code': 'Delete'}, session_id=session_id)
                await cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'key': 'Delete', 'code': 'Delete'}, session_id=session_id)
                logger.debug('Text field cleared using triple-click + Delete')
                return True
        except Exception as e:
            logger.debug(f'Triple-click clear failed: {e}')
        logger.warning('All text clearing strategies failed')
        return False

    async def _focus_element_simple(self, backend_node_id: int, object_id: str, cdp_client, session_id: str, input_coordinates=None) -> bool:
        try:
            logger.debug('Focusing element using CDP focus')
            await cdp_client.send.DOM.focus(params={'backendNodeId': backend_node_id}, session_id=session_id)
            logger.debug('Element focused successfully using CDP focus')
            return True
        except Exception as e:
            logger.debug(f'CDP focus failed: {e}, trying JavaScript focus')
        try:
            logger.debug('Focusing element using JavaScript focus')
            await cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': 'function() { this.focus(); }', 'objectId': object_id}, session_id=session_id)
            logger.debug('Element focused successfully using JavaScript')
            return True
        except Exception as e:
            logger.debug(f'JavaScript focus failed: {e}, trying click focus')
        try:
            if input_coordinates:
                logger.debug(f'Focusing element by clicking at coordinates: {input_coordinates}')
                center_x = input_coordinates['input_x']
                center_y = input_coordinates['input_y']
                await cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mousePressed', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 1}, session_id=session_id)
                await cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mouseReleased', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 1}, session_id=session_id)
                logger.debug('Element focused using click')
                return True
            else:
                logger.debug('No coordinates available for click focus')
        except Exception as e:
            logger.warning(f'All focus strategies failed: {e}')
        return False

    async def get_basic_info(self) -> ElementInfo:
        try:
            node_id = await self._get_node_id()
            describe_result = await self._client.send.DOM.describeNode({'nodeId': node_id}, session_id=self._session_id)
            node_info = describe_result['node']
            bounding_box = await self.get_bounding_box()
            attributes_list = node_info.get('attributes', [])
            attributes_dict: dict[str, str] = {}
            for i in range(0, len(attributes_list), 2):
                if i + 1 < len(attributes_list):
                    attributes_dict[attributes_list[i]] = attributes_list[i + 1]
            return ElementInfo(backendNodeId=self._backend_node_id, nodeId=node_id, nodeName=node_info.get('nodeName', ''), nodeType=node_info.get('nodeType', 0), nodeValue=node_info.get('nodeValue'), attributes=attributes_dict, boundingBox=bounding_box, error=None)
        except Exception as e:
            return ElementInfo(backendNodeId=self._backend_node_id, nodeId=None, nodeName='', nodeType=0, nodeValue=None, attributes={}, boundingBox=None, error=str(e))