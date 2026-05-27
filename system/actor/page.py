from typing import TYPE_CHECKING, TypeVar
from pydantic import BaseModel
from system import logger
from system.actor.utils import get_key_info
from system.dom.serializer.serializer import DOMTreeSerializer
from system.dom.service import DomService
from system.llm.messages import SystemMessage, UserMessage
T = TypeVar('T', bound=BaseModel)
if TYPE_CHECKING:
    from cdp_use.cdp.dom.commands import DescribeNodeParameters, QuerySelectorAllParameters
    from cdp_use.cdp.emulation.commands import SetDeviceMetricsOverrideParameters
    from cdp_use.cdp.input.commands import DispatchKeyEventParameters
    from cdp_use.cdp.page.commands import CaptureScreenshotParameters, NavigateParameters, NavigateToHistoryEntryParameters
    from cdp_use.cdp.runtime.commands import EvaluateParameters
    from cdp_use.cdp.target.commands import AttachToTargetParameters, GetTargetInfoParameters
    from cdp_use.cdp.target.types import TargetInfo
    from system.browser.session import BrowserSession
    from system.llm.base import BaseChatModel
    from .element import Element
    from .mouse import Mouse

class Page:

    def __init__(self, browser_session: 'BrowserSession', target_id: str, session_id: str | None=None, llm: 'BaseChatModel | None'=None):
        self._browser_session = browser_session
        self._client = browser_session.cdp_client
        self._target_id = target_id
        self._session_id: str | None = session_id
        self._mouse: 'Mouse | None' = None
        self._llm = llm

    async def _ensure_session(self) -> str:
        if not self._session_id:
            params: 'AttachToTargetParameters' = {'targetId': self._target_id, 'flatten': True}
            result = await self._client.send.Target.attachToTarget(params)
            self._session_id = result['sessionId']
            import asyncio
            await asyncio.gather(self._client.send.Page.enable(session_id=self._session_id), self._client.send.DOM.enable(session_id=self._session_id), self._client.send.Runtime.enable(session_id=self._session_id), self._client.send.Network.enable(session_id=self._session_id))
        return self._session_id

    @property
    async def session_id(self) -> str:
        return await self._ensure_session()

    @property
    async def mouse(self) -> 'Mouse':
        if not self._mouse:
            session_id = await self._ensure_session()
            from .mouse import Mouse
            self._mouse = Mouse(self._browser_session, session_id, self._target_id)
        return self._mouse

    async def reload(self) -> None:
        session_id = await self._ensure_session()
        await self._client.send.Page.reload(session_id=session_id)

    async def get_element(self, backend_node_id: int) -> 'Element':
        session_id = await self._ensure_session()
        from .element import Element as Element_
        return Element_(self._browser_session, backend_node_id, session_id)

    async def evaluate(self, page_function: str, *args) -> str:
        session_id = await self._ensure_session()
        page_function = self._fix_javascript_string(page_function)
        if not (page_function.startswith('(') and '=>' in page_function):
            raise ValueError(f'JavaScript code must start with (...args) => format. Got: {page_function[:50]}...')
        if args:
            import json
            arg_strs = [json.dumps(arg) for arg in args]
            expression = f"({page_function})({', '.join(arg_strs)})"
        else:
            expression = f'({page_function})()'
        logger.debug(f'Evaluating JavaScript: {repr(expression)}')
        params: 'EvaluateParameters' = {'expression': expression, 'returnByValue': True, 'awaitPromise': True}
        result = await self._client.send.Runtime.evaluate(params, session_id=session_id)
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

    def _fix_javascript_string(self, js_code: str) -> str:
        js_code = js_code.strip()
        if js_code.startswith('"') and js_code.endswith('"') or (js_code.startswith("'") and js_code.endswith("'")):
            inner = js_code[1:-1]
            if inner.count('"') + inner.count("'") == 0 or '() =>' in inner:
                js_code = inner
        if '\\"' in js_code and js_code.count('\\"') > js_code.count('"'):
            js_code = js_code.replace('\\"', '"')
        if "\\'" in js_code and js_code.count("\\'") > js_code.count("'"):
            js_code = js_code.replace("\\'", "'")
        js_code = js_code.strip()
        if not js_code:
            raise ValueError('JavaScript code is empty after cleaning')
        return js_code

    async def screenshot(self, format: str='png', quality: int | None=None) -> str:
        session_id = await self._ensure_session()
        params: 'CaptureScreenshotParameters' = {'format': format}
        if quality is not None and format.lower() == 'jpeg':
            params['quality'] = quality
        result = await self._client.send.Page.captureScreenshot(params, session_id=session_id)
        return result['data']

    async def press(self, key: str) -> None:
        session_id = await self._ensure_session()
        if '+' in key:
            parts = key.split('+')
            modifiers = parts[:-1]
            main_key = parts[-1]
            modifier_value = 0
            modifier_map = {'Alt': 1, 'Control': 2, 'Meta': 4, 'Shift': 8}
            for mod in modifiers:
                modifier_value |= modifier_map.get(mod, 0)
            for mod in modifiers:
                code, vk_code = get_key_info(mod)
                params: 'DispatchKeyEventParameters' = {'type': 'keyDown', 'key': mod, 'code': code}
                if vk_code is not None:
                    params['windowsVirtualKeyCode'] = vk_code
                await self._client.send.Input.dispatchKeyEvent(params, session_id=session_id)
            main_code, main_vk_code = get_key_info(main_key)
            main_down_params: 'DispatchKeyEventParameters' = {'type': 'keyDown', 'key': main_key, 'code': main_code, 'modifiers': modifier_value}
            if main_vk_code is not None:
                main_down_params['windowsVirtualKeyCode'] = main_vk_code
            await self._client.send.Input.dispatchKeyEvent(main_down_params, session_id=session_id)
            main_up_params: 'DispatchKeyEventParameters' = {'type': 'keyUp', 'key': main_key, 'code': main_code, 'modifiers': modifier_value}
            if main_vk_code is not None:
                main_up_params['windowsVirtualKeyCode'] = main_vk_code
            await self._client.send.Input.dispatchKeyEvent(main_up_params, session_id=session_id)
            for mod in reversed(modifiers):
                code, vk_code = get_key_info(mod)
                release_params: 'DispatchKeyEventParameters' = {'type': 'keyUp', 'key': mod, 'code': code}
                if vk_code is not None:
                    release_params['windowsVirtualKeyCode'] = vk_code
                await self._client.send.Input.dispatchKeyEvent(release_params, session_id=session_id)
        else:
            code, vk_code = get_key_info(key)
            key_down_params: 'DispatchKeyEventParameters' = {'type': 'keyDown', 'key': key, 'code': code}
            if vk_code is not None:
                key_down_params['windowsVirtualKeyCode'] = vk_code
            await self._client.send.Input.dispatchKeyEvent(key_down_params, session_id=session_id)
            key_up_params: 'DispatchKeyEventParameters' = {'type': 'keyUp', 'key': key, 'code': code}
            if vk_code is not None:
                key_up_params['windowsVirtualKeyCode'] = vk_code
            await self._client.send.Input.dispatchKeyEvent(key_up_params, session_id=session_id)

    async def set_viewport_size(self, width: int, height: int) -> None:
        session_id = await self._ensure_session()
        params: 'SetDeviceMetricsOverrideParameters' = {'width': width, 'height': height, 'deviceScaleFactor': 1.0, 'mobile': False}
        await self._client.send.Emulation.setDeviceMetricsOverride(params, session_id=session_id)

    async def get_target_info(self) -> 'TargetInfo':
        params: 'GetTargetInfoParameters' = {'targetId': self._target_id}
        result = await self._client.send.Target.getTargetInfo(params)
        return result['targetInfo']

    async def get_url(self) -> str:
        info = await self.get_target_info()
        return info.get('url', '')

    async def get_title(self) -> str:
        info = await self.get_target_info()
        return info.get('title', '')

    async def goto(self, url: str) -> None:
        session_id = await self._ensure_session()
        params: 'NavigateParameters' = {'url': url}
        await self._client.send.Page.navigate(params, session_id=session_id)

    async def navigate(self, url: str) -> None:
        await self.goto(url)

    async def go_back(self) -> None:
        session_id = await self._ensure_session()
        try:
            history = await self._client.send.Page.getNavigationHistory(session_id=session_id)
            current_index = history['currentIndex']
            entries = history['entries']
            if current_index <= 0:
                raise RuntimeError('Cannot go back - no previous entry in history')
            previous_entry_id = entries[current_index - 1]['id']
            params: 'NavigateToHistoryEntryParameters' = {'entryId': previous_entry_id}
            await self._client.send.Page.navigateToHistoryEntry(params, session_id=session_id)
        except Exception as e:
            raise RuntimeError(f'Failed to navigate back: {e}')

    async def go_forward(self) -> None:
        session_id = await self._ensure_session()
        try:
            history = await self._client.send.Page.getNavigationHistory(session_id=session_id)
            current_index = history['currentIndex']
            entries = history['entries']
            if current_index >= len(entries) - 1:
                raise RuntimeError('Cannot go forward - no next entry in history')
            next_entry_id = entries[current_index + 1]['id']
            params: 'NavigateToHistoryEntryParameters' = {'entryId': next_entry_id}
            await self._client.send.Page.navigateToHistoryEntry(params, session_id=session_id)
        except Exception as e:
            raise RuntimeError(f'Failed to navigate forward: {e}')

    async def get_elements_by_css_selector(self, selector: str) -> list['Element']:
        session_id = await self._ensure_session()
        doc_result = await self._client.send.DOM.getDocument(session_id=session_id)
        document_node_id = doc_result['root']['nodeId']
        query_params: 'QuerySelectorAllParameters' = {'nodeId': document_node_id, 'selector': selector}
        result = await self._client.send.DOM.querySelectorAll(query_params, session_id=session_id)
        elements = []
        from .element import Element as Element_
        for node_id in result['nodeIds']:
            describe_params: 'DescribeNodeParameters' = {'nodeId': node_id}
            node_result = await self._client.send.DOM.describeNode(describe_params, session_id=session_id)
            backend_node_id = node_result['node']['backendNodeId']
            elements.append(Element_(self._browser_session, backend_node_id, session_id))
        return elements

    @property
    def dom_service(self) -> 'DomService':
        return DomService(self._browser_session)

    async def get_element_by_prompt(self, prompt: str, llm: 'BaseChatModel | None'=None) -> 'Element | None':
        await self._ensure_session()
        llm = llm or self._llm
        if not llm:
            raise ValueError('LLM not provided')
        dom_service = self.dom_service
        enhanced_dom_tree, _ = await dom_service.get_dom_tree(target_id=self._target_id, all_frames=None)
        session_id = self._browser_session.id
        serialized_dom_state, _ = DOMTreeSerializer(enhanced_dom_tree, None, paint_order_filtering=True, session_id=session_id).serialize_accessible_elements()
        llm_representation = serialized_dom_state.llm_representation()
        system_message = SystemMessage(content="You are an AI created to find an element on a page by a prompt.\n\n<browser_state>\nInteractive Elements: All interactive elements will be provided in format as [index]<type>text</type> where\n- index: Numeric identifier for interaction\n- type: HTML element type (button, input, etc.)\n- text: Element description\n\nExamples:\n[33]<div>User form</div>\n[35]<button aria-label='Submit form'>Submit</button>\n\nNote that:\n- Only elements with numeric indexes in [] are interactive\n- (stacked) indentation (with \t) is important and means that the element is a (html) child of the element above (with a lower index)\n- Pure text elements without [] are not interactive.\n</browser_state>\n\nYour task is to find an element index (if any) that matches the prompt (written in <prompt> tag).\n\nIf non of the elements matches the, return None.\n\nBefore you return the element index, reason about the state and elements for a sentence or two.")
        state_message = UserMessage(content=f'\n\t\t\t<browser_state>\n\t\t\t{llm_representation}\n\t\t\t</browser_state>\n\n\t\t\t<prompt>\n\t\t\t{prompt}\n\t\t\t</prompt>\n\t\t\t')

        class ElementResponse(BaseModel):
            element_highlight_index: int | None
        llm_response = await llm.ainvoke([system_message, state_message], output_format=ElementResponse)
        element_highlight_index = llm_response.completion.element_highlight_index
        if element_highlight_index is None or element_highlight_index not in serialized_dom_state.selector_map:
            return None
        element = serialized_dom_state.selector_map[element_highlight_index]
        from .element import Element as Element_
        return Element_(self._browser_session, element.backend_node_id, self._session_id)

    async def must_get_element_by_prompt(self, prompt: str, llm: 'BaseChatModel | None'=None) -> 'Element':
        element = await self.get_element_by_prompt(prompt, llm)
        if element is None:
            raise ValueError(f'No element found for prompt: {prompt}')
        return element

    async def extract_content(self, prompt: str, structured_output: type[T], llm: 'BaseChatModel | None'=None) -> T:
        llm = llm or self._llm
        if not llm:
            raise ValueError('LLM not provided')
        try:
            content, content_stats = await self._extract_clean_markdown()
        except Exception as e:
            raise RuntimeError(f'Could not extract clean markdown: {type(e).__name__}')
        system_prompt = '\nYou are an expert at extracting structured data from the markdown of a webpage.\n\n<input>\nYou will be given a query and the markdown of a webpage that has been filtered to remove noise and advertising content.\n</input>\n\n<instructions>\n- You are tasked to extract information from the webpage that is relevant to the query.\n- You should ONLY use the information available in the webpage to answer the query. Do not make up information or provide guess from your own knowledge.\n- If the information relevant to the query is not available in the page, your response should mention that.\n- If the query asks for all items, products, etc., make sure to directly list all of them.\n- Return the extracted content in the exact structured format specified.\n</instructions>\n\n<output>\n- Your output should present ALL the information relevant to the query in the specified structured format.\n- Do not answer in conversational format - directly output the relevant information in the structured format.\n</output>\n'.strip()
        prompt_content = f'<query>\n{prompt}\n</query>\n\n<webpage_content>\n{content}\n</webpage_content>'
        import asyncio
        try:
            response = await asyncio.wait_for(llm.ainvoke([SystemMessage(content=system_prompt), UserMessage(content=prompt_content)], output_format=structured_output), timeout=120.0)
            return response.completion
        except Exception as e:
            raise RuntimeError(str(e))

    async def _extract_clean_markdown(self, extract_links: bool=False) -> tuple[str, dict]:
        from system.dom.markdown_extractor import extract_clean_markdown
        dom_service = self.dom_service
        return await extract_clean_markdown(dom_service=dom_service, target_id=self._target_id, extract_links=extract_links)