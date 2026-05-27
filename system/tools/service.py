import asyncio
import json
import logging
import os
from typing import Generic, TypeVar
import anyio
try:
    from lmnr import Laminar
except ImportError:
    Laminar = None
from pydantic import BaseModel
from system.agent.views import ActionModel, ActionResult
from system.browser import BrowserSession
from system.browser.events import ClickCoordinateEvent, ClickElementEvent, CloseTabEvent, GetDropdownOptionsEvent, GoBackEvent, NavigateToUrlEvent, ScrollEvent, ScrollToTextEvent, SendKeysEvent, SwitchTabEvent, TypeTextEvent, UploadFileEvent
from system.browser.views import BrowserError
from system.dom.service import EnhancedDOMTreeNode
from system.filesystem.file_system import FileSystem
from system.llm.base import BaseChatModel
from system.llm.messages import SystemMessage, UserMessage
from system.observability import observe_debug
from system.tools.registry.service import Registry
from system.tools.utils import get_click_description
from system.tools.views import ClickElementAction, ClickElementActionIndexOnly, CloseTabAction, DoneAction, ExtractAction, FindElementsAction, GetDropdownOptionsAction, InputTextAction, NavigateAction, NoParamsAction, ReadContentAction, SaveAsPdfAction, ScreenshotAction, ScrollAction, SearchAction, SearchPageAction, SelectDropdownOptionAction, SendKeysAction, StructuredOutputAction, SwitchTabAction, UploadFileAction
from system.utils import create_task_with_error_handling, sanitize_surrogates, time_execution_sync
logger = logging.getLogger(__name__)
ClickElementEvent.model_rebuild()
TypeTextEvent.model_rebuild()
ScrollEvent.model_rebuild()
UploadFileEvent.model_rebuild()
Context = TypeVar('Context')
T = TypeVar('T', bound=BaseModel)

def _detect_sensitive_key_name(text: str, sensitive_data: dict[str, str | dict[str, str]] | None) -> str | None:
    if not sensitive_data or not text:
        return None
    for domain_or_key, content in sensitive_data.items():
        if isinstance(content, dict):
            for key, value in content.items():
                if value and value == text:
                    return key
        elif content:
            if content == text:
                return domain_or_key
    return None

def handle_browser_error(e: BrowserError) -> ActionResult:
    if e.long_term_memory is not None:
        if e.short_term_memory is not None:
            return ActionResult(extracted_content=e.short_term_memory, error=e.long_term_memory, include_extracted_content_only_once=True)
        else:
            return ActionResult(error=e.long_term_memory)
    logger.warning('⚠️ A BrowserError was raised without long_term_memory - always set long_term_memory when raising BrowserError to propagate right messages to LLM.')
    raise e
_SEARCH_PAGE_JS_BODY = "try {\n\tvar scope = CSS_SCOPE ? document.querySelector(CSS_SCOPE) : document.body;\n\tif (!scope) {\n\t\treturn {error: 'CSS scope selector not found: ' + CSS_SCOPE, matches: [], total: 0};\n\t}\n\tvar walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);\n\tvar fullText = '';\n\tvar nodeOffsets = [];\n\twhile (walker.nextNode()) {\n\t\tvar node = walker.currentNode;\n\t\tvar text = node.textContent;\n\t\tif (text && text.trim()) {\n\t\t\tnodeOffsets.push({offset: fullText.length, length: text.length, node: node});\n\t\t\tfullText += text;\n\t\t}\n\t}\n\tvar re;\n\ttry {\n\t\tvar flags = CASE_SENSITIVE ? 'g' : 'gi';\n\t\tif (IS_REGEX) {\n\t\t\tre = new RegExp(PATTERN, flags);\n\t\t} else {\n\t\t\tre = new RegExp(PATTERN.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'), flags);\n\t\t}\n\t} catch (e) {\n\t\treturn {error: 'Invalid regex pattern: ' + e.message, matches: [], total: 0};\n\t}\n\tvar matches = [];\n\tvar match;\n\tvar totalFound = 0;\n\twhile ((match = re.exec(fullText)) !== null) {\n\t\ttotalFound++;\n\t\tif (matches.length < MAX_RESULTS) {\n\t\t\tvar start = Math.max(0, match.index - CONTEXT_CHARS);\n\t\t\tvar end = Math.min(fullText.length, match.index + match[0].length + CONTEXT_CHARS);\n\t\t\tvar context = fullText.slice(start, end);\n\t\t\tvar elementPath = '';\n\t\t\tfor (var i = 0; i < nodeOffsets.length; i++) {\n\t\t\t\tvar no = nodeOffsets[i];\n\t\t\t\tif (no.offset <= match.index && no.offset + no.length > match.index) {\n\t\t\t\t\telementPath = _getPath(no.node.parentElement);\n\t\t\t\t\tbreak;\n\t\t\t\t}\n\t\t\t}\n\t\t\tmatches.push({\n\t\t\t\tmatch_text: match[0],\n\t\t\t\tcontext: (start > 0 ? '...' : '') + context + (end < fullText.length ? '...' : ''),\n\t\t\t\telement_path: elementPath,\n\t\t\t\tchar_position: match.index\n\t\t\t});\n\t\t}\n\t\tif (match[0].length === 0) re.lastIndex++;\n\t}\n\treturn {matches: matches, total: totalFound, has_more: totalFound > MAX_RESULTS};\n} catch (e) {\n\treturn {error: 'search_page error: ' + e.message, matches: [], total: 0};\n}\nfunction _getPath(el) {\n\tvar parts = [];\n\tvar current = el;\n\twhile (current && current !== document.body && current !== document) {\n\t\tvar desc = current.tagName ? current.tagName.toLowerCase() : '';\n\t\tif (!desc) break;\n\t\tif (current.id) desc += '#' + current.id;\n\t\telse if (current.className && typeof current.className === 'string') {\n\t\t\tvar classes = current.className.trim().split(/\\s+/).slice(0, 2).join('.');\n\t\t\tif (classes) desc += '.' + classes;\n\t\t}\n\t\tparts.unshift(desc);\n\t\tcurrent = current.parentElement;\n\t}\n\treturn parts.join(' > ');\n}\n"
_FIND_ELEMENTS_JS_BODY = "try {\n\tvar elements;\n\ttry {\n\t\telements = document.querySelectorAll(SELECTOR);\n\t} catch (e) {\n\t\treturn {error: 'Invalid CSS selector: ' + e.message, elements: [], total: 0};\n\t}\n\tvar total = elements.length;\n\tvar limit = Math.min(total, MAX_RESULTS);\n\tvar results = [];\n\tfor (var i = 0; i < limit; i++) {\n\t\tvar el = elements[i];\n\t\tvar item = {index: i, tag: el.tagName.toLowerCase()};\n\t\tif (INCLUDE_TEXT) {\n\t\t\tvar text = (el.textContent || '').trim();\n\t\t\titem.text = text.length > 300 ? text.slice(0, 300) + '...' : text;\n\t\t}\n\t\tif (ATTRIBUTES && ATTRIBUTES.length > 0) {\n\t\t\titem.attrs = {};\n\t\t\tfor (var j = 0; j < ATTRIBUTES.length; j++) {\n\t\t\t\tvar val = el.getAttribute(ATTRIBUTES[j]);\n\t\t\t\tif (val !== null) {\n\t\t\t\t\titem.attrs[ATTRIBUTES[j]] = val.length > 500 ? val.slice(0, 500) + '...' : val;\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t\titem.children_count = el.children.length;\n\t\tresults.push(item);\n\t}\n\treturn {elements: results, total: total, showing: limit};\n} catch (e) {\n\treturn {error: 'find_elements error: ' + e.message, elements: [], total: 0};\n}\n"

def _build_search_page_js(pattern: str, regex: bool, case_sensitive: bool, context_chars: int, css_scope: str | None, max_results: int) -> str:
    params_js = f'var PATTERN = {json.dumps(pattern)};\nvar IS_REGEX = {json.dumps(regex)};\nvar CASE_SENSITIVE = {json.dumps(case_sensitive)};\nvar CONTEXT_CHARS = {json.dumps(context_chars)};\nvar CSS_SCOPE = {json.dumps(css_scope)};\nvar MAX_RESULTS = {json.dumps(max_results)};\n'
    return '(function() {\n' + params_js + _SEARCH_PAGE_JS_BODY + '\n})()'

def _build_find_elements_js(selector: str, attributes: list[str] | None, max_results: int, include_text: bool) -> str:
    params_js = f'var SELECTOR = {json.dumps(selector)};\nvar ATTRIBUTES = {json.dumps(attributes)};\nvar MAX_RESULTS = {json.dumps(max_results)};\nvar INCLUDE_TEXT = {json.dumps(include_text)};\n'
    return '(function() {\n' + params_js + _FIND_ELEMENTS_JS_BODY + '\n})()'

def _format_search_results(data: dict, pattern: str) -> str:
    if not isinstance(data, dict):
        return f'search_page returned unexpected result: {data}'
    matches = data.get('matches', [])
    total = data.get('total', 0)
    has_more = data.get('has_more', False)
    if total == 0:
        return f'No matches found for "{pattern}" on page.'
    lines = [f'''Found {total} match{('es' if total != 1 else '')} for "{pattern}" on page:''']
    lines.append('')
    for i, m in enumerate(matches):
        context = m.get('context', '')
        path = m.get('element_path', '')
        loc = f' (in {path})' if path else ''
        lines.append(f'[{i + 1}] {context}{loc}')
    if has_more:
        lines.append(f'\n... showing {len(matches)} of {total} total matches. Increase max_results to see more.')
    return '\n'.join(lines)

def _format_find_results(data: dict, selector: str) -> str:
    if not isinstance(data, dict):
        return f'find_elements returned unexpected result: {data}'
    elements = data.get('elements', [])
    total = data.get('total', 0)
    showing = data.get('showing', 0)
    if total == 0:
        return f'No elements found matching "{selector}".'
    lines = [f'''Found {total} element{('s' if total != 1 else '')} matching "{selector}":''']
    lines.append('')
    for el in elements:
        idx = el.get('index', 0)
        tag = el.get('tag', '?')
        text = el.get('text', '')
        attrs = el.get('attrs', {})
        children = el.get('children_count', 0)
        parts = [f'[{idx}] <{tag}>']
        if text:
            display_text = ' '.join(text.split())
            if len(display_text) > 120:
                display_text = display_text[:120] + '...'
            parts.append(f'"{display_text}"')
        if attrs:
            attr_strs = [f'{k}="{v}"' for k, v in attrs.items()]
            parts.append('{' + ', '.join(attr_strs) + '}')
        parts.append(f'({children} children)')
        lines.append(' '.join(parts))
    if showing < total:
        lines.append(f'\nShowing {showing} of {total} total elements. Increase max_results to see more.')
    return '\n'.join(lines)

def _is_autocomplete_field(node: EnhancedDOMTreeNode) -> bool:
    attrs = node.attributes or {}
    if attrs.get('role') == 'combobox':
        return True
    aria_ac = attrs.get('aria-autocomplete', '')
    if aria_ac and aria_ac != 'none':
        return True
    if attrs.get('list'):
        return True
    haspopup = attrs.get('aria-haspopup', '')
    if haspopup and haspopup != 'false' and (attrs.get('aria-controls') or attrs.get('aria-owns')):
        return True
    return False

class Tools(Generic[Context]):

    def __init__(self, exclude_actions: list[str] | None=None, output_model: type[T] | None=None, display_files_in_done_text: bool=True):
        self.registry = Registry[Context](exclude_actions if exclude_actions is not None else [])
        self.display_files_in_done_text = display_files_in_done_text
        self._output_model: type[BaseModel] | None = output_model
        self._coordinate_clicking_enabled: bool = False
        'Register all default browser actions'
        self._register_done_action(output_model)

        @self.registry.action('', param_model=SearchAction, terminates_sequence=True)
        async def search(params: SearchAction, browser_session: BrowserSession):
            import urllib.parse
            encoded_query = urllib.parse.quote_plus(params.query)
            search_engines = {'duckduckgo': f'https://duckduckgo.com/?q={encoded_query}', 'google': f'https://www.google.com/search?q={encoded_query}&udm=14', 'bing': f'https://www.bing.com/search?q={encoded_query}'}
            if params.engine.lower() not in search_engines:
                return ActionResult(error=f'Unsupported search engine: {params.engine}. Options: duckduckgo, google, bing')
            search_url = search_engines[params.engine.lower()]
            use_new_tab = False
            try:
                event = browser_session.event_bus.dispatch(NavigateToUrlEvent(url=search_url, new_tab=use_new_tab))
                await event
                await event.event_result(raise_if_any=True, raise_if_none=False)
                memory = f"Searched {params.engine.title()} for '{params.query}'"
                msg = f'🔍  {memory}'
                logger.info(msg)
                return ActionResult(extracted_content=memory, long_term_memory=memory)
            except Exception as e:
                logger.error(f'Failed to search {params.engine}: {e}')
                return ActionResult(error=f'Failed to search {params.engine} for "{params.query}": {str(e)}')

        @self.registry.action('', param_model=NavigateAction, terminates_sequence=True)
        async def navigate(params: NavigateAction, browser_session: BrowserSession):
            try:
                event = browser_session.event_bus.dispatch(NavigateToUrlEvent(url=params.url, new_tab=params.new_tab))
                await event
                await event.event_result(raise_if_any=True, raise_if_none=False)
                if params.new_tab:
                    memory = f'Opened new tab with URL {params.url}'
                    msg = f'🔗  Opened new tab with url {params.url}'
                else:
                    memory = f'Navigated to {params.url}'
                    msg = f'🔗 {memory}'
                logger.info(msg)
                return ActionResult(extracted_content=msg, long_term_memory=memory)
            except Exception as e:
                error_msg = str(e)
                browser_session.logger.error(f'❌ Navigation failed: {error_msg}')
                if isinstance(e, RuntimeError) and 'CDP client not initialized' in error_msg:
                    browser_session.logger.error('❌ Browser connection failed - CDP client not properly initialized')
                    return ActionResult(error=f'Browser connection error: {error_msg}')
                elif any((err in error_msg for err in ['ERR_NAME_NOT_RESOLVED', 'ERR_INTERNET_DISCONNECTED', 'ERR_CONNECTION_REFUSED', 'ERR_TIMED_OUT', 'net::'])):
                    site_unavailable_msg = f'Navigation failed - site unavailable: {params.url}'
                    browser_session.logger.warning(f'⚠️ {site_unavailable_msg} - {error_msg}')
                    return ActionResult(error=site_unavailable_msg)
                else:
                    return ActionResult(error=f'Navigation failed: {str(e)}')

        @self.registry.action('Go back', param_model=NoParamsAction, terminates_sequence=True)
        async def go_back(_: NoParamsAction, browser_session: BrowserSession):
            try:
                event = browser_session.event_bus.dispatch(GoBackEvent())
                await event
                memory = 'Navigated back'
                msg = f'🔙  {memory}'
                logger.info(msg)
                return ActionResult(extracted_content=memory)
            except Exception as e:
                logger.error(f'Failed to dispatch GoBackEvent: {type(e).__name__}: {e}')
                error_msg = f'Failed to go back: {str(e)}'
                return ActionResult(error=error_msg)

        @self.registry.action('Wait for x seconds.')
        async def wait(seconds: int=3):
            actual_seconds = min(max(seconds - 1, 0), 30)
            memory = f'Waited for {seconds} seconds'
            logger.info(f"🕒 waited for {seconds} second{('' if seconds == 1 else 's')}")
            await asyncio.sleep(actual_seconds)
            return ActionResult(extracted_content=memory, long_term_memory=memory)

        def _convert_llm_coordinates_to_viewport(llm_x: int, llm_y: int, browser_session: BrowserSession) -> tuple[int, int]:
            if browser_session.llm_screenshot_size and browser_session._original_viewport_size:
                original_width, original_height = browser_session._original_viewport_size
                llm_width, llm_height = browser_session.llm_screenshot_size
                actual_x = int(llm_x / llm_width * original_width)
                actual_y = int(llm_y / llm_height * original_height)
                logger.info(f'🔄 Converting coordinates: LLM ({llm_x}, {llm_y}) @ {llm_width}x{llm_height} → Viewport ({actual_x}, {actual_y}) @ {original_width}x{original_height}')
                return (actual_x, actual_y)
            return (llm_x, llm_y)

        async def _detect_new_tab_opened(browser_session: BrowserSession, tabs_before: set[str]) -> str:
            try:
                await asyncio.sleep(0.05)
                tabs_after = await browser_session.get_tabs()
                new_tabs = [t for t in tabs_after if t.target_id not in tabs_before]
                if new_tabs:
                    new_tab_id = new_tabs[0].target_id[-4:]
                    return f'. Note: This opened a new tab (tab_id: {new_tab_id}) - switch to it if you need to interact with the new page.'
            except Exception:
                pass
            return ''

        async def _click_by_coordinate(params: ClickElementAction, browser_session: BrowserSession) -> ActionResult:
            if params.coordinate_x is None or params.coordinate_y is None:
                return ActionResult(error='Both coordinate_x and coordinate_y must be provided')
            try:
                actual_x, actual_y = _convert_llm_coordinates_to_viewport(params.coordinate_x, params.coordinate_y, browser_session)
                tabs_before = {t.target_id for t in await browser_session.get_tabs()}
                asyncio.create_task(browser_session.highlight_coordinate_click(actual_x, actual_y))
                event = browser_session.event_bus.dispatch(ClickCoordinateEvent(coordinate_x=actual_x, coordinate_y=actual_y, force=True))
                await event
                click_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
                if isinstance(click_metadata, dict) and 'validation_error' in click_metadata:
                    error_msg = click_metadata['validation_error']
                    return ActionResult(error=error_msg)
                memory = f'Clicked on coordinate {params.coordinate_x}, {params.coordinate_y}'
                memory += await _detect_new_tab_opened(browser_session, tabs_before)
                logger.info(f'🖱️ {memory}')
                return ActionResult(extracted_content=memory, metadata={'click_x': actual_x, 'click_y': actual_y})
            except BrowserError as e:
                return handle_browser_error(e)
            except Exception as e:
                error_msg = f'Failed to click at coordinates ({params.coordinate_x}, {params.coordinate_y}).'
                return ActionResult(error=error_msg)

        async def _click_by_index(params: ClickElementAction | ClickElementActionIndexOnly, browser_session: BrowserSession) -> ActionResult:
            assert params.index is not None
            try:
                assert params.index != 0, 'Cannot click on element with index 0. If there are no interactive elements use wait(), refresh(), etc. to troubleshoot'
                node = await browser_session.get_element_by_index(params.index)
                if node is None:
                    msg = f'Element index {params.index} not available - page may have changed. Try refreshing browser state.'
                    logger.warning(f'⚠️ {msg}')
                    return ActionResult(extracted_content=msg)
                element_desc = get_click_description(node)
                tabs_before = {t.target_id for t in await browser_session.get_tabs()}
                create_task_with_error_handling(browser_session.highlight_interaction_element(node), name='highlight_click_element', suppress_exceptions=True)
                event = browser_session.event_bus.dispatch(ClickElementEvent(node=node))
                await event
                click_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
                if isinstance(click_metadata, dict) and 'validation_error' in click_metadata:
                    error_msg = click_metadata['validation_error']
                    if 'Cannot click on <select> elements.' in error_msg:
                        try:
                            return await dropdown_options(params=GetDropdownOptionsAction(index=params.index), browser_session=browser_session)
                        except Exception as dropdown_error:
                            logger.debug(f'Failed to get dropdown options as shortcut during click on dropdown: {type(dropdown_error).__name__}: {dropdown_error}')
                    return ActionResult(error=error_msg)
                memory = f'Clicked {element_desc}'
                memory += await _detect_new_tab_opened(browser_session, tabs_before)
                logger.info(f'🖱️ {memory}')
                return ActionResult(extracted_content=memory, metadata=click_metadata if isinstance(click_metadata, dict) else None)
            except BrowserError as e:
                return handle_browser_error(e)
            except Exception as e:
                error_msg = f'Failed to click element {params.index}: {str(e)}'
                return ActionResult(error=error_msg)
        self._click_by_index = _click_by_index
        self._click_by_coordinate = _click_by_coordinate
        self._register_click_action()

        @self.registry.action('Input text into element by index.', param_model=InputTextAction)
        async def input(params: InputTextAction, browser_session: BrowserSession, has_sensitive_data: bool=False, sensitive_data: dict[str, str | dict[str, str]] | None=None):
            node = await browser_session.get_element_by_index(params.index)
            if node is None:
                msg = f'Element index {params.index} not available - page may have changed. Try refreshing browser state.'
                logger.warning(f'⚠️ {msg}')
                return ActionResult(extracted_content=msg)
            create_task_with_error_handling(browser_session.highlight_interaction_element(node), name='highlight_type_element', suppress_exceptions=True)
            try:
                sensitive_key_name = None
                if has_sensitive_data and sensitive_data:
                    sensitive_key_name = _detect_sensitive_key_name(params.text, sensitive_data)
                event = browser_session.event_bus.dispatch(TypeTextEvent(node=node, text=params.text, clear=params.clear, is_sensitive=has_sensitive_data, sensitive_key_name=sensitive_key_name))
                await event
                input_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
                if has_sensitive_data:
                    if sensitive_key_name:
                        msg = f'Typed {sensitive_key_name}'
                        log_msg = f'Typed <{sensitive_key_name}>'
                    else:
                        msg = 'Typed sensitive data'
                        log_msg = 'Typed <sensitive>'
                else:
                    msg = f"Typed '{params.text}'"
                    log_msg = f"Typed '{params.text}'"
                logger.debug(log_msg)
                actual_value = None
                if isinstance(input_metadata, dict):
                    actual_value = input_metadata.pop('actual_value', None)
                if not has_sensitive_data and actual_value is not None and (actual_value != params.text):
                    msg += f"\n⚠️ Note: the field's actual value '{actual_value}' differs from typed text '{params.text}'. The page may have reformatted or autocompleted your input."
                if _is_autocomplete_field(node):
                    msg += '\n💡 This is an autocomplete field. Wait for suggestions to appear, then click the correct suggestion instead of pressing Enter.'
                    attrs = node.attributes or {}
                    if attrs.get('role') == 'combobox' or attrs.get('aria-autocomplete', '') not in ('', 'none'):
                        await asyncio.sleep(0.4)
                return ActionResult(extracted_content=msg, long_term_memory=msg, metadata=input_metadata if isinstance(input_metadata, dict) else None)
            except BrowserError as e:
                return handle_browser_error(e)
            except Exception as e:
                logger.error(f'Failed to dispatch TypeTextEvent: {type(e).__name__}: {e}')
                error_msg = f'Failed to type text into element {params.index}: {e}'
                return ActionResult(error=error_msg)

        @self.registry.action('', param_model=UploadFileAction)
        async def upload_file(params: UploadFileAction, browser_session: BrowserSession, available_file_paths: list[str], file_system: FileSystem):
            if params.path not in available_file_paths:
                downloaded_files = browser_session.downloaded_files
                if params.path not in downloaded_files:
                    if file_system and file_system.get_dir():
                        file_obj = file_system.get_file(params.path)
                        if file_obj:
                            file_system_path = str(file_system.get_dir() / params.path)
                            params = UploadFileAction(index=params.index, path=file_system_path)
                        elif not browser_session.is_local:
                            pass
                        else:
                            msg = f'File path {params.path} is not available. To fix: The user must add this file path to the available_file_paths parameter when creating the Agent. Example: Agent(task="...", llm=llm, browser=browser, available_file_paths=["{params.path}"])'
                            logger.error(f'❌ {msg}')
                            return ActionResult(error=msg)
                    elif not browser_session.is_local:
                        pass
                    else:
                        msg = f'File path {params.path} is not available. To fix: The user must add this file path to the available_file_paths parameter when creating the Agent. Example: Agent(task="...", llm=llm, browser=browser, available_file_paths=["{params.path}"])'
                        raise BrowserError(message=msg, long_term_memory=msg)
            if browser_session.is_local:
                if not os.path.exists(params.path):
                    msg = f'File {params.path} does not exist'
                    return ActionResult(error=msg)
                file_size = os.path.getsize(params.path)
                if file_size == 0:
                    msg = f'File {params.path} is empty (0 bytes). The file may not have been saved correctly.'
                    return ActionResult(error=msg)
            selector_map = await browser_session.get_selector_map()
            if params.index not in selector_map:
                msg = f'Element with index {params.index} does not exist.'
                return ActionResult(error=msg)
            node = selector_map[params.index]

            def find_file_input_near_element(node: EnhancedDOMTreeNode, max_height: int=3, max_descendant_depth: int=3) -> EnhancedDOMTreeNode | None:

                def find_file_input_in_descendants(n: EnhancedDOMTreeNode, depth: int) -> EnhancedDOMTreeNode | None:
                    if depth < 0:
                        return None
                    if browser_session.is_file_input(n):
                        return n
                    for child in n.children_nodes or []:
                        result = find_file_input_in_descendants(child, depth - 1)
                        if result:
                            return result
                    return None
                current = node
                for _ in range(max_height + 1):
                    if browser_session.is_file_input(current):
                        return current
                    result = find_file_input_in_descendants(current, max_descendant_depth)
                    if result:
                        return result
                    if current.parent_node:
                        for sibling in current.parent_node.children_nodes or []:
                            if sibling is current:
                                continue
                            if browser_session.is_file_input(sibling):
                                return sibling
                            result = find_file_input_in_descendants(sibling, max_descendant_depth)
                            if result:
                                return result
                    current = current.parent_node
                    if not current:
                        break
                return None
            file_input_node = find_file_input_near_element(node)
            if file_input_node:
                create_task_with_error_handling(browser_session.highlight_interaction_element(file_input_node), name='highlight_file_input', suppress_exceptions=True)
            if file_input_node is None:
                logger.info(f'No file upload element found near index {params.index}, searching for closest file input to scroll position')
                cdp_session = await browser_session.get_or_create_cdp_session()
                try:
                    scroll_info = await cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': 'window.scrollY || window.pageYOffset || 0'}, session_id=cdp_session.session_id)
                    current_scroll_y = scroll_info.get('result', {}).get('value', 0)
                except Exception:
                    current_scroll_y = 0
                closest_file_input = None
                min_distance = float('inf')
                for idx, element in selector_map.items():
                    if browser_session.is_file_input(element):
                        if element.absolute_position:
                            element_y = element.absolute_position.y
                            distance = abs(element_y - current_scroll_y)
                            if distance < min_distance:
                                min_distance = distance
                                closest_file_input = element
                if closest_file_input:
                    file_input_node = closest_file_input
                    logger.info(f'Found file input closest to scroll position (distance: {min_distance}px)')
                    create_task_with_error_handling(browser_session.highlight_interaction_element(file_input_node), name='highlight_file_input_fallback', suppress_exceptions=True)
                else:
                    msg = 'No file upload element found on the page'
                    logger.error(msg)
                    raise BrowserError(msg)
            try:
                event = browser_session.event_bus.dispatch(UploadFileEvent(node=file_input_node, file_path=params.path))
                await event
                await event.event_result(raise_if_any=True, raise_if_none=False)
                msg = f'Successfully uploaded file to index {params.index}'
                logger.info(f'📁 {msg}')
                return ActionResult(extracted_content=msg, long_term_memory=f'Uploaded file {params.path} to element {params.index}')
            except Exception as e:
                logger.error(f'Failed to upload file: {e}')
                raise BrowserError(f'Failed to upload file: {e}')

        @self.registry.action('Switch to another open tab by tab_id. Tab IDs are shown in browser state tabs list (last 4 chars of target_id). Use when you need to work with content in a different tab.', param_model=SwitchTabAction, terminates_sequence=True)
        async def switch(params: SwitchTabAction, browser_session: BrowserSession):
            try:
                target_id = await browser_session.get_target_id_from_tab_id(params.tab_id)
                event = browser_session.event_bus.dispatch(SwitchTabEvent(target_id=target_id))
                await event
                new_target_id = await event.event_result(raise_if_any=False, raise_if_none=False)
                if new_target_id:
                    memory = f'Switched to tab #{new_target_id[-4:]}'
                else:
                    memory = f'Switched to tab #{params.tab_id}'
                logger.info(f'🔄  {memory}')
                return ActionResult(extracted_content=memory, long_term_memory=memory)
            except Exception as e:
                logger.warning(f'Tab switch may have failed: {e}')
                memory = f'Attempted to switch to tab #{params.tab_id}'
                return ActionResult(extracted_content=memory, long_term_memory=memory)

        @self.registry.action('Close a tab by tab_id. Tab IDs are shown in browser state tabs list (last 4 chars of target_id). Use to clean up tabs you no longer need.', param_model=CloseTabAction)
        async def close(params: CloseTabAction, browser_session: BrowserSession):
            try:
                target_id = await browser_session.get_target_id_from_tab_id(params.tab_id)
                event = browser_session.event_bus.dispatch(CloseTabEvent(target_id=target_id))
                await event
                await event.event_result(raise_if_any=False, raise_if_none=False)
                memory = f'Closed tab #{params.tab_id}'
                logger.info(f'🗑️  {memory}')
                return ActionResult(extracted_content=memory, long_term_memory=memory)
            except Exception as e:
                logger.warning(f'Tab {params.tab_id} may already be closed: {e}')
                memory = f'Tab #{params.tab_id} closed (was already closed or invalid)'
                return ActionResult(extracted_content=memory, long_term_memory=memory)

        @self.registry.action("LLM extracts structured data from page markdown. Use when: on right page, know what to extract, haven't called before on same page+query. Can't get interactive elements. Set extract_links=True for URLs. Use start_from_char if previous extraction was truncated to extract data further down the page.", param_model=ExtractAction)
        async def extract(params: ExtractAction, browser_session: BrowserSession, page_extraction_llm: BaseChatModel, file_system: FileSystem, extraction_schema: dict | None=None):
            MAX_CHAR_LIMIT = 100000
            query = params['query'] if isinstance(params, dict) else params.query
            extract_links = params['extract_links'] if isinstance(params, dict) else params.extract_links
            start_from_char = params['start_from_char'] if isinstance(params, dict) else params.start_from_char
            output_schema: dict | None = params.get('output_schema') if isinstance(params, dict) else params.output_schema
            if output_schema is None and extraction_schema is not None:
                output_schema = extraction_schema
            structured_model: type[BaseModel] | None = None
            if output_schema is not None:
                try:
                    from system.tools.extraction.schema_utils import schema_dict_to_pydantic_model
                    structured_model = schema_dict_to_pydantic_model(output_schema)
                except (ValueError, TypeError) as exc:
                    logger.warning(f'Invalid output_schema, falling back to free-text extraction: {exc}')
                    output_schema = None
            try:
                from system.dom.markdown_extractor import extract_clean_markdown
                content, content_stats = await extract_clean_markdown(browser_session=browser_session, extract_links=extract_links)
            except Exception as e:
                raise RuntimeError(f'Could not extract clean markdown: {type(e).__name__}')
            final_filtered_length = content_stats['final_filtered_chars']
            from system.dom.markdown_extractor import chunk_markdown_by_structure
            chunks = chunk_markdown_by_structure(content, max_chunk_chars=MAX_CHAR_LIMIT, start_from_char=start_from_char)
            if not chunks:
                return ActionResult(error=f'start_from_char ({start_from_char}) exceeds content length {final_filtered_length} characters.')
            chunk = chunks[0]
            content = chunk.content
            truncated = chunk.has_more
            if chunk.overlap_prefix:
                content = chunk.overlap_prefix + '\n' + content
            if start_from_char > 0:
                content_stats['started_from_char'] = start_from_char
            if truncated:
                content_stats['truncated_at_char'] = chunk.char_offset_end
                content_stats['next_start_char'] = chunk.char_offset_end
                content_stats['chunk_index'] = chunk.chunk_index
                content_stats['total_chunks'] = chunk.total_chunks
            original_html_length = content_stats['original_html_chars']
            initial_markdown_length = content_stats['initial_markdown_chars']
            chars_filtered = content_stats['filtered_chars_removed']
            stats_summary = f'Content processed: {original_html_length:,} HTML chars → {initial_markdown_length:,} initial markdown → {final_filtered_length:,} filtered markdown'
            if start_from_char > 0:
                stats_summary += f' (started from char {start_from_char:,})'
            if truncated:
                chunk_info = f'chunk {chunk.chunk_index + 1} of {chunk.total_chunks}, '
                stats_summary += f" → {len(content):,} final chars ({chunk_info}use start_from_char={content_stats['next_start_char']} to continue)"
            elif chars_filtered > 0:
                stats_summary += f' (filtered {chars_filtered:,} chars of noise)'
            content = sanitize_surrogates(content)
            query = sanitize_surrogates(query)
            if structured_model is not None:
                assert output_schema is not None
                system_prompt = "\nYou are an expert at extracting structured data from the markdown of a webpage.\n\n<input>\nYou will be given a query, a JSON Schema, and the markdown of a webpage that has been filtered to remove noise and advertising content.\n</input>\n\n<instructions>\n- Extract ONLY information present in the webpage. Do not guess or fabricate values.\n- Your response MUST conform to the provided JSON Schema exactly.\n- If a required field's value cannot be found on the page, use null (if the schema allows it) or an empty string / empty array as appropriate.\n- If the content was truncated, extract what is available from the visible portion.\n</instructions>\n".strip()
                schema_json = json.dumps(output_schema, indent=2)
                prompt = f'<query>\n{query}\n</query>\n\n<output_schema>\n{schema_json}\n</output_schema>\n\n<content_stats>\n{stats_summary}\n</content_stats>\n\n<webpage_content>\n{content}\n</webpage_content>'
                try:
                    response = await asyncio.wait_for(page_extraction_llm.ainvoke([SystemMessage(content=system_prompt), UserMessage(content=prompt)], output_format=structured_model), timeout=120.0)
                    result_data: dict = response.completion.model_dump(mode='json')
                    result_json = json.dumps(result_data)
                    current_url = await browser_session.get_current_page_url()
                    extracted_content = f'<url>\n{current_url}\n</url>\n<query>\n{query}\n</query>\n<structured_result>\n{result_json}\n</structured_result>'
                    from system.tools.extraction.views import ExtractionResult
                    extraction_meta = ExtractionResult(data=result_data, schema_used=output_schema, is_partial=truncated, source_url=current_url, content_stats=content_stats)
                    MAX_MEMORY_LENGTH = 10000
                    if len(extracted_content) < MAX_MEMORY_LENGTH:
                        memory = extracted_content
                        include_extracted_content_only_once = False
                    else:
                        file_name = await file_system.save_extracted_content(extracted_content)
                        memory = f'Query: {query}\nContent in {file_name} and once in <read_state>.'
                        include_extracted_content_only_once = True
                    logger.info(f'📄 {memory}')
                    return ActionResult(extracted_content=extracted_content, include_extracted_content_only_once=include_extracted_content_only_once, long_term_memory=memory, metadata={'structured_extraction': True, 'extraction_result': extraction_meta.model_dump(mode='json')})
                except Exception as e:
                    logger.debug(f'Error in structured extraction: {e}')
                    raise RuntimeError(str(e))
            system_prompt = '\nYou are an expert at extracting data from the markdown of a webpage.\n\n<input>\nYou will be given a query and the markdown of a webpage that has been filtered to remove noise and advertising content.\n</input>\n\n<instructions>\n- You are tasked to extract information from the webpage that is relevant to the query.\n- You should ONLY use the information available in the webpage to answer the query. Do not make up information or provide guess from your own knowledge.\n- If the information relevant to the query is not available in the page, your response should mention that.\n- If the query asks for all items, products, etc., make sure to directly list all of them.\n- If the content was truncated and you need more information, note that the user can use start_from_char parameter to continue from where truncation occurred.\n</instructions>\n\n<output>\n- Your output should present ALL the information relevant to the query in a concise way.\n- Do not answer in conversational format - directly output the relevant information or that the information is unavailable.\n</output>\n'.strip()
            prompt = f'<query>\n{query}\n</query>\n\n<content_stats>\n{stats_summary}\n</content_stats>\n\n<webpage_content>\n{content}\n</webpage_content>'
            try:
                response = await asyncio.wait_for(page_extraction_llm.ainvoke([SystemMessage(content=system_prompt), UserMessage(content=prompt)]), timeout=120.0)
                current_url = await browser_session.get_current_page_url()
                extracted_content = f'<url>\n{current_url}\n</url>\n<query>\n{query}\n</query>\n<result>\n{response.completion}\n</result>'
                MAX_MEMORY_LENGTH = 10000
                if len(extracted_content) < MAX_MEMORY_LENGTH:
                    memory = extracted_content
                    include_extracted_content_only_once = False
                else:
                    file_name = await file_system.save_extracted_content(extracted_content)
                    memory = f'Query: {query}\nContent in {file_name} and once in <read_state>.'
                    include_extracted_content_only_once = True
                logger.info(f'📄 {memory}')
                return ActionResult(extracted_content=extracted_content, include_extracted_content_only_once=include_extracted_content_only_once, long_term_memory=memory)
            except Exception as e:
                logger.debug(f'Error extracting content: {e}')
                raise RuntimeError(str(e))

        @self.registry.action('Search page text for a pattern (like grep). Zero LLM cost, instant. Returns matches with surrounding context. Use to find specific text, verify content exists, or locate data on the page. Set regex=True for regex patterns. Use css_scope to search within a specific section.', param_model=SearchPageAction)
        async def search_page(params: SearchPageAction, browser_session: BrowserSession):
            js_code = _build_search_page_js(pattern=params.pattern, regex=params.regex, case_sensitive=params.case_sensitive, context_chars=params.context_chars, css_scope=params.css_scope, max_results=params.max_results)
            cdp_session = await browser_session.get_or_create_cdp_session()
            result = await cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': js_code, 'returnByValue': True, 'awaitPromise': True}, session_id=cdp_session.session_id)
            if result.get('exceptionDetails'):
                error_text = result['exceptionDetails'].get('text', 'Unknown JS error')
                return ActionResult(error=f'search_page failed: {error_text}')
            data = result.get('result', {}).get('value')
            if data is None:
                return ActionResult(error='search_page returned no result')
            if isinstance(data, dict) and data.get('error'):
                return ActionResult(error=f"search_page: {data['error']}")
            formatted = _format_search_results(data, params.pattern)
            total = data.get('total', 0)
            memory = f'''Searched page for "{params.pattern}": {total} match{('es' if total != 1 else '')} found.'''
            logger.info(f'🔎 {memory}')
            return ActionResult(extracted_content=formatted, long_term_memory=memory)

        @self.registry.action('Query DOM elements by CSS selector (like find). Zero LLM cost, instant. Returns matching elements with tag, text, and attributes. Use to explore page structure, count items, get links/attributes. Use attributes=["href","src"] to extract specific attributes.', param_model=FindElementsAction)
        async def find_elements(params: FindElementsAction, browser_session: BrowserSession):
            js_code = _build_find_elements_js(selector=params.selector, attributes=params.attributes, max_results=params.max_results, include_text=params.include_text)
            cdp_session = await browser_session.get_or_create_cdp_session()
            result = await cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': js_code, 'returnByValue': True, 'awaitPromise': True}, session_id=cdp_session.session_id)
            if result.get('exceptionDetails'):
                error_text = result['exceptionDetails'].get('text', 'Unknown JS error')
                return ActionResult(error=f'find_elements failed: {error_text}')
            data = result.get('result', {}).get('value')
            if data is None:
                return ActionResult(error='find_elements returned no result')
            if isinstance(data, dict) and data.get('error'):
                return ActionResult(error=f"find_elements: {data['error']}")
            formatted = _format_find_results(data, params.selector)
            total = data.get('total', 0)
            memory = f'''Found {total} element{('s' if total != 1 else '')} matching "{params.selector}".'''
            logger.info(f'🔍 {memory}')
            return ActionResult(extracted_content=formatted, long_term_memory=memory)

        @self.registry.action('Scroll by pages. REQUIRED: down=True/False (True=scroll down, False=scroll up, default=True). Optional: pages=0.5-10.0 (default 1.0). Use index for scroll elements (dropdowns/custom UI). High pages (10) reaches bottom. Multi-page scrolls sequentially. Viewport-based height, fallback 1000px/page.', param_model=ScrollAction)
        async def scroll(params: ScrollAction, browser_session: BrowserSession):
            try:
                node = None
                if params.index is not None and params.index != 0:
                    node = await browser_session.get_element_by_index(params.index)
                    if node is None:
                        msg = f'Element index {params.index} not found in browser state'
                        return ActionResult(error=msg)
                direction = 'down' if params.down else 'up'
                target = f'element {params.index}' if params.index is not None and params.index != 0 else ''
                try:
                    cdp_session = await browser_session.get_or_create_cdp_session()
                    metrics = await cdp_session.cdp_client.send.Page.getLayoutMetrics(session_id=cdp_session.session_id)
                    css_viewport = metrics.get('cssVisualViewport', {})
                    css_layout_viewport = metrics.get('cssLayoutViewport', {})
                    viewport_height = int(css_viewport.get('clientHeight') or css_layout_viewport.get('clientHeight', 1000))
                    logger.debug(f'Detected viewport height: {viewport_height}px')
                except Exception as e:
                    viewport_height = 1000
                    logger.debug(f'Failed to get viewport height, using fallback 1000px: {e}')
                if params.pages >= 1.0:
                    import asyncio
                    num_full_pages = int(params.pages)
                    remaining_fraction = params.pages - num_full_pages
                    completed_scrolls = 0
                    for i in range(num_full_pages):
                        try:
                            pixels = viewport_height
                            if not params.down:
                                pixels = -pixels
                            event = browser_session.event_bus.dispatch(ScrollEvent(direction=direction, amount=abs(pixels), node=node))
                            await event
                            await event.event_result(raise_if_any=True, raise_if_none=False)
                            completed_scrolls += 1
                            await asyncio.sleep(0.15)
                        except Exception as e:
                            logger.warning(f'Scroll {i + 1}/{num_full_pages} failed: {e}')
                    if remaining_fraction > 0:
                        try:
                            pixels = int(remaining_fraction * viewport_height)
                            if not params.down:
                                pixels = -pixels
                            event = browser_session.event_bus.dispatch(ScrollEvent(direction=direction, amount=abs(pixels), node=node))
                            await event
                            await event.event_result(raise_if_any=True, raise_if_none=False)
                            completed_scrolls += remaining_fraction
                        except Exception as e:
                            logger.warning(f'Fractional scroll failed: {e}')
                    if params.pages == 1.0:
                        long_term_memory = f'Scrolled {direction} {target} {viewport_height}px'.replace('  ', ' ')
                    else:
                        long_term_memory = f'Scrolled {direction} {target} {completed_scrolls:.1f} pages'.replace('  ', ' ')
                else:
                    pixels = int(params.pages * viewport_height)
                    event = browser_session.event_bus.dispatch(ScrollEvent(direction='down' if params.down else 'up', amount=pixels, node=node))
                    await event
                    await event.event_result(raise_if_any=True, raise_if_none=False)
                    long_term_memory = f'Scrolled {direction} {target} {params.pages} pages'.replace('  ', ' ')
                msg = f'🔍 {long_term_memory}'
                logger.info(msg)
                return ActionResult(extracted_content=msg, long_term_memory=long_term_memory)
            except Exception as e:
                logger.error(f'Failed to dispatch ScrollEvent: {type(e).__name__}: {e}')
                error_msg = 'Failed to execute scroll action.'
                return ActionResult(error=error_msg)

        @self.registry.action('', param_model=SendKeysAction)
        async def send_keys(params: SendKeysAction, browser_session: BrowserSession):
            try:
                event = browser_session.event_bus.dispatch(SendKeysEvent(keys=params.keys))
                await event
                await event.event_result(raise_if_any=True, raise_if_none=False)
                memory = f'Sent keys: {params.keys}'
                msg = f'⌨️  {memory}'
                logger.info(msg)
                return ActionResult(extracted_content=memory, long_term_memory=memory)
            except Exception as e:
                logger.error(f'Failed to dispatch SendKeysEvent: {type(e).__name__}: {e}')
                error_msg = f'Failed to send keys: {str(e)}'
                return ActionResult(error=error_msg)

        @self.registry.action('Scroll to text.')
        async def find_text(text: str, browser_session: BrowserSession):
            event = browser_session.event_bus.dispatch(ScrollToTextEvent(text=text))
            try:
                await event.event_result(raise_if_any=True, raise_if_none=False)
                memory = f'Scrolled to text: {text}'
                msg = f'🔍  {memory}'
                logger.info(msg)
                return ActionResult(extracted_content=memory, long_term_memory=memory)
            except Exception as e:
                msg = f"Text '{text}' not found or not visible on page"
                logger.info(msg)
                return ActionResult(extracted_content=msg, long_term_memory=f"Tried scrolling to text '{text}' but it was not found")

        @self.registry.action('Take a screenshot of the current viewport. If file_name is provided, saves to that file and returns the path. Otherwise, screenshot is included in the next browser_state observation.', param_model=ScreenshotAction)
        async def screenshot(params: ScreenshotAction, browser_session: BrowserSession, file_system: FileSystem):
            if params.file_name:
                file_name = params.file_name
                if not file_name.lower().endswith('.png'):
                    file_name = f'{file_name}.png'
                file_name = FileSystem.sanitize_filename(file_name)
                screenshot_bytes = await browser_session.take_screenshot(full_page=False)
                file_path = file_system.get_dir() / file_name
                file_path.write_bytes(screenshot_bytes)
                result = f'Screenshot saved to {file_name}'
                logger.info(f'📸 {result}. Full path: {file_path}')
                return ActionResult(extracted_content=result, long_term_memory=f'{result}. Full path: {file_path}', attachments=[str(file_path)])
            else:
                memory = 'Requested screenshot for next observation'
                logger.info(f'📸 {memory}')
                return ActionResult(extracted_content=memory, metadata={'include_screenshot': True})

        @self.registry.action('Save the current page as a PDF file. Returns the file path of the saved PDF. Use this to capture the full page content (including content below the fold) as a printable document.', param_model=SaveAsPdfAction)
        async def save_as_pdf(params: SaveAsPdfAction, browser_session: BrowserSession, file_system: FileSystem):
            import base64
            import re
            paper_sizes: dict[str, tuple[float, float]] = {'letter': (8.5, 11), 'legal': (8.5, 14), 'a4': (8.27, 11.69), 'a3': (11.69, 16.54), 'tabloid': (11, 17)}
            paper_key = params.paper_format.lower()
            if paper_key not in paper_sizes:
                paper_key = 'letter'
            paper_width, paper_height = paper_sizes[paper_key]
            cdp_session = await browser_session.get_or_create_cdp_session(focus=True)
            result = await asyncio.wait_for(cdp_session.cdp_client.send.Page.printToPDF(params={'printBackground': params.print_background, 'landscape': params.landscape, 'scale': params.scale, 'paperWidth': paper_width, 'paperHeight': paper_height, 'preferCSSPageSize': True}, session_id=cdp_session.session_id), timeout=30.0)
            pdf_data = result.get('data')
            assert pdf_data, 'CDP Page.printToPDF returned no data'
            pdf_bytes = base64.b64decode(pdf_data)
            if params.file_name:
                file_name = params.file_name
            else:
                try:
                    page_title = await asyncio.wait_for(browser_session.get_current_page_title(), timeout=2.0)
                    safe_title = re.sub('[^\\w\\s-]', '', page_title).strip()[:50]
                    file_name = safe_title if safe_title else 'page'
                except Exception:
                    file_name = 'page'
            if not file_name.lower().endswith('.pdf'):
                file_name = f'{file_name}.pdf'
            file_name = FileSystem.sanitize_filename(file_name)
            file_path = file_system.get_dir() / file_name
            if file_path.exists():
                base, ext = os.path.splitext(file_name)
                counter = 1
                while (file_system.get_dir() / f'{base} ({counter}){ext}').exists():
                    counter += 1
                file_name = f'{base} ({counter}){ext}'
                file_path = file_system.get_dir() / file_name
            async with await anyio.open_file(file_path, 'wb') as f:
                await f.write(pdf_bytes)
            file_size = file_path.stat().st_size
            msg = f'Saved page as PDF: {file_name} ({file_size:,} bytes)'
            logger.info(f'📄 {msg}. Full path: {file_path}')
            return ActionResult(extracted_content=msg, long_term_memory=f'{msg}. Full path: {file_path}', attachments=[str(file_path)])

        @self.registry.action('', param_model=GetDropdownOptionsAction)
        async def dropdown_options(params: GetDropdownOptionsAction, browser_session: BrowserSession):
            node = await browser_session.get_element_by_index(params.index)
            if node is None:
                msg = f'Element index {params.index} not available - page may have changed. Try refreshing browser state.'
                logger.warning(f'⚠️ {msg}')
                return ActionResult(extracted_content=msg)
            event = browser_session.event_bus.dispatch(GetDropdownOptionsEvent(node=node))
            dropdown_data = await event.event_result(timeout=3.0, raise_if_none=True, raise_if_any=True)
            if not dropdown_data:
                raise ValueError('Failed to get dropdown options - no data returned')
            return ActionResult(extracted_content=dropdown_data['short_term_memory'], long_term_memory=dropdown_data['long_term_memory'], include_extracted_content_only_once=True)

        @self.registry.action('Set the option of a <select> element.', param_model=SelectDropdownOptionAction)
        async def select_dropdown(params: SelectDropdownOptionAction, browser_session: BrowserSession):
            node = await browser_session.get_element_by_index(params.index)
            if node is None:
                msg = f'Element index {params.index} not available - page may have changed. Try refreshing browser state.'
                logger.warning(f'⚠️ {msg}')
                return ActionResult(extracted_content=msg)
            from system.browser.events import SelectDropdownOptionEvent
            event = browser_session.event_bus.dispatch(SelectDropdownOptionEvent(node=node, text=params.text))
            selection_data = await event.event_result()
            if not selection_data:
                raise ValueError('Failed to select dropdown option - no data returned')
            if selection_data.get('success') == 'true':
                msg = selection_data.get('message', f'Selected option: {params.text}')
                return ActionResult(extracted_content=msg, include_in_memory=True, long_term_memory=f"Selected dropdown option '{params.text}' at index {params.index}")
            elif 'short_term_memory' in selection_data and 'long_term_memory' in selection_data:
                return ActionResult(extracted_content=selection_data['short_term_memory'], long_term_memory=selection_data['long_term_memory'], include_extracted_content_only_once=True)
            else:
                error_msg = selection_data.get('error', f'Failed to select option: {params.text}')
                return ActionResult(error=error_msg)

        @self.registry.action('Write content to a file. By default this OVERWRITES the entire file - use append=true to add to an existing file, or use replace_file for targeted edits within a file. FILENAME RULES: Use only letters, numbers, underscores, hyphens, dots, parentheses. Spaces are auto-converted to hyphens. SUPPORTED EXTENSIONS: .txt, .md, .json, .jsonl, .csv, .html, .xml, .pdf, .docx. CANNOT write binary/image files (.png, .jpg, .mp4, etc.) - do not attempt to save screenshots as files. For PDF files, write content in markdown format and it will be auto-converted to PDF.')
        async def write_file(file_name: str, content: str, file_system: FileSystem, append: bool=False, trailing_newline: bool=True, leading_newline: bool=False):
            if trailing_newline:
                content += '\n'
            if leading_newline:
                content = '\n' + content
            if append:
                result = await file_system.append_file(file_name, content)
            else:
                result = await file_system.write_file(file_name, content)
            resolved_name, _ = file_system._resolve_filename(file_name)
            file_path = file_system.get_dir() / resolved_name
            logger.info(f'💾 {result} File location: {file_path}')
            return ActionResult(extracted_content=result, long_term_memory=result)

        @self.registry.action('Replace specific text within a file by searching for old_str and replacing with new_str. Use this for targeted edits like updating todo checkboxes or modifying specific lines without rewriting the entire file.')
        async def replace_file(file_name: str, old_str: str, new_str: str, file_system: FileSystem):
            result = await file_system.replace_file_str(file_name, old_str, new_str)
            logger.info(f'💾 {result}')
            return ActionResult(extracted_content=result, long_term_memory=result)

        @self.registry.action('Read the complete content of a file. Use this to view file contents before editing or to retrieve data from files. Supports text files (txt, md, json, csv, jsonl), documents (pdf, docx), and images (jpg, png).')
        async def read_file(file_name: str, available_file_paths: list[str], file_system: FileSystem):
            if available_file_paths and file_name in available_file_paths:
                structured_result = await file_system.read_file_structured(file_name, external_file=True)
            else:
                structured_result = await file_system.read_file_structured(file_name)
            result = structured_result['message']
            images = structured_result.get('images')
            MAX_MEMORY_SIZE = 1000
            if images:
                memory = f'Read image file {file_name}'
            elif len(result) > MAX_MEMORY_SIZE:
                lines = result.splitlines()
                display = ''
                lines_count = 0
                for line in lines:
                    if len(display) + len(line) < MAX_MEMORY_SIZE:
                        display += line + '\n'
                        lines_count += 1
                    else:
                        break
                remaining_lines = len(lines) - lines_count
                memory = f'{display}{remaining_lines} more lines...' if remaining_lines > 0 else display
            else:
                memory = result
            logger.info(f'💾 {memory}')
            return ActionResult(extracted_content=result, long_term_memory=memory, images=images, include_extracted_content_only_once=True)

        @self.registry.action('Intelligently read long content to find specific information. Works on current page (source="page") or files. For large content, uses search to identify relevant sections. Best for long articles, documents, or any content where you know what you are looking for.', param_model=ReadContentAction)
        async def read_long_content(params: ReadContentAction, browser_session: BrowserSession, page_extraction_llm: BaseChatModel, available_file_paths: list[str]):
            import re
            from system.llm.messages import UserMessage
            goal = params.goal
            context = params.context
            source = params.source
            max_chars = 50000

            async def extract_search_terms(goal: str, context: str) -> list[str]:
                prompt = f'Extract 3-5 key search terms from this goal that would help find relevant sections.\nReturn only the terms, one per line, no numbering or bullets.\n\nGoal: {goal}\n\nContext: {context}'
                response = await page_extraction_llm.ainvoke([UserMessage(content=prompt)])
                return [term.strip() for term in response.completion.strip().split('\n') if term.strip()][:5]

            def search_text(content: str, pattern: str, context_chars: int=100) -> list[dict]:
                try:
                    regex = re.compile(pattern, re.IGNORECASE)
                except re.error:
                    regex = re.compile(re.escape(pattern), re.IGNORECASE)
                matches = []
                for match in regex.finditer(content):
                    start = max(0, match.start() - context_chars)
                    end = min(len(content), match.end() + context_chars)
                    matches.append({'position': match.start(), 'snippet': content[start:end]})
                return matches

            def chunk_content(content: str, chunk_size: int=2000) -> list[dict]:
                chunks = []
                for i in range(0, len(content), chunk_size):
                    chunks.append({'start': i, 'end': min(i + chunk_size, len(content)), 'text': content[i:i + chunk_size]})
                return chunks
            try:
                if source.lower() == 'page':
                    from system.dom.markdown_extractor import extract_clean_markdown
                    if browser_session._dom_watchdog:
                        browser_session._dom_watchdog.clear_cache()
                    wait_time = browser_session.browser_profile.wait_for_network_idle_page_load_time
                    await asyncio.sleep(wait_time)
                    content, _ = await extract_clean_markdown(browser_session=browser_session, extract_links=False)
                    source_name = 'current page'
                    if not content:
                        return ActionResult(extracted_content='Error: No page content available', long_term_memory='Failed to read page: no content')
                else:
                    file_path = source
                    allowed_paths = set(available_file_paths or [])
                    allowed_paths.update(browser_session.downloaded_files)
                    if file_path not in allowed_paths:
                        return ActionResult(extracted_content=f'Error: File path not in available_file_paths: {file_path}. The user must add this path to available_file_paths when creating the Agent.', long_term_memory=f'Failed to read: file path not allowed: {file_path}')
                    if not os.path.exists(file_path):
                        return ActionResult(extracted_content=f'Error: File not found: {file_path}', long_term_memory='Failed to read: file not found')
                    ext = os.path.splitext(file_path)[1].lower()
                    source_name = os.path.basename(file_path)
                    if ext == '.pdf':
                        import pypdf
                        reader = pypdf.PdfReader(file_path)
                        num_pages = len(reader.pages)
                        page_texts: list[str] = []
                        total_chars = 0
                        for page in reader.pages:
                            text = page.extract_text() or ''
                            page_texts.append(text)
                            total_chars += len(text)
                        if total_chars <= max_chars:
                            content_parts = []
                            for i, text in enumerate(page_texts, 1):
                                if text.strip():
                                    content_parts.append(f'--- Page {i} ---\n{text}')
                            content = '\n\n'.join(content_parts)
                            memory = f'Read {source_name} ({num_pages} pages, {total_chars:,} chars) for goal: {goal[:50]}'
                            logger.info(f'📄 {memory}')
                            return ActionResult(extracted_content=f'PDF: {source_name} ({num_pages} pages)\n\n{content}', long_term_memory=memory, include_extracted_content_only_once=True)
                        logger.info(f'PDF has {total_chars:,} chars across {num_pages} pages, using intelligent extraction')
                        search_terms = await extract_search_terms(goal, context)
                        page_scores: dict[int, int] = {}
                        for term in search_terms:
                            try:
                                term_pattern = re.compile(re.escape(term), re.IGNORECASE)
                            except re.error:
                                continue
                            for i, text in enumerate(page_texts, 1):
                                if term_pattern.search(text):
                                    page_scores[i] = page_scores.get(i, 0) + 1
                        pages_to_read = [1]
                        sorted_pages = sorted(page_scores.items(), key=lambda x: -x[1])
                        for page_num, _ in sorted_pages:
                            if page_num not in pages_to_read:
                                pages_to_read.append(page_num)
                        content_parts = []
                        chars_used = 0
                        pages_included = []
                        for page_num in sorted(set(pages_to_read)):
                            text = page_texts[page_num - 1]
                            page_header = f'--- Page {page_num} ---\n'
                            remaining = max_chars - chars_used
                            if remaining < len(page_header) + 50:
                                break
                            page_content = page_header + text
                            if len(page_content) > remaining:
                                page_content = page_content[:remaining - len('\n[...truncated]')] + '\n[...truncated]'
                            content_parts.append(page_content)
                            chars_used += len(page_content)
                            pages_included.append(page_num)
                        content = '\n\n'.join(content_parts)
                        memory = f'Read {source_name} ({len(pages_included)} relevant pages of {num_pages}) for goal: {goal[:50]}'
                        logger.info(f'📄 {memory}')
                        return ActionResult(extracted_content=f'PDF: {source_name} ({num_pages} pages, showing {len(pages_included)} relevant)\n\n{content}', long_term_memory=memory, include_extracted_content_only_once=True)
                    else:
                        async with await anyio.open_file(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = await f.read()
                if len(content) <= max_chars:
                    memory = f'Read {source_name} ({len(content):,} chars) for goal: {goal[:50]}'
                    logger.info(f'📄 {memory}')
                    return ActionResult(extracted_content=f'Content from {source_name} ({len(content):,} chars):\n\n{content}', long_term_memory=memory, include_extracted_content_only_once=True)
                logger.info(f'Content has {len(content):,} chars, using intelligent extraction')
                search_terms = await extract_search_terms(goal, context)
                chunks = chunk_content(content, chunk_size=2000)
                chunk_scores: dict[int, int] = {}
                for term in search_terms:
                    matches = search_text(content, term)
                    for match in matches:
                        for i, chunk in enumerate(chunks):
                            if chunk['start'] <= match['position'] < chunk['end']:
                                chunk_scores[i] = chunk_scores.get(i, 0) + 1
                                break
                if not chunk_scores:
                    truncated = content[:max_chars]
                    memory = f'Read {source_name} (truncated to {max_chars:,} chars, no matches for search terms)'
                    logger.info(f'📄 {memory}')
                    return ActionResult(extracted_content=f'Content from {source_name} (first {max_chars:,} of {len(content):,} chars):\n\n{truncated}', long_term_memory=memory, include_extracted_content_only_once=True)
                sorted_chunks = sorted(chunk_scores.items(), key=lambda x: -x[1])
                selected_indices = {0}
                for chunk_idx, _ in sorted_chunks:
                    selected_indices.add(chunk_idx)
                result_parts = []
                total_chars = 0
                for i in sorted(selected_indices):
                    chunk = chunks[i]
                    if total_chars + len(chunk['text']) > max_chars:
                        break
                    if i > 0 and i - 1 not in selected_indices:
                        result_parts.append('\n[...]\n')
                    result_parts.append(chunk['text'])
                    total_chars += len(chunk['text'])
                result_content = ''.join(result_parts)
                memory = f'Read {source_name} ({len(selected_indices)} relevant sections of {len(chunks)}) for goal: {goal[:50]}'
                logger.info(f'📄 {memory}')
                return ActionResult(extracted_content=f'Content from {source_name} (relevant sections, {total_chars:,} of {len(content):,} chars):\n\n{result_content}', long_term_memory=memory, include_extracted_content_only_once=True)
            except Exception as e:
                error_msg = f'Error reading content: {str(e)}'
                logger.error(error_msg)
                return ActionResult(extracted_content=error_msg, long_term_memory=error_msg)

        @self.registry.action("Execute browser JavaScript. Best practice: wrap in IIFE (function(){...})() with try-catch for safety. Use ONLY browser APIs (document, window, DOM). NO Node.js APIs (fs, require, process). Example: (function(){try{const el=document.querySelector('#id');return el?el.value:'not found'}catch(e){return 'Error: '+e.message}})() Avoid comments. Use for hover, drag, zoom, custom selectors, extract/filter links, or analysing page structure. IMPORTANT: Shadow DOM elements with [index] markers can be clicked directly with click(index) — do NOT use evaluate() to click them. Only use evaluate for shadow DOM elements that are NOT indexed. Limit output size.", terminates_sequence=True)
        async def evaluate(code: str, browser_session: BrowserSession):
            cdp_session = await browser_session.get_or_create_cdp_session()
            try:
                validated_code = self._validate_and_fix_javascript(code)
                result = await cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': validated_code, 'returnByValue': True, 'awaitPromise': True}, session_id=cdp_session.session_id)
                if result.get('exceptionDetails'):
                    exception = result['exceptionDetails']
                    error_msg = f"JavaScript execution error: {exception.get('text', 'Unknown error')}"
                    enhanced_msg = f"JavaScript Execution Failed:\n{error_msg}\n\nValidated Code (after quote fixing):\n{validated_code[:500]}{('...' if len(validated_code) > 500 else '')}\n"
                    logger.debug(enhanced_msg)
                    return ActionResult(error=enhanced_msg)
                result_data = result.get('result', {})
                if result_data.get('wasThrown'):
                    msg = f'JavaScript code: {code} execution failed (wasThrown=true)'
                    logger.debug(msg)
                    return ActionResult(error=msg)
                value = result_data.get('value')
                if value is None:
                    result_text = str(value) if 'value' in result_data else 'undefined'
                elif isinstance(value, (dict, list)):
                    try:
                        result_text = json.dumps(value, ensure_ascii=False)
                    except (TypeError, ValueError):
                        result_text = str(value)
                else:
                    result_text = str(value)
                import re
                image_pattern = '(data:image/[^;]+;base64,[A-Za-z0-9+/=]+)'
                found_images = re.findall(image_pattern, result_text)
                metadata = None
                if found_images:
                    metadata = {'images': found_images}
                    modified_text = result_text
                    for i, img_data in enumerate(found_images, 1):
                        placeholder = '[Image]'
                        modified_text = modified_text.replace(img_data, placeholder)
                    result_text = modified_text
                if len(result_text) > 20000:
                    result_text = result_text[:19950] + '\n... [Truncated after 20000 characters]'
                logger.debug(f'JavaScript executed successfully, result length: {len(result_text)}')
                MAX_MEMORY_LENGTH = 10000
                if len(result_text) < MAX_MEMORY_LENGTH:
                    memory = result_text
                    include_extracted_content_only_once = False
                else:
                    memory = f'JavaScript executed successfully, result length: {len(result_text)} characters.'
                    include_extracted_content_only_once = True
                return ActionResult(extracted_content=result_text, long_term_memory=memory, include_extracted_content_only_once=include_extracted_content_only_once, metadata=metadata)
            except Exception as e:
                error_msg = f'Failed to execute JavaScript: {type(e).__name__}: {e}'
                logger.debug(f'JavaScript code that failed: {code[:200]}...')
                return ActionResult(error=error_msg)

    def _validate_and_fix_javascript(self, code: str) -> str:
        import re
        fixed_code = re.sub('\\\\"', '"', code)
        fixed_code = re.sub('\\\\\\\\([dDsSwWbBnrtfv])', '\\\\\\1', fixed_code)
        fixed_code = re.sub('\\\\\\\\([.*+?^${}()|[\\]])', '\\\\\\1', fixed_code)
        xpath_pattern = 'document\\.evaluate\\s*\\(\\s*"([^"]*)"\\s*,'

        def fix_xpath_quotes(match):
            xpath_with_quotes = match.group(1)
            return f'document.evaluate(`{xpath_with_quotes}`,'
        fixed_code = re.sub(xpath_pattern, fix_xpath_quotes, fixed_code)
        selector_pattern = '(querySelector(?:All)?)\\s*\\(\\s*"([^"]*)"\\s*\\)'

        def fix_selector_quotes(match):
            method_name = match.group(1)
            selector_with_quotes = match.group(2)
            return f'{method_name}(`{selector_with_quotes}`)'
        fixed_code = re.sub(selector_pattern, fix_selector_quotes, fixed_code)
        closest_pattern = '\\.closest\\s*\\(\\s*"([^"]*)"\\s*\\)'

        def fix_closest_quotes(match):
            selector_with_quotes = match.group(1)
            return f'.closest(`{selector_with_quotes}`)'
        fixed_code = re.sub(closest_pattern, fix_closest_quotes, fixed_code)
        matches_pattern = '\\.matches\\s*\\(\\s*"([^"]*)"\\s*\\)'

        def fix_matches_quotes(match):
            selector_with_quotes = match.group(1)
            return f'.matches(`{selector_with_quotes}`)'
        fixed_code = re.sub(matches_pattern, fix_matches_quotes, fixed_code)
        changes_made = []
        if '\\"' in code and '\\"' not in fixed_code:
            changes_made.append('fixed escaped quotes')
        if '`' in fixed_code and '`' not in code:
            changes_made.append('converted mixed quotes to template literals')
        if changes_made:
            logger.debug(f"JavaScript fixes applied: {', '.join(changes_made)}")
        return fixed_code

    def _register_done_action(self, output_model: type[T] | None, display_files_in_done_text: bool=True):
        if output_model is not None:
            self.display_files_in_done_text = display_files_in_done_text

            @self.registry.action('Complete task with structured output.', param_model=StructuredOutputAction[output_model])
            async def done(params: StructuredOutputAction, file_system: FileSystem, browser_session: BrowserSession):
                output_dict = params.data.model_dump(mode='json')
                attachments: list[str] = []
                if params.files_to_display:
                    for file_name in params.files_to_display:
                        file_content = file_system.display_file(file_name)
                        if file_content:
                            attachments.append(str(file_system.get_dir() / file_name))
                session_downloads = browser_session.downloaded_files
                if session_downloads:
                    existing = set(attachments)
                    for file_path in session_downloads:
                        if file_path not in existing:
                            attachments.append(file_path)
                return ActionResult(is_done=True, success=params.success, extracted_content=json.dumps(output_dict, ensure_ascii=False), long_term_memory=f'Task completed. Success Status: {params.success}', attachments=attachments)
        else:

            @self.registry.action('Complete task.', param_model=DoneAction)
            async def done(params: DoneAction, file_system: FileSystem):
                user_message = params.text
                len_text = len(params.text)
                len_max_memory = 100
                memory = f'Task completed: {params.success} - {params.text[:len_max_memory]}'
                if len_text > len_max_memory:
                    memory += f' - {len_text - len_max_memory} more characters'
                attachments = []
                if params.files_to_display:
                    if self.display_files_in_done_text:
                        file_msg = ''
                        for file_name in params.files_to_display:
                            file_content = file_system.display_file(file_name)
                            if file_content:
                                file_msg += f'\n\n{file_name}:\n{file_content}'
                                attachments.append(file_name)
                        if file_msg:
                            user_message += '\n\nAttachments:'
                            user_message += file_msg
                        else:
                            logger.warning('Agent wanted to display files but none were found')
                    else:
                        for file_name in params.files_to_display:
                            file_content = file_system.display_file(file_name)
                            if file_content:
                                attachments.append(file_name)
                attachments = [str(file_system.get_dir() / file_name) for file_name in attachments]
                return ActionResult(is_done=True, success=params.success, extracted_content=user_message, long_term_memory=memory, attachments=attachments)

    def use_structured_output_action(self, output_model: type[T]):
        self._output_model = output_model
        self._register_done_action(output_model)

    def get_output_model(self) -> type[BaseModel] | None:
        return self._output_model

    def action(self, description: str, **kwargs):
        return self.registry.action(description, **kwargs)

    def exclude_action(self, action_name: str) -> None:
        self.registry.exclude_action(action_name)

    def _register_click_action(self) -> None:
        if 'click' in self.registry.registry.actions:
            del self.registry.registry.actions['click']
        if self._coordinate_clicking_enabled:

            @self.registry.action('Click element by index or coordinates. Use coordinates only if the index is not available. Either provide coordinates or index.', param_model=ClickElementAction)
            async def click(params: ClickElementAction, browser_session: BrowserSession):
                if params.index is None and (params.coordinate_x is None or params.coordinate_y is None):
                    return ActionResult(error='Must provide either index or both coordinate_x and coordinate_y')
                if params.index is not None:
                    return await self._click_by_index(params, browser_session)
                else:
                    return await self._click_by_coordinate(params, browser_session)
        else:

            @self.registry.action('Click element by index.', param_model=ClickElementActionIndexOnly)
            async def click(params: ClickElementActionIndexOnly, browser_session: BrowserSession):
                return await self._click_by_index(params, browser_session)

    def set_coordinate_clicking(self, enabled: bool) -> None:
        if enabled == self._coordinate_clicking_enabled:
            return
        self._coordinate_clicking_enabled = enabled
        self._register_click_action()
        logger.debug(f"Coordinate clicking {('enabled' if enabled else 'disabled')}")

    @observe_debug(ignore_input=True, ignore_output=True, name='act')
    @time_execution_sync('--act')
    async def act(self, action: ActionModel, browser_session: BrowserSession, page_extraction_llm: BaseChatModel | None=None, sensitive_data: dict[str, str | dict[str, str]] | None=None, available_file_paths: list[str] | None=None, file_system: FileSystem | None=None, extraction_schema: dict | None=None) -> ActionResult:
        for action_name, params in action.model_dump(exclude_unset=True).items():
            if params is not None:
                if Laminar is not None:
                    span_context = Laminar.start_as_current_span(name=action_name, input={'action': action_name, 'params': params}, span_type='TOOL')
                else:
                    from contextlib import nullcontext
                    span_context = nullcontext()
                with span_context:
                    try:
                        result = await self.registry.execute_action(action_name=action_name, params=params, browser_session=browser_session, page_extraction_llm=page_extraction_llm, file_system=file_system, sensitive_data=sensitive_data, available_file_paths=available_file_paths, extraction_schema=extraction_schema)
                    except BrowserError as e:
                        logger.error(f'❌ Action {action_name} failed with BrowserError: {str(e)}')
                        result = handle_browser_error(e)
                    except TimeoutError as e:
                        logger.error(f'❌ Action {action_name} failed with TimeoutError: {str(e)}')
                        result = ActionResult(error=f'{action_name} was not executed due to timeout.')
                    except Exception as e:
                        logger.error(f"Action '{action_name}' failed with error: {str(e)}")
                        result = ActionResult(error=str(e))
                    if Laminar is not None:
                        Laminar.set_span_output(result)
                if isinstance(result, str):
                    return ActionResult(extracted_content=result)
                elif isinstance(result, ActionResult):
                    return result
                elif result is None:
                    return ActionResult()
                else:
                    raise ValueError(f'Invalid action result type: {type(result)} of {result}')
        return ActionResult()

    def __getattr__(self, name: str):
        if name in self.registry.registry.actions:
            from typing import Union
            from pydantic import create_model
            action = self.registry.registry.actions[name]

            async def action_wrapper(**kwargs):
                browser_session = kwargs.get('browser_session')
                special_param_names = {'browser_session', 'page_extraction_llm', 'file_system', 'available_file_paths', 'sensitive_data', 'extraction_schema'}
                action_params = {k: v for k, v in kwargs.items() if k not in special_param_names}
                special_kwargs = {k: v for k, v in kwargs.items() if k in special_param_names and k != 'browser_session'}
                params_instance = action.param_model(**action_params)
                DynamicActionModel = create_model('DynamicActionModel', __base__=ActionModel, **{name: (Union[action.param_model, None], None)})
                action_model = DynamicActionModel(**{name: params_instance})
                return await self.act(action=action_model, browser_session=browser_session, **special_kwargs)
            return action_wrapper
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
Controller = Tools

class CodeAgentTools(Tools[Context]):

    def __init__(self, exclude_actions: list[str] | None=None, output_model: type[T] | None=None, display_files_in_done_text: bool=True):
        if exclude_actions is None:
            exclude_actions = ['extract', 'find_text', 'screenshot', 'search', 'write_file', 'read_file', 'replace_file']
        super().__init__(exclude_actions=exclude_actions, output_model=output_model, display_files_in_done_text=display_files_in_done_text)
        self._register_code_use_done_action(output_model, display_files_in_done_text)

    def _register_code_use_done_action(self, output_model: type[T] | None, display_files_in_done_text: bool=True):
        if output_model is not None:
            return

        @self.registry.action('Complete task.', param_model=DoneAction)
        async def done(params: DoneAction, file_system: FileSystem):
            user_message = params.text
            len_text = len(params.text)
            len_max_memory = 100
            memory = f'Task completed: {params.success} - {params.text[:len_max_memory]}'
            if len_text > len_max_memory:
                memory += f' - {len_text - len_max_memory} more characters'
            attachments = []
            if params.files_to_display:
                if self.display_files_in_done_text:
                    file_msg = ''
                    for file_name in params.files_to_display:
                        file_content = file_system.display_file(file_name)
                        if file_content:
                            file_msg += f'\n\n{file_name}:\n{file_content}'
                            attachments.append(file_name)
                        elif os.path.exists(file_name):
                            attachments.append(file_name)
                    if file_msg:
                        user_message += '\n\nAttachments:'
                        user_message += file_msg
                    else:
                        logger.warning('Agent wanted to display files but none were found')
                else:
                    for file_name in params.files_to_display:
                        file_content = file_system.display_file(file_name)
                        if file_content:
                            attachments.append(file_name)
                        elif os.path.exists(file_name):
                            attachments.append(file_name)
            resolved_attachments = []
            for file_name in attachments:
                if os.path.isabs(file_name):
                    resolved_attachments.append(file_name)
                elif file_system.get_file(file_name):
                    resolved_attachments.append(str(file_system.get_dir() / file_name))
                elif os.path.exists(file_name):
                    resolved_attachments.append(os.path.abspath(file_name))
                else:
                    resolved_attachments.append(str(file_system.get_dir() / file_name))
            attachments = resolved_attachments
            return ActionResult(is_done=True, success=params.success, extracted_content=user_message, long_term_memory=memory, attachments=attachments)

        @self.registry.action('Upload a file to a file input element. For code-use mode, any file accessible from the current directory can be uploaded.', param_model=UploadFileAction)
        async def upload_file(params: UploadFileAction, browser_session: BrowserSession, available_file_paths: list[str], file_system: FileSystem):
            if available_file_paths:
                if params.path not in available_file_paths:
                    downloaded_files = browser_session.downloaded_files
                    if params.path not in downloaded_files:
                        if file_system is not None and file_system.get_dir():
                            file_obj = file_system.get_file(params.path)
                            if file_obj:
                                file_system_path = str(file_system.get_dir() / params.path)
                                params = UploadFileAction(index=params.index, path=file_system_path)
                            elif not browser_session.is_local:
                                pass
                            else:
                                msg = f'File path {params.path} is not available. To fix: add this file path to the available_file_paths parameter when creating the Agent. Example: Agent(task="...", llm=llm, browser=browser, available_file_paths=["{params.path}"])'
                                logger.error(f'❌ {msg}')
                                return ActionResult(error=msg)
                        elif not browser_session.is_local:
                            pass
                        else:
                            msg = f'File path {params.path} is not available. To fix: add this file path to the available_file_paths parameter when creating the Agent. Example: Agent(task="...", llm=llm, browser=browser, available_file_paths=["{params.path}"])'
                            logger.error(f'❌ {msg}')
                            return ActionResult(error=msg)
            if browser_session.is_local:
                if not os.path.exists(params.path):
                    msg = f'File {params.path} does not exist'
                    return ActionResult(error=msg)
            selector_map = await browser_session.get_selector_map()
            if params.index not in selector_map:
                msg = f'Element with index {params.index} does not exist.'
                return ActionResult(error=msg)
            node = selector_map[params.index]

            def find_file_input_near_element(node: EnhancedDOMTreeNode, max_height: int=3, max_descendant_depth: int=3) -> EnhancedDOMTreeNode | None:

                def find_file_input_in_descendants(n: EnhancedDOMTreeNode, depth: int) -> EnhancedDOMTreeNode | None:
                    if depth < 0:
                        return None
                    if browser_session.is_file_input(n):
                        return n
                    for child in n.children_nodes or []:
                        result = find_file_input_in_descendants(child, depth - 1)
                        if result:
                            return result
                    return None
                current = node
                for _ in range(max_height + 1):
                    if browser_session.is_file_input(current):
                        return current
                    result = find_file_input_in_descendants(current, max_descendant_depth)
                    if result:
                        return result
                    if current.parent_node:
                        for sibling in current.parent_node.children_nodes or []:
                            if sibling is current:
                                continue
                            if browser_session.is_file_input(sibling):
                                return sibling
                            result = find_file_input_in_descendants(sibling, max_descendant_depth)
                            if result:
                                return result
                    current = current.parent_node
                    if not current:
                        break
                return None
            file_input_node = find_file_input_near_element(node)
            if file_input_node:
                create_task_with_error_handling(browser_session.highlight_interaction_element(file_input_node), name='highlight_file_input', suppress_exceptions=True)
            if file_input_node is None:
                logger.info(f'No file upload element found near index {params.index}, searching for closest file input to scroll position')
                cdp_session = await browser_session.get_or_create_cdp_session()
                try:
                    scroll_info = await cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': 'window.scrollY || window.pageYOffset || 0'}, session_id=cdp_session.session_id)
                    current_scroll_y = scroll_info.get('result', {}).get('value', 0)
                except Exception:
                    current_scroll_y = 0
                closest_file_input = None
                min_distance = float('inf')
                for idx, element in selector_map.items():
                    if browser_session.is_file_input(element):
                        if element.absolute_position:
                            element_y = element.absolute_position.y
                            distance = abs(element_y - current_scroll_y)
                            if distance < min_distance:
                                min_distance = distance
                                closest_file_input = element
                if closest_file_input:
                    file_input_node = closest_file_input
                    logger.info(f'Found file input closest to scroll position (distance: {min_distance}px)')
                    create_task_with_error_handling(browser_session.highlight_interaction_element(file_input_node), name='highlight_file_input_fallback', suppress_exceptions=True)
                else:
                    msg = 'No file upload element found on the page'
                    logger.error(msg)
                    raise BrowserError(msg)
            try:
                event = browser_session.event_bus.dispatch(UploadFileEvent(node=file_input_node, file_path=params.path))
                await event
                await event.event_result(raise_if_any=True, raise_if_none=False)
                msg = f'Successfully uploaded file to index {params.index}'
                logger.info(f'📁 {msg}')
                return ActionResult(extracted_content=msg, long_term_memory=f'Uploaded file {params.path} to element {params.index}')
            except Exception as e:
                logger.error(f'Failed to upload file: {e}')
                raise BrowserError(f'Failed to upload file: {e}')