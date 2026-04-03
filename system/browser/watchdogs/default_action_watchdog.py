import asyncio
import json
import os
from cdp_use.cdp.input.commands import DispatchKeyEventParameters
from system.actor.utils import get_key_info
from system.browser.events import ClickCoordinateEvent, ClickElementEvent, GetDropdownOptionsEvent, GoBackEvent, GoForwardEvent, RefreshEvent, ScrollEvent, ScrollToTextEvent, SelectDropdownOptionEvent, SendKeysEvent, TypeTextEvent, UploadFileEvent, WaitEvent
from system.browser.views import BrowserError, URLNotAllowedError
from system.browser.watchdog_base import BaseWatchdog
from system.dom.service import EnhancedDOMTreeNode
from system.observability import observe_debug
ClickCoordinateEvent.model_rebuild()
ClickElementEvent.model_rebuild()
GetDropdownOptionsEvent.model_rebuild()
SelectDropdownOptionEvent.model_rebuild()
TypeTextEvent.model_rebuild()
ScrollEvent.model_rebuild()
UploadFileEvent.model_rebuild()

class DefaultActionWatchdog(BaseWatchdog):

    async def _execute_click_with_download_detection(self, click_coro, download_start_timeout: float=0.5, download_complete_timeout: float=30.0) -> dict | None:
        import time
        download_started = asyncio.Event()
        download_completed = asyncio.Event()
        download_info: dict = {}
        progress_info: dict = {'last_update': 0.0, 'received_bytes': 0, 'total_bytes': 0, 'state': ''}

        def on_download_start(info: dict) -> None:
            if info.get('auto_download'):
                return
            download_info['guid'] = info.get('guid', '')
            download_info['url'] = info.get('url', '')
            download_info['suggested_filename'] = info.get('suggested_filename', 'download')
            download_started.set()
            self.logger.debug(f"[ClickWithDownload] Download started: {download_info['suggested_filename']}")

        def on_download_progress(info: dict) -> None:
            if download_info.get('guid') and info.get('guid') != download_info['guid']:
                return
            progress_info['last_update'] = time.time()
            progress_info['received_bytes'] = info.get('received_bytes', 0)
            progress_info['total_bytes'] = info.get('total_bytes', 0)
            progress_info['state'] = info.get('state', '')
            self.logger.debug(f"[ClickWithDownload] Progress: {progress_info['received_bytes']}/{progress_info['total_bytes']} bytes ({progress_info['state']})")

        def on_download_complete(info: dict) -> None:
            if info.get('auto_download'):
                return
            if download_info.get('guid') and info.get('guid') and (info.get('guid') != download_info['guid']):
                return
            download_info['path'] = info.get('path', '')
            download_info['file_name'] = info.get('file_name', '')
            download_info['file_size'] = info.get('file_size', 0)
            download_info['file_type'] = info.get('file_type')
            download_info['mime_type'] = info.get('mime_type')
            download_completed.set()
            self.logger.debug(f"[ClickWithDownload] Download completed: {download_info['file_name']}")
        downloads_watchdog = self.browser_session._downloads_watchdog
        self.logger.debug(f'[ClickWithDownload] downloads_watchdog={downloads_watchdog is not None}')
        if downloads_watchdog:
            self.logger.debug('[ClickWithDownload] Registering download callbacks...')
            downloads_watchdog.register_download_callbacks(on_start=on_download_start, on_progress=on_download_progress, on_complete=on_download_complete)
        else:
            self.logger.warning('[ClickWithDownload] No downloads_watchdog available!')
        try:
            click_metadata = await click_coro
            if isinstance(click_metadata, dict) and 'validation_error' in click_metadata:
                return click_metadata
            try:
                await asyncio.wait_for(download_started.wait(), timeout=download_start_timeout)
                self.logger.info(f"📥 Download started: {download_info.get('suggested_filename', 'unknown')}")
                try:
                    await asyncio.wait_for(download_completed.wait(), timeout=download_complete_timeout)
                    msg = f"Downloaded file: {download_info['file_name']} ({download_info['file_size']} bytes) saved to {download_info['path']}"
                    self.logger.info(f'💾 {msg}')
                    if click_metadata is None:
                        click_metadata = {}
                    click_metadata['download'] = {'path': download_info['path'], 'file_name': download_info['file_name'], 'file_size': download_info['file_size'], 'file_type': download_info.get('file_type'), 'mime_type': download_info.get('mime_type')}
                except TimeoutError:
                    if click_metadata is None:
                        click_metadata = {}
                    filename = download_info.get('suggested_filename', 'unknown')
                    received = progress_info.get('received_bytes', 0)
                    total = progress_info.get('total_bytes', 0)
                    state = progress_info.get('state', 'unknown')
                    last_update = progress_info.get('last_update', 0.0)
                    time_since_update = time.time() - last_update if last_update > 0 else float('inf')
                    is_still_active = time_since_update < 5.0 and state == 'inProgress'
                    if is_still_active:
                        if total > 0:
                            percent = received / total * 100
                            progress_str = f'{percent:.1f}% ({received:,}/{total:,} bytes)'
                        else:
                            progress_str = f'{received:,} bytes downloaded (total size unknown)'
                        msg = f'Download timed out after {download_complete_timeout}s but is still in progress: {filename} - {progress_str}. The download appears to be progressing normally. Consider using the wait action to allow more time for the download to complete.'
                        self.logger.warning(f'⏱️ {msg}')
                        click_metadata['download_in_progress'] = {'file_name': filename, 'received_bytes': received, 'total_bytes': total, 'state': state, 'message': msg}
                    else:
                        if received > 0:
                            msg = f'Download timed out after {download_complete_timeout}s: {filename}. Last progress: {received:,} bytes received. The download may have stalled or completed - check the downloads folder.'
                        else:
                            msg = f'Download timed out after {download_complete_timeout}s: {filename}. No progress data received - the download may have failed to start properly.'
                        self.logger.warning(f'⏱️ {msg}')
                        click_metadata['download_timeout'] = {'file_name': filename, 'received_bytes': received, 'total_bytes': total, 'message': msg}
            except TimeoutError:
                pass
            return click_metadata if isinstance(click_metadata, dict) else None
        finally:
            if downloads_watchdog:
                downloads_watchdog.unregister_download_callbacks(on_start=on_download_start, on_progress=on_download_progress, on_complete=on_download_complete)

    def _is_print_related_element(self, element_node: EnhancedDOMTreeNode) -> bool:
        onclick = element_node.attributes.get('onclick', '').lower() if element_node.attributes else ''
        if onclick and 'print' in onclick:
            return True
        return False

    async def _handle_print_button_click(self, element_node: EnhancedDOMTreeNode) -> dict | None:
        try:
            import base64
            import os
            from pathlib import Path
            cdp_session = await self.browser_session.get_or_create_cdp_session(focus=True)
            result = await asyncio.wait_for(cdp_session.cdp_client.send.Page.printToPDF(params={'printBackground': True, 'preferCSSPageSize': True}, session_id=cdp_session.session_id), timeout=15.0)
            pdf_data = result.get('data')
            if not pdf_data:
                self.logger.warning('⚠️ PDF generation returned no data')
                return None
            pdf_bytes = base64.b64decode(pdf_data)
            downloads_path = self.browser_session.browser_profile.downloads_path
            if not downloads_path:
                self.logger.warning('⚠️ No downloads path configured, cannot save PDF')
                return None
            try:
                page_title = await asyncio.wait_for(self.browser_session.get_current_page_title(), timeout=2.0)
                import re
                safe_title = re.sub('[^\\w\\s-]', '', page_title)[:50]
                filename = f'{safe_title}.pdf' if safe_title else 'print.pdf'
            except Exception:
                filename = 'print.pdf'
            downloads_dir = Path(downloads_path).expanduser().resolve()
            downloads_dir.mkdir(parents=True, exist_ok=True)
            final_path = downloads_dir / filename
            if final_path.exists():
                base, ext = os.path.splitext(filename)
                counter = 1
                while (downloads_dir / f'{base} ({counter}){ext}').exists():
                    counter += 1
                final_path = downloads_dir / f'{base} ({counter}){ext}'
            import anyio
            async with await anyio.open_file(final_path, 'wb') as f:
                await f.write(pdf_bytes)
            file_size = final_path.stat().st_size
            self.logger.info(f'✅ Generated PDF via CDP: {final_path} ({file_size:,} bytes)')
            from system.browser.events import FileDownloadedEvent
            page_url = await self.browser_session.get_current_page_url()
            self.browser_session.event_bus.dispatch(FileDownloadedEvent(url=page_url, path=str(final_path), file_name=final_path.name, file_size=file_size, file_type='pdf', mime_type='application/pdf', auto_download=False))
            return {'pdf_generated': True, 'path': str(final_path)}
        except TimeoutError:
            self.logger.warning('⏱️ PDF generation timed out')
            return None
        except Exception as e:
            self.logger.warning(f'⚠️ Failed to generate PDF via CDP: {type(e).__name__}: {e}')
            return None

    @observe_debug(ignore_input=True, ignore_output=True, name='click_element_event')
    async def on_ClickElementEvent(self, event: ClickElementEvent) -> dict | None:
        try:
            if not self.browser_session.agent_focus_target_id:
                error_msg = 'Cannot execute click: browser session is corrupted (target_id=None). Session may have crashed.'
                self.logger.error(f'{error_msg}')
                raise BrowserError(error_msg)
            element_node = event.node
            index_for_logging = element_node.backend_node_id or 'unknown'
            if self.browser_session.is_file_input(element_node):
                msg = f'Index {index_for_logging} - has an element which opens file upload dialog. To upload files please use a specific function to upload files'
                self.logger.info(f'{msg}')
                return {'validation_error': msg}
            is_print_element = self._is_print_related_element(element_node)
            if is_print_element:
                self.logger.info(f'🖨️ Detected print button (index {index_for_logging}), generating PDF directly instead of opening dialog...')
                click_metadata = await self._handle_print_button_click(element_node)
                if click_metadata and click_metadata.get('pdf_generated'):
                    msg = f"Generated PDF: {click_metadata.get('path')}"
                    self.logger.info(f'💾 {msg}')
                    return click_metadata
                else:
                    self.logger.warning('⚠️ PDF generation failed, falling back to regular click')
            click_metadata = await self._execute_click_with_download_detection(self._click_element_node_impl(element_node))
            if isinstance(click_metadata, dict) and 'validation_error' in click_metadata:
                self.logger.info(f"{click_metadata['validation_error']}")
                return click_metadata
            if 'download' not in (click_metadata or {}):
                msg = f'Clicked button {element_node.node_name}: {element_node.get_all_children_text(max_depth=2)}'
                self.logger.debug(f'🖱️ {msg}')
            self.logger.debug(f'Element xpath: {element_node.xpath}')
            return click_metadata
        except Exception:
            raise

    async def on_ClickCoordinateEvent(self, event: ClickCoordinateEvent) -> dict | None:
        try:
            if not self.browser_session.agent_focus_target_id:
                error_msg = 'Cannot execute click: browser session is corrupted (target_id=None). Session may have crashed.'
                self.logger.error(f'{error_msg}')
                raise BrowserError(error_msg)
            if event.force:
                self.logger.debug(f'Force clicking at coordinates ({event.coordinate_x}, {event.coordinate_y})')
                return await self._execute_click_with_download_detection(self._click_on_coordinate(event.coordinate_x, event.coordinate_y, force=True))
            element_node = await self.browser_session.get_dom_element_at_coordinates(event.coordinate_x, event.coordinate_y)
            if element_node is None:
                self.logger.debug(f'No element found at coordinates ({event.coordinate_x}, {event.coordinate_y}), proceeding with click anyway')
                return await self._execute_click_with_download_detection(self._click_on_coordinate(event.coordinate_x, event.coordinate_y, force=False))
            if self.browser_session.is_file_input(element_node):
                msg = f'Cannot click at ({event.coordinate_x}, {event.coordinate_y}) - element is a file input. To upload files please use upload_file action'
                self.logger.info(f'{msg}')
                return {'validation_error': msg}
            tag_name = element_node.tag_name.lower() if element_node.tag_name else ''
            if tag_name == 'select':
                msg = f'Cannot click at ({event.coordinate_x}, {event.coordinate_y}) - element is a <select>. Use dropdown_options action instead.'
                self.logger.info(f'{msg}')
                return {'validation_error': msg}
            is_print_element = self._is_print_related_element(element_node)
            if is_print_element:
                self.logger.info(f'🖨️ Detected print button at ({event.coordinate_x}, {event.coordinate_y}), generating PDF directly instead of opening dialog...')
                click_metadata = await self._handle_print_button_click(element_node)
                if click_metadata and click_metadata.get('pdf_generated'):
                    msg = f"Generated PDF: {click_metadata.get('path')}"
                    self.logger.info(f'💾 {msg}')
                    return click_metadata
                else:
                    self.logger.warning('⚠️ PDF generation failed, falling back to regular click')
            return await self._execute_click_with_download_detection(self._click_on_coordinate(event.coordinate_x, event.coordinate_y, force=False))
        except Exception:
            raise

    async def on_TypeTextEvent(self, event: TypeTextEvent) -> dict | None:
        try:
            element_node = event.node
            index_for_logging = element_node.backend_node_id or 'unknown'
            if not element_node.backend_node_id or element_node.backend_node_id == 0:
                await self._type_to_page(event.text)
                if event.is_sensitive:
                    if event.sensitive_key_name:
                        self.logger.info(f'⌨️ Typed <{event.sensitive_key_name}> to the page (current focus)')
                    else:
                        self.logger.info('⌨️ Typed <sensitive> to the page (current focus)')
                else:
                    self.logger.info(f'⌨️ Typed "{event.text}" to the page (current focus)')
                return None
            else:
                try:
                    input_metadata = await self._input_text_element_node_impl(element_node, event.text, clear=event.clear or not event.text, is_sensitive=event.is_sensitive)
                    if event.is_sensitive:
                        if event.sensitive_key_name:
                            self.logger.info(f'⌨️ Typed <{event.sensitive_key_name}> into element with index {index_for_logging}')
                        else:
                            self.logger.info(f'⌨️ Typed <sensitive> into element with index {index_for_logging}')
                    else:
                        self.logger.info(f'⌨️ Typed "{event.text}" into element with index {index_for_logging}')
                    self.logger.debug(f'Element xpath: {element_node.xpath}')
                    return input_metadata
                except Exception as e:
                    self.logger.warning(f'Failed to type to element {index_for_logging}: {e}. Falling back to page typing.')
                    try:
                        await asyncio.wait_for(self._click_element_node_impl(element_node), timeout=10.0)
                    except Exception as e:
                        pass
                    await self._type_to_page(event.text)
                    if event.is_sensitive:
                        if event.sensitive_key_name:
                            self.logger.info(f'⌨️ Typed <{event.sensitive_key_name}> to the page as fallback')
                        else:
                            self.logger.info('⌨️ Typed <sensitive> to the page as fallback')
                    else:
                        self.logger.info(f'⌨️ Typed "{event.text}" to the page as fallback')
                    return None
        except Exception as e:
            raise

    async def on_ScrollEvent(self, event: ScrollEvent) -> None:
        if not self.browser_session.agent_focus_target_id:
            error_msg = 'No active target for scrolling'
            raise BrowserError(error_msg)
        try:
            pixels = event.amount if event.direction == 'down' else -event.amount
            if event.node is not None:
                element_node = event.node
                index_for_logging = element_node.backend_node_id or 'unknown'
                is_iframe = element_node.tag_name and element_node.tag_name.upper() == 'IFRAME'
                success = await self._scroll_element_container(element_node, pixels)
                if success:
                    self.logger.debug(f'📜 Scrolled element {index_for_logging} container {event.direction} by {event.amount} pixels')
                    if is_iframe:
                        self.logger.debug('🔄 Forcing DOM refresh after iframe scroll')
                        await asyncio.sleep(0.2)
                    return None
            await self._scroll_with_cdp_gesture(pixels)
            self.logger.debug(f'📜 Scrolled {event.direction} by {event.amount} pixels')
            return None
        except Exception as e:
            raise

    async def _check_element_occlusion(self, backend_node_id: int, x: float, y: float, cdp_session) -> bool:
        try:
            session_id = cdp_session.session_id
            target_result = await cdp_session.cdp_client.send.DOM.resolveNode(params={'backendNodeId': backend_node_id}, session_id=session_id)
            if 'object' not in target_result:
                self.logger.debug('Could not resolve target element, assuming occluded')
                return True
            object_id = target_result['object']['objectId']
            target_info_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'objectId': object_id, 'functionDeclaration': '\n\t\t\t\t\tfunction() {\n\t\t\t\t\t\tconst getElementInfo = (el) => {\n\t\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\t\ttagName: el.tagName,\n\t\t\t\t\t\t\t\tid: el.id || \'\',\n\t\t\t\t\t\t\t\tclassName: el.className || \'\',\n\t\t\t\t\t\t\t\ttextContent: (el.textContent || \'\').substring(0, 100)\n\t\t\t\t\t\t\t};\n\t\t\t\t\t\t};\n\n\n\t\t\t\t\t\tconst elementAtPoint = document.elementFromPoint(arguments[0], arguments[1]);\n\t\t\t\t\t\tif (!elementAtPoint) {\n\t\t\t\t\t\t\treturn { targetInfo: getElementInfo(this), isClickable: false };\n\t\t\t\t\t\t}\n\n\n\t\t\t\t\t\t// Simple containment-based clickability logic\n\t\t\t\t\t\tlet isClickable = this === elementAtPoint ||\n\t\t\t\t\t\t\tthis.contains(elementAtPoint) ||\n\t\t\t\t\t\t\telementAtPoint.contains(this);\n\n\t\t\t\t\t\t// Check label-input associations when containment check fails\n\t\t\t\t\t\tif (!isClickable) {\n\t\t\t\t\t\t\tconst target = this;\n\t\t\t\t\t\t\tconst atPoint = elementAtPoint;\n\n\t\t\t\t\t\t\t// Case 1: target is <input>, atPoint is its associated <label> (or child of that label)\n\t\t\t\t\t\t\tif (target.tagName === \'INPUT\' && target.id) {\n\t\t\t\t\t\t\t\tconst escapedId = CSS.escape(target.id);\n\t\t\t\t\t\t\t\tconst assocLabel = document.querySelector(\'label[for="\' + escapedId + \'"]\');\n\t\t\t\t\t\t\t\tif (assocLabel && (assocLabel === atPoint || assocLabel.contains(atPoint))) {\n\t\t\t\t\t\t\t\t\tisClickable = true;\n\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t}\n\n\t\t\t\t\t\t\t// Case 2: target is <input>, atPoint is inside a <label> ancestor that wraps the target\n\t\t\t\t\t\t\tif (!isClickable && target.tagName === \'INPUT\') {\n\t\t\t\t\t\t\t\tlet ancestor = atPoint;\n\t\t\t\t\t\t\t\tfor (let i = 0; i < 3 && ancestor; i++) {\n\t\t\t\t\t\t\t\t\tif (ancestor.tagName === \'LABEL\' && ancestor.contains(target)) {\n\t\t\t\t\t\t\t\t\t\tisClickable = true;\n\t\t\t\t\t\t\t\t\t\tbreak;\n\t\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t\t\tancestor = ancestor.parentElement;\n\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t}\n\n\t\t\t\t\t\t\t// Case 3: target is <label>, atPoint is the associated <input>\n\t\t\t\t\t\t\tif (!isClickable && target.tagName === \'LABEL\') {\n\t\t\t\t\t\t\t\tif (target.htmlFor && atPoint.tagName === \'INPUT\' && atPoint.id === target.htmlFor) {\n\t\t\t\t\t\t\t\t\tisClickable = true;\n\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t\t// Also check if atPoint is an input inside the label\n\t\t\t\t\t\t\t\tif (!isClickable && atPoint.tagName === \'INPUT\' && target.contains(atPoint)) {\n\t\t\t\t\t\t\t\t\tisClickable = true;\n\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t}\n\t\t\t\t\t\t}\n\n\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\ttargetInfo: getElementInfo(this),\n\t\t\t\t\t\t\telementAtPointInfo: getElementInfo(elementAtPoint),\n\t\t\t\t\t\t\tisClickable: isClickable\n\t\t\t\t\t\t};\n\t\t\t\t\t}\n\t\t\t\t\t', 'arguments': [{'value': x}, {'value': y}], 'returnByValue': True}, session_id=session_id)
            if 'result' not in target_info_result or 'value' not in target_info_result['result']:
                self.logger.debug('Could not get target element info, assuming occluded')
                return True
            target_data = target_info_result['result']['value']
            is_clickable = target_data.get('isClickable', False)
            if is_clickable:
                self.logger.debug('Element is clickable (target, contained, or semantically related)')
                return False
            else:
                target_info = target_data.get('targetInfo', {})
                element_at_point_info = target_data.get('elementAtPointInfo', {})
                self.logger.debug(f"Element is occluded. Target: {target_info.get('tagName', 'unknown')} (id={target_info.get('id', 'none')}), ElementAtPoint: {element_at_point_info.get('tagName', 'unknown')} (id={element_at_point_info.get('id', 'none')})")
                return True
        except Exception as e:
            self.logger.debug(f'Occlusion check failed: {e}, assuming not occluded')
            return False

    async def _click_element_node_impl(self, element_node) -> dict | None:
        try:
            tag_name = element_node.tag_name.lower() if element_node.tag_name else ''
            element_type = element_node.attributes.get('type', '').lower() if element_node.attributes else ''
            if tag_name == 'select':
                msg = f'Cannot click on <select> elements. Use dropdown_options(index={element_node.backend_node_id}) action instead.'
                return {'validation_error': msg}
            if tag_name == 'input' and element_type == 'file':
                msg = f'Cannot click on file input element (index={element_node.backend_node_id}). File uploads must be handled using upload_file_to_element action.'
                return {'validation_error': msg}
            cdp_session = await self.browser_session.cdp_client_for_node(element_node)
            session_id = cdp_session.session_id
            backend_node_id = element_node.backend_node_id
            layout_metrics = await cdp_session.cdp_client.send.Page.getLayoutMetrics(session_id=session_id)
            viewport_width = layout_metrics['layoutViewport']['clientWidth']
            viewport_height = layout_metrics['layoutViewport']['clientHeight']
            try:
                await cdp_session.cdp_client.send.DOM.scrollIntoViewIfNeeded(params={'backendNodeId': backend_node_id}, session_id=session_id)
                await asyncio.sleep(0.05)
                self.logger.debug('Scrolled element into view before getting coordinates')
            except Exception as e:
                self.logger.debug(f'Failed to scroll element into view: {e}')
            element_rect = await self.browser_session.get_element_coordinates(backend_node_id, cdp_session)
            quads = []
            if element_rect:
                x, y, w, h = (element_rect.x, element_rect.y, element_rect.width, element_rect.height)
                quads = [[x, y, x + w, y, x + w, y + h, x, y + h]]
                self.logger.debug(f'Got coordinates from unified method: {element_rect.x}, {element_rect.y}, {element_rect.width}x{element_rect.height}')
            if not quads:
                self.logger.warning('Could not get element geometry from any method, falling back to JavaScript click')
                try:
                    result = await cdp_session.cdp_client.send.DOM.resolveNode(params={'backendNodeId': backend_node_id}, session_id=session_id)
                    assert 'object' in result and 'objectId' in result['object'], 'Failed to find DOM element based on backendNodeId, maybe page content changed?'
                    object_id = result['object']['objectId']
                    await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': 'function() { this.click(); }', 'objectId': object_id}, session_id=session_id)
                    await asyncio.sleep(0.05)
                    return None
                except Exception as js_e:
                    self.logger.warning(f'CDP JavaScript click also failed: {js_e}')
                    if 'No node with given id found' in str(js_e):
                        raise Exception('Element with given id not found')
                    else:
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
                self.logger.warning('No visible quad found, using first quad')
            center_x = sum((best_quad[i] for i in range(0, 8, 2))) / 4
            center_y = sum((best_quad[i] for i in range(1, 8, 2))) / 4
            center_x = max(0, min(viewport_width - 1, center_x))
            center_y = max(0, min(viewport_height - 1, center_y))
            is_occluded = await self._check_element_occlusion(backend_node_id, center_x, center_y, cdp_session)
            if is_occluded:
                self.logger.debug('🚫 Element is occluded, falling back to JavaScript click')
                try:
                    result = await cdp_session.cdp_client.send.DOM.resolveNode(params={'backendNodeId': backend_node_id}, session_id=session_id)
                    assert 'object' in result and 'objectId' in result['object'], 'Failed to find DOM element based on backendNodeId'
                    object_id = result['object']['objectId']
                    await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': 'function() { this.click(); }', 'objectId': object_id}, session_id=session_id)
                    await asyncio.sleep(0.05)
                    return None
                except Exception as js_e:
                    self.logger.error(f'JavaScript click fallback failed: {js_e}')
                    raise Exception(f'Failed to click occluded element: {js_e}')
            try:
                self.logger.debug(f'👆 Dragging mouse over element before clicking x: {center_x}px y: {center_y}px ...')
                await cdp_session.cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mouseMoved', 'x': center_x, 'y': center_y}, session_id=session_id)
                await asyncio.sleep(0.05)
                self.logger.debug(f'👆🏾 Clicking x: {center_x}px y: {center_y}px ...')
                try:
                    await asyncio.wait_for(cdp_session.cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mousePressed', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 1}, session_id=session_id), timeout=3.0)
                    await asyncio.sleep(0.08)
                except TimeoutError:
                    self.logger.debug('⏱️ Mouse down timed out (likely due to dialog), continuing...')
                try:
                    await asyncio.wait_for(cdp_session.cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mouseReleased', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 1}, session_id=session_id), timeout=5.0)
                except TimeoutError:
                    self.logger.debug('⏱️ Mouse up timed out (possibly due to lag or dialog popup), continuing...')
                self.logger.debug('🖱️ Clicked successfully using x,y coordinates')
                return {'click_x': center_x, 'click_y': center_y}
            except Exception as e:
                self.logger.warning(f'CDP click failed: {type(e).__name__}: {e}')
                try:
                    result = await cdp_session.cdp_client.send.DOM.resolveNode(params={'backendNodeId': backend_node_id}, session_id=session_id)
                    assert 'object' in result and 'objectId' in result['object'], 'Failed to find DOM element based on backendNodeId, maybe page content changed?'
                    object_id = result['object']['objectId']
                    await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': 'function() { this.click(); }', 'objectId': object_id}, session_id=session_id)
                    await asyncio.sleep(0.1)
                    return None
                except Exception as js_e:
                    self.logger.warning(f'CDP JavaScript click also failed: {js_e}')
                    raise Exception(f'Failed to click element: {e}')
            finally:
                try:
                    cdp_session = await asyncio.wait_for(self.browser_session.get_or_create_cdp_session(focus=True), timeout=3.0)
                    await asyncio.wait_for(cdp_session.cdp_client.send.Runtime.runIfWaitingForDebugger(session_id=cdp_session.session_id), timeout=2.0)
                except TimeoutError:
                    self.logger.debug('⏱️ Refocus after click timed out (page may be blocked by dialog). Continuing...')
                except Exception as e:
                    self.logger.debug(f'⚠️ Refocus error (non-critical): {type(e).__name__}: {e}')
        except URLNotAllowedError as e:
            raise e
        except BrowserError as e:
            raise e
        except Exception as e:
            element_info = f"<{element_node.tag_name or 'unknown'}"
            if element_node.backend_node_id:
                element_info += f' index={element_node.backend_node_id}'
            element_info += '>'
            error_detail = f'Failed to click element {element_info}. The element may not be interactable or visible.'
            if element_node.backend_node_id:
                error_detail += f' If the page changed after navigation/interaction, the index [{element_node.backend_node_id}] may be stale. Get fresh browser state before retrying.'
            raise BrowserError(message=f'Failed to click element: {str(e)}', long_term_memory=error_detail)

    async def _click_on_coordinate(self, coordinate_x: int, coordinate_y: int, force: bool=False) -> dict | None:
        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
            session_id = cdp_session.session_id
            self.logger.debug(f'👆 Moving mouse to ({coordinate_x}, {coordinate_y})...')
            await cdp_session.cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mouseMoved', 'x': coordinate_x, 'y': coordinate_y}, session_id=session_id)
            await asyncio.sleep(0.05)
            self.logger.debug(f'👆🏾 Clicking at ({coordinate_x}, {coordinate_y})...')
            try:
                await asyncio.wait_for(cdp_session.cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mousePressed', 'x': coordinate_x, 'y': coordinate_y, 'button': 'left', 'clickCount': 1}, session_id=session_id), timeout=3.0)
                await asyncio.sleep(0.05)
            except TimeoutError:
                self.logger.debug('⏱️ Mouse down timed out (likely due to dialog), continuing...')
            try:
                await asyncio.wait_for(cdp_session.cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mouseReleased', 'x': coordinate_x, 'y': coordinate_y, 'button': 'left', 'clickCount': 1}, session_id=session_id), timeout=5.0)
            except TimeoutError:
                self.logger.debug('⏱️ Mouse up timed out (possibly due to lag or dialog popup), continuing...')
            self.logger.debug(f'🖱️ Clicked successfully at ({coordinate_x}, {coordinate_y})')
            return {'click_x': coordinate_x, 'click_y': coordinate_y}
        except Exception as e:
            self.logger.error(f'Failed to click at coordinates ({coordinate_x}, {coordinate_y}): {type(e).__name__}: {e}')
            raise BrowserError(message=f'Failed to click at coordinates: {e}', long_term_memory=f'Failed to click at coordinates ({coordinate_x}, {coordinate_y}). The coordinates may be outside viewport or the page may have changed.')

    async def _type_to_page(self, text: str):
        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=True)
            for char in text:
                if char == '\n':
                    await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyDown', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13}, session_id=cdp_session.session_id)
                    await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'char', 'text': '\r'}, session_id=cdp_session.session_id)
                    await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13}, session_id=cdp_session.session_id)
                else:
                    await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyDown', 'key': char}, session_id=cdp_session.session_id)
                    await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'char', 'text': char}, session_id=cdp_session.session_id)
                    await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'key': char}, session_id=cdp_session.session_id)
                await asyncio.sleep(0.01)
        except Exception as e:
            raise Exception(f'Failed to type to page: {str(e)}')

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
        key_codes = {' ': 'Space', '.': 'Period', ',': 'Comma', '-': 'Minus', '_': 'Minus', '@': 'Digit2', '!': 'Digit1', '?': 'Slash', ':': 'Semicolon', ';': 'Semicolon', '(': 'Digit9', ')': 'Digit0', '[': 'BracketLeft', ']': 'BracketRight', '{': 'BracketLeft', '}': 'BracketRight', '/': 'Slash', '\\': 'Backslash', '=': 'Equal', '+': 'Equal', '*': 'Digit8', '&': 'Digit7', '%': 'Digit5', '$': 'Digit4', '#': 'Digit3', '^': 'Digit6', '~': 'Backquote', '`': 'Backquote', "'": 'Quote', '"': 'Quote'}
        if char.isdigit():
            return f'Digit{char}'
        if char.isalpha():
            return f'Key{char.upper()}'
        if char in key_codes:
            return key_codes[char]
        return f'Key{char.upper()}'

    async def _clear_text_field(self, object_id: str, cdp_session) -> bool:
        try:
            self.logger.debug('🧹 Clearing text field using JavaScript value setting')
            clear_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': '\n\t\t\t\t\t\tfunction() {\n\t\t\t\t\t\t\t// Check if it\'s a contenteditable element\n\t\t\t\t\t\t\tconst hasContentEditable = this.getAttribute(\'contenteditable\') === \'true\' ||\n\t\t\t\t\t\t\t\t\t\t\t\t\tthis.getAttribute(\'contenteditable\') === \'\' ||\n\t\t\t\t\t\t\t\t\t\t\t\t\tthis.isContentEditable === true;\n\n\t\t\t\t\t\t\tif (hasContentEditable) {\n\t\t\t\t\t\t\t\t// For contenteditable elements, clear all content\n\t\t\t\t\t\t\t\twhile (this.firstChild) {\n\t\t\t\t\t\t\t\t\tthis.removeChild(this.firstChild);\n\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t\tthis.textContent = "";\n\t\t\t\t\t\t\t\tthis.innerHTML = "";\n\n\t\t\t\t\t\t\t\t// Focus and position cursor at the beginning\n\t\t\t\t\t\t\t\tthis.focus();\n\t\t\t\t\t\t\t\tconst selection = window.getSelection();\n\t\t\t\t\t\t\t\tconst range = document.createRange();\n\t\t\t\t\t\t\t\trange.setStart(this, 0);\n\t\t\t\t\t\t\t\trange.setEnd(this, 0);\n\t\t\t\t\t\t\t\tselection.removeAllRanges();\n\t\t\t\t\t\t\t\tselection.addRange(range);\n\n\t\t\t\t\t\t\t\t// Dispatch events\n\t\t\t\t\t\t\t\tthis.dispatchEvent(new Event("input", { bubbles: true }));\n\t\t\t\t\t\t\t\tthis.dispatchEvent(new Event("change", { bubbles: true }));\n\n\t\t\t\t\t\t\t\treturn {cleared: true, method: \'contenteditable\', finalText: this.textContent};\n\t\t\t\t\t\t\t} else if (this.value !== undefined) {\n\t\t\t\t\t\t\t\t// For regular inputs with value property\n\t\t\t\t\t\t\t\ttry {\n\t\t\t\t\t\t\t\t\tthis.select();\n\t\t\t\t\t\t\t\t} catch (e) {\n\t\t\t\t\t\t\t\t\t// ignore\n\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t\tthis.value = "";\n\t\t\t\t\t\t\t\tthis.dispatchEvent(new Event("input", { bubbles: true }));\n\t\t\t\t\t\t\t\tthis.dispatchEvent(new Event("change", { bubbles: true }));\n\t\t\t\t\t\t\t\treturn {cleared: true, method: \'value\', finalText: this.value};\n\t\t\t\t\t\t\t} else {\n\t\t\t\t\t\t\t\treturn {cleared: false, method: \'none\', error: \'Not a supported input type\'};\n\t\t\t\t\t\t\t}\n\t\t\t\t\t\t}\n\t\t\t\t\t', 'objectId': object_id, 'returnByValue': True}, session_id=cdp_session.session_id)
            clear_info = clear_result.get('result', {}).get('value', {})
            self.logger.debug(f'Clear result: {clear_info}')
            if clear_info.get('cleared'):
                final_text = clear_info.get('finalText', '')
                if not final_text or not final_text.strip():
                    self.logger.debug(f"✅ Text field cleared successfully using {clear_info.get('method')}")
                    return True
                else:
                    self.logger.debug(f'⚠️ JavaScript clear partially failed, field still contains: "{final_text}"')
                    return False
            else:
                self.logger.debug(f"❌ JavaScript clear failed: {clear_info.get('error', 'Unknown error')}")
                return False
        except Exception as e:
            self.logger.debug(f'JavaScript clear failed with exception: {e}')
            return False
        try:
            self.logger.debug('🧹 Fallback: Clearing using triple-click + Delete')
            bounds_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': 'function() { return this.getBoundingClientRect(); }', 'objectId': object_id, 'returnByValue': True}, session_id=cdp_session.session_id)
            if bounds_result.get('result', {}).get('value'):
                bounds = bounds_result['result']['value']
                center_x = bounds['x'] + bounds['width'] / 2
                center_y = bounds['y'] + bounds['height'] / 2
                await cdp_session.cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mousePressed', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 3}, session_id=cdp_session.session_id)
                await cdp_session.cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mouseReleased', 'x': center_x, 'y': center_y, 'button': 'left', 'clickCount': 3}, session_id=cdp_session.session_id)
                await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyDown', 'key': 'Delete', 'code': 'Delete'}, session_id=cdp_session.session_id)
                await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'key': 'Delete', 'code': 'Delete'}, session_id=cdp_session.session_id)
                self.logger.debug('✅ Text field cleared using triple-click + Delete')
                return True
        except Exception as e:
            self.logger.debug(f'Triple-click clear failed: {e}')
        try:
            import platform
            is_macos = platform.system() == 'Darwin'
            select_all_modifier = 4 if is_macos else 2
            modifier_name = 'Cmd' if is_macos else 'Ctrl'
            self.logger.debug(f'🧹 Last resort: Clearing using {modifier_name}+A + Backspace')
            await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyDown', 'key': 'a', 'code': 'KeyA', 'modifiers': select_all_modifier}, session_id=cdp_session.session_id)
            await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'key': 'a', 'code': 'KeyA', 'modifiers': select_all_modifier}, session_id=cdp_session.session_id)
            await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyDown', 'key': 'Backspace', 'code': 'Backspace'}, session_id=cdp_session.session_id)
            await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'key': 'Backspace', 'code': 'Backspace'}, session_id=cdp_session.session_id)
            self.logger.debug('✅ Text field cleared using keyboard shortcuts')
            return True
        except Exception as e:
            self.logger.debug(f'All clearing strategies failed: {e}')
            return False

    async def _focus_element_simple(self, backend_node_id: int, object_id: str, cdp_session, input_coordinates: dict | None=None) -> bool:
        try:
            result = await cdp_session.cdp_client.send.DOM.focus(params={'backendNodeId': backend_node_id}, session_id=cdp_session.session_id)
            self.logger.debug(f'Element focused using CDP DOM.focus (result: {result})')
            return True
        except Exception as e:
            self.logger.debug(f'❌ CDP DOM.focus threw exception: {type(e).__name__}: {e}')
        if input_coordinates and 'input_x' in input_coordinates and ('input_y' in input_coordinates):
            try:
                click_x = input_coordinates['input_x']
                click_y = input_coordinates['input_y']
                self.logger.debug(f'🎯 Attempting click-to-focus at ({click_x:.1f}, {click_y:.1f})')
                await cdp_session.cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mousePressed', 'x': click_x, 'y': click_y, 'button': 'left', 'clickCount': 1}, session_id=cdp_session.session_id)
                await cdp_session.cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mouseReleased', 'x': click_x, 'y': click_y, 'button': 'left', 'clickCount': 1}, session_id=cdp_session.session_id)
                self.logger.debug('✅ Element focused using click method')
                return True
            except Exception as e:
                self.logger.debug(f'Click focus failed: {e}')
        self.logger.debug('Focus strategies failed, will attempt typing anyway')
        return False

    def _requires_direct_value_assignment(self, element_node: EnhancedDOMTreeNode) -> bool:
        if not element_node.tag_name or not element_node.attributes:
            return False
        tag_name = element_node.tag_name.lower()
        if tag_name == 'input':
            input_type = element_node.attributes.get('type', '').lower()
            if input_type in {'date', 'time', 'datetime-local', 'month', 'week', 'color', 'range'}:
                return True
            if input_type in {'text', ''}:
                class_attr = element_node.attributes.get('class', '').lower()
                if any((indicator in class_attr for indicator in ['datepicker', 'daterangepicker', 'datetimepicker', 'bootstrap-datepicker'])):
                    return True
                if any((attr in element_node.attributes for attr in ['data-datepicker', 'data-date-format', 'data-provide'])):
                    return True
        return False

    async def _set_value_directly(self, element_node: EnhancedDOMTreeNode, text: str, object_id: str, cdp_session) -> None:
        try:
            set_value_js = f"\n\t\t\tfunction() {{\n\t\t\t\t// Store old value for comparison\n\t\t\t\tconst oldValue = this.value;\n\n\t\t\t\t// REACT-COMPATIBLE VALUE SETTING:\n\t\t\t\t// React uses Object.getOwnPropertyDescriptor to track input changes\n\t\t\t\t// We need to use the native setter to bypass React's tracking and then trigger events\n\t\t\t\tconst nativeInputValueSetter = Object.getOwnPropertyDescriptor(\n\t\t\t\t\twindow.HTMLInputElement.prototype,\n\t\t\t\t\t'value'\n\t\t\t\t).set;\n\n\t\t\t\t// Set the value using the native setter (bypasses React's control)\n\t\t\t\tnativeInputValueSetter.call(this, {json.dumps(text)});\n\n\t\t\t\t// Dispatch comprehensive events to ensure all frameworks detect the change\n\t\t\t\t// Order matters: focus -> input -> change -> blur (mimics user interaction)\n\n\t\t\t\t// 1. Focus event (in case element isn't focused)\n\t\t\t\tthis.dispatchEvent(new FocusEvent('focus', {{ bubbles: true }}));\n\n\t\t\t\t// 2. Input event (CRITICAL for React onChange)\n\t\t\t\t// React listens to 'input' events on the document and checks for value changes\n\t\t\t\tconst inputEvent = new Event('input', {{ bubbles: true, cancelable: true }});\n\t\t\t\tthis.dispatchEvent(inputEvent);\n\n\t\t\t\t// 3. Change event (for form handling, traditional listeners)\n\t\t\t\tconst changeEvent = new Event('change', {{ bubbles: true, cancelable: true }});\n\t\t\t\tthis.dispatchEvent(changeEvent);\n\n\t\t\t\t// 4. Blur event (triggers final validation in some libraries)\n\t\t\t\tthis.dispatchEvent(new FocusEvent('blur', {{ bubbles: true }}));\n\n\t\t\t\t// 5. jQuery-specific events (if jQuery is present)\n\t\t\t\tif (typeof jQuery !== 'undefined' && jQuery.fn) {{\n\t\t\t\t\ttry {{\n\t\t\t\t\t\tjQuery(this).trigger('change');\n\t\t\t\t\t\t// Trigger datepicker-specific events if it's a datepicker\n\t\t\t\t\t\tif (jQuery(this).data('datepicker')) {{\n\t\t\t\t\t\t\tjQuery(this).datepicker('update');\n\t\t\t\t\t\t}}\n\t\t\t\t\t}} catch (e) {{\n\t\t\t\t\t\t// jQuery not available or error, continue anyway\n\t\t\t\t\t}}\n\t\t\t\t}}\n\n\t\t\t\treturn this.value;\n\t\t\t}}\n\t\t\t"
            result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'objectId': object_id, 'functionDeclaration': set_value_js, 'returnByValue': True}, session_id=cdp_session.session_id)
            if 'result' in result and 'value' in result['result']:
                actual_value = result['result']['value']
                self.logger.debug(f'✅ Value set directly to: "{actual_value}"')
            else:
                self.logger.warning('⚠️ Could not verify value was set correctly')
        except Exception as e:
            self.logger.error(f'❌ Failed to set value directly: {e}')
            raise

    async def _input_text_element_node_impl(self, element_node: EnhancedDOMTreeNode, text: str, clear: bool=True, is_sensitive: bool=False) -> dict | None:
        try:
            cdp_client = self.browser_session.cdp_client
            cdp_session = await self.browser_session.cdp_client_for_node(element_node)
            backend_node_id = element_node.backend_node_id
            input_coordinates = None
            try:
                await cdp_session.cdp_client.send.DOM.scrollIntoViewIfNeeded(params={'backendNodeId': backend_node_id}, session_id=cdp_session.session_id)
                await asyncio.sleep(0.01)
            except Exception as e:
                error_str = str(e)
                if 'Node is detached from document' in error_str or 'detached from document' in error_str:
                    self.logger.debug(f'Element node temporarily detached during scroll (common with shadow DOM), continuing: {element_node}')
                else:
                    self.logger.debug(f'Failed to scroll element {element_node} into view before typing: {type(e).__name__}: {e}')
            result = await cdp_client.send.DOM.resolveNode(params={'backendNodeId': backend_node_id}, session_id=cdp_session.session_id)
            assert 'object' in result and 'objectId' in result['object'], 'Failed to find DOM element based on backendNodeId, maybe page content changed?'
            object_id = result['object']['objectId']
            coords = await self.browser_session.get_element_coordinates(backend_node_id, cdp_session)
            if coords:
                center_x = coords.x + coords.width / 2
                center_y = coords.y + coords.height / 2
                is_occluded = await self._check_element_occlusion(backend_node_id, center_x, center_y, cdp_session)
                if is_occluded:
                    self.logger.debug('🚫 Input element is occluded, skipping coordinate-based focus')
                    input_coordinates = None
                else:
                    input_coordinates = {'input_x': center_x, 'input_y': center_y}
                    self.logger.debug(f'Using unified coordinates: x={center_x:.1f}, y={center_y:.1f}')
            else:
                input_coordinates = None
                self.logger.debug('No coordinates found for element')
            if not object_id:
                raise ValueError('Could not get object_id for element')
            focused_successfully = await self._focus_element_simple(backend_node_id=backend_node_id, object_id=object_id, cdp_session=cdp_session, input_coordinates=input_coordinates)
            requires_direct_assignment = self._requires_direct_value_assignment(element_node)
            if requires_direct_assignment:
                self.logger.debug(f"🎯 Element type={element_node.attributes.get('type')} requires direct value assignment, setting value directly")
                await self._set_value_directly(element_node, text, object_id, cdp_session)
                return input_coordinates
            if clear:
                cleared_successfully = await self._clear_text_field(object_id=object_id, cdp_session=cdp_session)
                if not cleared_successfully:
                    self.logger.warning('⚠️ Text field clearing failed, typing may append to existing text')
            if is_sensitive:
                self.logger.debug('🎯 Typing <sensitive> character by character')
            else:
                self.logger.debug(f'🎯 Typing text character by character: "{text}"')
            _attrs = element_node.attributes or {}
            _is_contenteditable = _attrs.get('contenteditable') in ('true', '') or (_attrs.get('role') == 'textbox' and element_node.tag_name not in ('input', 'textarea'))
            _check_first_char = _is_contenteditable and len(text) > 0 and clear
            _first_char = text[0] if _check_first_char else None
            for i, char in enumerate(text):
                if char == '\n':
                    await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyDown', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13}, session_id=cdp_session.session_id)
                    await asyncio.sleep(0.001)
                    await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'char', 'text': '\r', 'key': 'Enter'}, session_id=cdp_session.session_id)
                    await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13}, session_id=cdp_session.session_id)
                else:
                    modifiers, vk_code, base_key = self._get_char_modifiers_and_vk(char)
                    key_code = self._get_key_code_for_char(base_key)
                    await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyDown', 'key': base_key, 'code': key_code, 'modifiers': modifiers, 'windowsVirtualKeyCode': vk_code}, session_id=cdp_session.session_id)
                    await asyncio.sleep(0.005)
                    await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'char', 'text': char, 'key': char}, session_id=cdp_session.session_id)
                    await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'key': base_key, 'code': key_code, 'modifiers': modifiers, 'windowsVirtualKeyCode': vk_code}, session_id=cdp_session.session_id)
                if i == 0 and _check_first_char and _first_char:
                    check_result = await cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': 'document.activeElement.textContent'}, session_id=cdp_session.session_id)
                    content = check_result.get('result', {}).get('value', '')
                    if _first_char not in content:
                        self.logger.debug(f'🎯 First char "{_first_char}" was dropped (leaf-start bug), retyping')
                        modifiers, vk_code, base_key = self._get_char_modifiers_and_vk(_first_char)
                        key_code = self._get_key_code_for_char(base_key)
                        await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyDown', 'key': base_key, 'code': key_code, 'modifiers': modifiers, 'windowsVirtualKeyCode': vk_code}, session_id=cdp_session.session_id)
                        await asyncio.sleep(0.005)
                        await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'char', 'text': _first_char, 'key': _first_char}, session_id=cdp_session.session_id)
                        await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'key': base_key, 'code': key_code, 'modifiers': modifiers, 'windowsVirtualKeyCode': vk_code}, session_id=cdp_session.session_id)
                await asyncio.sleep(0.001)
            await self._trigger_framework_events(object_id=object_id, cdp_session=cdp_session)
            if not is_sensitive:
                try:
                    await asyncio.sleep(0.05)
                    readback_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'objectId': object_id, 'functionDeclaration': 'function() { return this.value !== undefined ? this.value : this.textContent; }', 'returnByValue': True}, session_id=cdp_session.session_id)
                    actual_value = readback_result.get('result', {}).get('value')
                    if actual_value is not None:
                        if input_coordinates is None:
                            input_coordinates = {}
                        input_coordinates['actual_value'] = actual_value
                except Exception as e:
                    self.logger.debug(f'Value readback failed (non-critical): {e}')
            if clear and (not is_sensitive) and input_coordinates and ('actual_value' in input_coordinates):
                actual_value = input_coordinates['actual_value']
                if isinstance(actual_value, str) and actual_value != text and (len(actual_value) > len(text)) and (actual_value.endswith(text) or actual_value.startswith(text)):
                    self.logger.info(f'🔄 Concatenation detected: got "{actual_value}", expected "{text}" — auto-retrying')
                    try:
                        retry_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'objectId': object_id, 'functionDeclaration': "\n\t\t\t\t\t\t\t\t\tfunction(newValue) {\n\t\t\t\t\t\t\t\t\t\tif (this.value !== undefined) {\n\t\t\t\t\t\t\t\t\t\t\tvar desc = Object.getOwnPropertyDescriptor(\n\t\t\t\t\t\t\t\t\t\t\t\tHTMLInputElement.prototype, 'value'\n\t\t\t\t\t\t\t\t\t\t\t) || Object.getOwnPropertyDescriptor(\n\t\t\t\t\t\t\t\t\t\t\t\tHTMLTextAreaElement.prototype, 'value'\n\t\t\t\t\t\t\t\t\t\t\t);\n\t\t\t\t\t\t\t\t\t\t\tif (desc && desc.set) {\n\t\t\t\t\t\t\t\t\t\t\t\tdesc.set.call(this, newValue);\n\t\t\t\t\t\t\t\t\t\t\t} else {\n\t\t\t\t\t\t\t\t\t\t\t\tthis.value = newValue;\n\t\t\t\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t\t\t\t} else if (this.isContentEditable) {\n\t\t\t\t\t\t\t\t\t\t\tthis.textContent = newValue;\n\t\t\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t\t\t\tthis.dispatchEvent(new Event('input', { bubbles: true }));\n\t\t\t\t\t\t\t\t\t\tthis.dispatchEvent(new Event('change', { bubbles: true }));\n\t\t\t\t\t\t\t\t\t\treturn this.value !== undefined ? this.value : this.textContent;\n\t\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t\t", 'arguments': [{'value': text}], 'returnByValue': True}, session_id=cdp_session.session_id)
                        retry_value = retry_result.get('result', {}).get('value')
                        if retry_value is not None:
                            input_coordinates['actual_value'] = retry_value
                            if retry_value == text:
                                self.logger.info('✅ Auto-retry fixed concatenation')
                            else:
                                self.logger.warning(f'⚠️ Auto-retry value still differs: "{retry_value}"')
                    except Exception as e:
                        self.logger.debug(f'Auto-retry failed (non-critical): {e}')
            return input_coordinates
        except Exception as e:
            self.logger.error(f'Failed to input text via CDP: {type(e).__name__}: {e}')
            raise BrowserError(f'Failed to input text into element: {repr(element_node)}')

    async def _trigger_framework_events(self, object_id: str, cdp_session) -> None:
        try:
            framework_events_script = "\n\t\t\tfunction() {\n\t\t\t\t// Find the target element (available as 'this' when using objectId)\n\t\t\t\tconst element = this;\n\t\t\t\tif (!element) return false;\n\n\t\t\t\t// Ensure element is focused\n\t\t\t\telement.focus();\n\n\t\t\t\t// Comprehensive event sequence for maximum framework compatibility\n\t\t\t\tconst events = [\n\t\t\t\t\t// Input event - primary event for React controlled components\n\t\t\t\t\t{ type: 'input', bubbles: true, cancelable: true },\n\t\t\t\t\t// Change event - important for form validation and Vue v-model\n\t\t\t\t\t{ type: 'change', bubbles: true, cancelable: true },\n\t\t\t\t\t// Blur event - triggers validation in many frameworks\n\t\t\t\t\t{ type: 'blur', bubbles: true, cancelable: true }\n\t\t\t\t];\n\n\t\t\t\tlet success = true;\n\n\t\t\t\tevents.forEach(eventConfig => {\n\t\t\t\t\ttry {\n\t\t\t\t\t\tconst event = new Event(eventConfig.type, {\n\t\t\t\t\t\t\tbubbles: eventConfig.bubbles,\n\t\t\t\t\t\t\tcancelable: eventConfig.cancelable\n\t\t\t\t\t\t});\n\n\t\t\t\t\t\t// Special handling for InputEvent (more specific than Event)\n\t\t\t\t\t\tif (eventConfig.type === 'input') {\n\t\t\t\t\t\t\tconst inputEvent = new InputEvent('input', {\n\t\t\t\t\t\t\t\tbubbles: true,\n\t\t\t\t\t\t\t\tcancelable: true,\n\t\t\t\t\t\t\t\tdata: element.value,\n\t\t\t\t\t\t\t\tinputType: 'insertText'\n\t\t\t\t\t\t\t});\n\t\t\t\t\t\t\telement.dispatchEvent(inputEvent);\n\t\t\t\t\t\t} else {\n\t\t\t\t\t\t\telement.dispatchEvent(event);\n\t\t\t\t\t\t}\n\t\t\t\t\t} catch (e) {\n\t\t\t\t\t\tsuccess = false;\n\t\t\t\t\t\tconsole.warn('Framework event dispatch failed:', eventConfig.type, e);\n\t\t\t\t\t}\n\t\t\t\t});\n\n\t\t\t\t// Special React synthetic event handling\n\t\t\t\t// React uses internal fiber properties for event system\n\t\t\t\tif (element._reactInternalFiber || element._reactInternalInstance || element.__reactInternalInstance) {\n\t\t\t\t\ttry {\n\t\t\t\t\t\t// Trigger React's synthetic event system\n\t\t\t\t\t\tconst syntheticInputEvent = new InputEvent('input', {\n\t\t\t\t\t\t\tbubbles: true,\n\t\t\t\t\t\t\tcancelable: true,\n\t\t\t\t\t\t\tdata: element.value\n\t\t\t\t\t\t});\n\n\t\t\t\t\t\t// Force React to process this as a synthetic event\n\t\t\t\t\t\tObject.defineProperty(syntheticInputEvent, 'isTrusted', { value: true });\n\t\t\t\t\t\telement.dispatchEvent(syntheticInputEvent);\n\t\t\t\t\t} catch (e) {\n\t\t\t\t\t\tconsole.warn('React synthetic event failed:', e);\n\t\t\t\t\t}\n\t\t\t\t}\n\n\t\t\t\t// Special Vue reactivity trigger\n\t\t\t\t// Vue uses __vueParentComponent or __vue__ for component access\n\t\t\t\tif (element.__vue__ || element._vnode || element.__vueParentComponent) {\n\t\t\t\t\ttry {\n\t\t\t\t\t\t// Vue often needs explicit input event with proper timing\n\t\t\t\t\t\tconst vueEvent = new Event('input', { bubbles: true });\n\t\t\t\t\t\tsetTimeout(() => element.dispatchEvent(vueEvent), 0);\n\t\t\t\t\t} catch (e) {\n\t\t\t\t\t\tconsole.warn('Vue reactivity trigger failed:', e);\n\t\t\t\t\t}\n\t\t\t\t}\n\n\t\t\t\treturn success;\n\t\t\t}\n\t\t\t"
            result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'objectId': object_id, 'functionDeclaration': framework_events_script, 'returnByValue': True}, session_id=cdp_session.session_id)
            success = result.get('result', {}).get('value', False)
            if success:
                self.logger.debug('✅ Framework events triggered successfully')
            else:
                self.logger.warning('⚠️ Failed to trigger framework events')
        except Exception as e:
            self.logger.warning(f'⚠️ Failed to trigger framework events: {type(e).__name__}: {e}')

    async def _scroll_with_cdp_gesture(self, pixels: int) -> bool:
        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
            cdp_client = cdp_session.cdp_client
            session_id = cdp_session.session_id
            if self.browser_session._original_viewport_size:
                viewport_width, viewport_height = self.browser_session._original_viewport_size
            else:
                layout_metrics = await cdp_client.send.Page.getLayoutMetrics(session_id=session_id)
                viewport_width = layout_metrics['layoutViewport']['clientWidth']
                viewport_height = layout_metrics['layoutViewport']['clientHeight']
            center_x = viewport_width / 2
            center_y = viewport_height / 2
            y_distance = -pixels
            await cdp_client.send.Input.synthesizeScrollGesture(params={'x': center_x, 'y': center_y, 'xDistance': 0, 'yDistance': y_distance, 'speed': 50000}, session_id=session_id)
            self.logger.debug(f'📄 Scrolled via CDP gesture: {pixels}px')
            return True
        except Exception as e:
            self.logger.debug(f'CDP gesture scroll failed ({type(e).__name__}: {e}), falling back to JS')
            return False

    async def _scroll_element_container(self, element_node, pixels: int) -> bool:
        try:
            cdp_session = await self.browser_session.cdp_client_for_node(element_node)
            if element_node.tag_name and element_node.tag_name.upper() == 'IFRAME':
                backend_node_id = element_node.backend_node_id
                result = await cdp_session.cdp_client.send.DOM.resolveNode(params={'backendNodeId': backend_node_id}, session_id=cdp_session.session_id)
                if 'object' in result and 'objectId' in result['object']:
                    object_id = result['object']['objectId']
                    scroll_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': f"\n\t\t\t\t\t\t\t\tfunction() {{\n\t\t\t\t\t\t\t\t\ttry {{\n\t\t\t\t\t\t\t\t\t\tconst doc = this.contentDocument || this.contentWindow.document;\n\t\t\t\t\t\t\t\t\t\tif (doc) {{\n\t\t\t\t\t\t\t\t\t\t\tconst scrollElement = doc.documentElement || doc.body;\n\t\t\t\t\t\t\t\t\t\t\tif (scrollElement) {{\n\t\t\t\t\t\t\t\t\t\t\t\tconst oldScrollTop = scrollElement.scrollTop;\n\t\t\t\t\t\t\t\t\t\t\t\tscrollElement.scrollTop += {pixels};\n\t\t\t\t\t\t\t\t\t\t\t\tconst newScrollTop = scrollElement.scrollTop;\n\t\t\t\t\t\t\t\t\t\t\t\treturn {{\n\t\t\t\t\t\t\t\t\t\t\t\t\tsuccess: true,\n\t\t\t\t\t\t\t\t\t\t\t\t\toldScrollTop: oldScrollTop,\n\t\t\t\t\t\t\t\t\t\t\t\t\tnewScrollTop: newScrollTop,\n\t\t\t\t\t\t\t\t\t\t\t\t\tscrolled: newScrollTop - oldScrollTop\n\t\t\t\t\t\t\t\t\t\t\t\t}};\n\t\t\t\t\t\t\t\t\t\t\t}}\n\t\t\t\t\t\t\t\t\t\t}}\n\t\t\t\t\t\t\t\t\t\treturn {{success: false, error: 'Could not access iframe content'}};\n\t\t\t\t\t\t\t\t\t}} catch (e) {{\n\t\t\t\t\t\t\t\t\t\treturn {{success: false, error: e.toString()}};\n\t\t\t\t\t\t\t\t\t}}\n\t\t\t\t\t\t\t\t}}\n\t\t\t\t\t\t\t", 'objectId': object_id, 'returnByValue': True}, session_id=cdp_session.session_id)
                    if scroll_result and 'result' in scroll_result and ('value' in scroll_result['result']):
                        result_value = scroll_result['result']['value']
                        if result_value.get('success'):
                            self.logger.debug(f"Successfully scrolled iframe content by {result_value.get('scrolled', 0)}px")
                            return True
                        else:
                            self.logger.debug(f"Failed to scroll iframe: {result_value.get('error', 'Unknown error')}")
            backend_node_id = element_node.backend_node_id
            box_model = await cdp_session.cdp_client.send.DOM.getBoxModel(params={'backendNodeId': backend_node_id}, session_id=cdp_session.session_id)
            content_quad = box_model['model']['content']
            center_x = (content_quad[0] + content_quad[2] + content_quad[4] + content_quad[6]) / 4
            center_y = (content_quad[1] + content_quad[3] + content_quad[5] + content_quad[7]) / 4
            await cdp_session.cdp_client.send.Input.dispatchMouseEvent(params={'type': 'mouseWheel', 'x': center_x, 'y': center_y, 'deltaX': 0, 'deltaY': pixels}, session_id=cdp_session.session_id)
            return True
        except Exception as e:
            self.logger.debug(f'Failed to scroll element container via CDP: {e}')
            return False

    async def _get_session_id_for_element(self, element_node: EnhancedDOMTreeNode) -> str | None:
        if element_node.frame_id:
            try:
                all_targets = self.browser_session.session_manager.get_all_targets()
                for target_id, target in all_targets.items():
                    if target.target_type == 'iframe' and element_node.frame_id in str(target_id):
                        temp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
                        return temp_session.session_id
                self.logger.debug(f'Frame {element_node.frame_id} not found in targets, using main session')
            except Exception as e:
                self.logger.debug(f'Error getting frame session: {e}, using main session')
        cdp_session = await self.browser_session.get_or_create_cdp_session()
        return cdp_session.session_id

    async def on_GoBackEvent(self, event: GoBackEvent) -> None:
        cdp_session = await self.browser_session.get_or_create_cdp_session()
        try:
            history = await cdp_session.cdp_client.send.Page.getNavigationHistory(session_id=cdp_session.session_id)
            current_index = history['currentIndex']
            entries = history['entries']
            if current_index <= 0:
                self.logger.warning('⚠️ Cannot go back - no previous entry in history')
                return
            previous_entry_id = entries[current_index - 1]['id']
            await cdp_session.cdp_client.send.Page.navigateToHistoryEntry(params={'entryId': previous_entry_id}, session_id=cdp_session.session_id)
            await asyncio.sleep(0.5)
            self.logger.info(f"🔙 Navigated back to {entries[current_index - 1]['url']}")
        except Exception as e:
            raise

    async def on_GoForwardEvent(self, event: GoForwardEvent) -> None:
        cdp_session = await self.browser_session.get_or_create_cdp_session()
        try:
            history = await cdp_session.cdp_client.send.Page.getNavigationHistory(session_id=cdp_session.session_id)
            current_index = history['currentIndex']
            entries = history['entries']
            if current_index >= len(entries) - 1:
                self.logger.warning('⚠️ Cannot go forward - no next entry in history')
                return
            next_entry_id = entries[current_index + 1]['id']
            await cdp_session.cdp_client.send.Page.navigateToHistoryEntry(params={'entryId': next_entry_id}, session_id=cdp_session.session_id)
            await asyncio.sleep(0.5)
            self.logger.info(f"🔜 Navigated forward to {entries[current_index + 1]['url']}")
        except Exception as e:
            raise

    async def on_RefreshEvent(self, event: RefreshEvent) -> None:
        cdp_session = await self.browser_session.get_or_create_cdp_session()
        try:
            await cdp_session.cdp_client.send.Page.reload(session_id=cdp_session.session_id)
            await asyncio.sleep(1.0)
            self.logger.info('🔄 Target refreshed')
        except Exception as e:
            raise

    @observe_debug(ignore_input=True, ignore_output=True, name='wait_event_handler')
    async def on_WaitEvent(self, event: WaitEvent) -> None:
        try:
            actual_seconds = min(max(event.seconds, 0), event.max_seconds)
            if actual_seconds != event.seconds:
                self.logger.info(f'🕒 Waiting for {actual_seconds} seconds (capped from {event.seconds}s)')
            else:
                self.logger.info(f'🕒 Waiting for {actual_seconds} seconds')
            await asyncio.sleep(actual_seconds)
        except Exception as e:
            raise

    async def _dispatch_key_event(self, cdp_session, event_type: str, key: str, modifiers: int=0) -> None:
        code, vk_code = get_key_info(key)
        params: DispatchKeyEventParameters = {'type': event_type, 'key': key, 'code': code}
        if modifiers:
            params['modifiers'] = modifiers
        if vk_code is not None:
            params['windowsVirtualKeyCode'] = vk_code
        await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params=params, session_id=cdp_session.session_id)

    async def on_SendKeysEvent(self, event: SendKeysEvent) -> None:
        cdp_session = await self.browser_session.get_or_create_cdp_session(focus=True)
        try:
            key_aliases = {'ctrl': 'Control', 'control': 'Control', 'alt': 'Alt', 'option': 'Alt', 'meta': 'Meta', 'cmd': 'Meta', 'command': 'Meta', 'shift': 'Shift', 'enter': 'Enter', 'return': 'Enter', 'tab': 'Tab', 'delete': 'Delete', 'backspace': 'Backspace', 'escape': 'Escape', 'esc': 'Escape', 'space': ' ', 'up': 'ArrowUp', 'down': 'ArrowDown', 'left': 'ArrowLeft', 'right': 'ArrowRight', 'pageup': 'PageUp', 'pagedown': 'PageDown', 'home': 'Home', 'end': 'End'}
            keys = event.keys
            if '+' in keys:
                parts = keys.split('+')
                normalized_parts = []
                for part in parts:
                    part_lower = part.strip().lower()
                    normalized = key_aliases.get(part_lower, part)
                    normalized_parts.append(normalized)
                normalized_keys = '+'.join(normalized_parts)
            else:
                keys_lower = keys.strip().lower()
                normalized_keys = key_aliases.get(keys_lower, keys)
            if '+' in normalized_keys:
                parts = normalized_keys.split('+')
                modifiers = parts[:-1]
                main_key = parts[-1]
                modifier_value = 0
                modifier_map = {'Alt': 1, 'Control': 2, 'Meta': 4, 'Shift': 8}
                for mod in modifiers:
                    modifier_value |= modifier_map.get(mod, 0)
                for mod in modifiers:
                    await self._dispatch_key_event(cdp_session, 'keyDown', mod)
                await self._dispatch_key_event(cdp_session, 'keyDown', main_key, modifier_value)
                await self._dispatch_key_event(cdp_session, 'keyUp', main_key, modifier_value)
                for mod in reversed(modifiers):
                    await self._dispatch_key_event(cdp_session, 'keyUp', mod)
            else:
                special_keys = {'Enter', 'Tab', 'Delete', 'Backspace', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'PageUp', 'PageDown', 'Home', 'End', 'Control', 'Alt', 'Meta', 'Shift', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12'}
                if normalized_keys in special_keys:
                    await self._dispatch_key_event(cdp_session, 'keyDown', normalized_keys)
                    if normalized_keys == 'Enter':
                        await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'char', 'text': '\r', 'key': 'Enter'}, session_id=cdp_session.session_id)
                    await self._dispatch_key_event(cdp_session, 'keyUp', normalized_keys)
                else:
                    for char in normalized_keys:
                        if char in ('\n', '\r'):
                            await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'rawKeyDown', 'windowsVirtualKeyCode': 13, 'unmodifiedText': '\r', 'text': '\r'}, session_id=cdp_session.session_id)
                            await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'char', 'windowsVirtualKeyCode': 13, 'unmodifiedText': '\r', 'text': '\r'}, session_id=cdp_session.session_id)
                            await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'windowsVirtualKeyCode': 13, 'unmodifiedText': '\r', 'text': '\r'}, session_id=cdp_session.session_id)
                            continue
                        modifiers, vk_code, base_key = self._get_char_modifiers_and_vk(char)
                        key_code = self._get_key_code_for_char(base_key)
                        await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyDown', 'key': base_key, 'code': key_code, 'modifiers': modifiers, 'windowsVirtualKeyCode': vk_code}, session_id=cdp_session.session_id)
                        await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'char', 'text': char, 'key': char}, session_id=cdp_session.session_id)
                        await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params={'type': 'keyUp', 'key': base_key, 'code': key_code, 'modifiers': modifiers, 'windowsVirtualKeyCode': vk_code}, session_id=cdp_session.session_id)
                        await asyncio.sleep(0.01)
            self.logger.info(f'⌨️ Sent keys: {event.keys}')
            if 'enter' in event.keys.lower() or 'return' in event.keys.lower():
                await asyncio.sleep(0.1)
        except Exception as e:
            raise

    async def on_UploadFileEvent(self, event: UploadFileEvent) -> None:
        try:
            element_node = event.node
            index_for_logging = element_node.backend_node_id or 'unknown'
            if not self.browser_session.is_file_input(element_node):
                msg = f'Upload failed - element {index_for_logging} is not a file input.'
                raise BrowserError(message=msg, long_term_memory=msg)
            cdp_client = self.browser_session.cdp_client
            session_id = await self._get_session_id_for_element(element_node)
            if os.path.exists(event.file_path):
                file_size = os.path.getsize(event.file_path)
                if file_size == 0:
                    msg = f'Upload failed - file {event.file_path} is empty (0 bytes).'
                    raise BrowserError(message=msg, long_term_memory=msg)
                self.logger.debug(f'📎 File {event.file_path} validated ({file_size} bytes)')
            backend_node_id = element_node.backend_node_id
            await cdp_client.send.DOM.setFileInputFiles(params={'files': [event.file_path], 'backendNodeId': backend_node_id}, session_id=session_id)
            self.logger.info(f'📎 Uploaded file {event.file_path} to element {index_for_logging}')
        except Exception as e:
            raise

    async def on_ScrollToTextEvent(self, event: ScrollToTextEvent) -> None:
        cdp_session = await self.browser_session.get_or_create_cdp_session()
        cdp_client = cdp_session.cdp_client
        session_id = cdp_session.session_id
        await cdp_client.send.DOM.enable(session_id=session_id)
        doc = await cdp_client.send.DOM.getDocument(params={'depth': -1}, session_id=session_id)
        root_node_id = doc['root']['nodeId']
        search_queries = [f'//*[contains(text(), "{event.text}")]', f'//*[contains(., "{event.text}")]', f'//*[@*[contains(., "{event.text}")]]']
        found = False
        for query in search_queries:
            try:
                search_result = await cdp_client.send.DOM.performSearch(params={'query': query}, session_id=session_id)
                search_id = search_result['searchId']
                result_count = search_result['resultCount']
                if result_count > 0:
                    node_ids = await cdp_client.send.DOM.getSearchResults(params={'searchId': search_id, 'fromIndex': 0, 'toIndex': 1}, session_id=session_id)
                    if node_ids['nodeIds']:
                        node_id = node_ids['nodeIds'][0]
                        await cdp_client.send.DOM.scrollIntoViewIfNeeded(params={'nodeId': node_id}, session_id=session_id)
                        found = True
                        self.logger.debug(f'📜 Scrolled to text: "{event.text}"')
                        break
                await cdp_client.send.DOM.discardSearchResults(params={'searchId': search_id}, session_id=session_id)
            except Exception as e:
                self.logger.debug(f'Search query failed: {query}, error: {e}')
                continue
        if not found:
            js_result = await cdp_client.send.Runtime.evaluate(params={'expression': f'''\n\t\t\t\t\t\t\t(() => {{\n\t\t\t\t\t\t\t\tconst walker = document.createTreeWalker(\n\t\t\t\t\t\t\t\t\tdocument.body,\n\t\t\t\t\t\t\t\t\tNodeFilter.SHOW_TEXT,\n\t\t\t\t\t\t\t\t\tnull,\n\t\t\t\t\t\t\t\t\tfalse\n\t\t\t\t\t\t\t\t);\n\t\t\t\t\t\t\t\tlet node;\n\t\t\t\t\t\t\t\twhile (node = walker.nextNode()) {{\n\t\t\t\t\t\t\t\t\tif (node.textContent.includes("{event.text}")) {{\n\t\t\t\t\t\t\t\t\t\tnode.parentElement.scrollIntoView({{behavior: 'smooth', block: 'center'}});\n\t\t\t\t\t\t\t\t\t\treturn true;\n\t\t\t\t\t\t\t\t\t}}\n\t\t\t\t\t\t\t\t}}\n\t\t\t\t\t\t\t\treturn false;\n\t\t\t\t\t\t\t}})()\n\t\t\t\t\t\t'''}, session_id=session_id)
            if js_result.get('result', {}).get('value'):
                self.logger.debug(f'📜 Scrolled to text: "{event.text}" (via JS)')
                return None
            else:
                self.logger.warning(f'⚠️ Text not found: "{event.text}"')
                raise BrowserError(f'Text not found: "{event.text}"', details={'text': event.text})
        if found:
            return None
        else:
            raise BrowserError(f'Text not found: "{event.text}"', details={'text': event.text})

    async def on_GetDropdownOptionsEvent(self, event: GetDropdownOptionsEvent) -> dict[str, str]:
        try:
            element_node = event.node
            index_for_logging = element_node.backend_node_id or 'unknown'
            cdp_session = await self.browser_session.cdp_client_for_node(element_node)
            try:
                object_result = await cdp_session.cdp_client.send.DOM.resolveNode(params={'backendNodeId': element_node.backend_node_id}, session_id=cdp_session.session_id)
                remote_object = object_result.get('object', {})
                object_id = remote_object.get('objectId')
                if not object_id:
                    raise ValueError('Could not get object ID from resolved node')
            except Exception as e:
                raise ValueError(f'Failed to resolve node to object: {e}') from e
            check_combobox_script = "\n\t\t\tfunction() {\n\t\t\t\tconst element = this;\n\t\t\t\tconst role = element.getAttribute('role');\n\t\t\t\tconst ariaControls = element.getAttribute('aria-controls');\n\t\t\t\tconst ariaExpanded = element.getAttribute('aria-expanded');\n\t\t\t\t\n\t\t\t\tif (role === 'combobox' && ariaControls) {\n\t\t\t\t\treturn {\n\t\t\t\t\t\tisCombobox: true,\n\t\t\t\t\t\tariaControls: ariaControls,\n\t\t\t\t\t\tisExpanded: ariaExpanded === 'true',\n\t\t\t\t\t\ttagName: element.tagName.toLowerCase()\n\t\t\t\t\t};\n\t\t\t\t}\n\t\t\t\treturn { isCombobox: false };\n\t\t\t}\n\t\t\t"
            combobox_check = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': check_combobox_script, 'objectId': object_id, 'returnByValue': True}, session_id=cdp_session.session_id)
            combobox_info = combobox_check.get('result', {}).get('value', {})
            if combobox_info.get('isCombobox'):
                return await self._handle_aria_combobox_options(cdp_session, object_id, combobox_info, index_for_logging)
            options_script = '\n\t\t\tfunction() {\n\t\t\t\tconst startElement = this;\n\n\t\t\t\t// Function to check if an element is a dropdown and extract options\n\t\t\t\tfunction checkDropdownElement(element) {\n\t\t\t\t\t// Check if it\'s a native select element\n\t\t\t\t\tif (element.tagName.toLowerCase() === \'select\') {\n\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\ttype: \'select\',\n\t\t\t\t\t\t\toptions: Array.from(element.options).map((opt, idx) => ({\n\t\t\t\t\t\t\t\ttext: opt.text.trim(),\n\t\t\t\t\t\t\t\tvalue: opt.value,\n\t\t\t\t\t\t\t\tindex: idx,\n\t\t\t\t\t\t\t\tselected: opt.selected\n\t\t\t\t\t\t\t})),\n\t\t\t\t\t\t\tid: element.id || \'\',\n\t\t\t\t\t\t\tname: element.name || \'\',\n\t\t\t\t\t\t\tsource: \'target\'\n\t\t\t\t\t\t};\n\t\t\t\t\t}\n\n\t\t\t\t\t// Check if it\'s an ARIA dropdown/menu (not combobox - handled separately)\n\t\t\t\t\tconst role = element.getAttribute(\'role\');\n\t\t\t\t\tif (role === \'menu\' || role === \'listbox\') {\n\t\t\t\t\t\t// Find all menu items/options\n\t\t\t\t\t\tconst menuItems = element.querySelectorAll(\'[role="menuitem"], [role="option"]\');\n\t\t\t\t\t\tconst options = [];\n\n\t\t\t\t\t\tmenuItems.forEach((item, idx) => {\n\t\t\t\t\t\t\tconst text = item.textContent ? item.textContent.trim() : \'\';\n\t\t\t\t\t\t\tif (text) {\n\t\t\t\t\t\t\t\toptions.push({\n\t\t\t\t\t\t\t\t\ttext: text,\n\t\t\t\t\t\t\t\t\tvalue: item.getAttribute(\'data-value\') || text,\n\t\t\t\t\t\t\t\t\tindex: idx,\n\t\t\t\t\t\t\t\t\tselected: item.getAttribute(\'aria-selected\') === \'true\' || item.classList.contains(\'selected\')\n\t\t\t\t\t\t\t\t});\n\t\t\t\t\t\t\t}\n\t\t\t\t\t\t});\n\n\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\ttype: \'aria\',\n\t\t\t\t\t\t\toptions: options,\n\t\t\t\t\t\t\tid: element.id || \'\',\n\t\t\t\t\t\t\tname: element.getAttribute(\'aria-label\') || \'\',\n\t\t\t\t\t\t\tsource: \'target\'\n\t\t\t\t\t\t};\n\t\t\t\t\t}\n\n\t\t\t\t\t// Check if it\'s a Semantic UI dropdown or similar\n\t\t\t\t\tif (element.classList.contains(\'dropdown\') || element.classList.contains(\'ui\')) {\n\t\t\t\t\t\tconst menuItems = element.querySelectorAll(\'.item, .option, [data-value]\');\n\t\t\t\t\t\tconst options = [];\n\n\t\t\t\t\t\tmenuItems.forEach((item, idx) => {\n\t\t\t\t\t\t\tconst text = item.textContent ? item.textContent.trim() : \'\';\n\t\t\t\t\t\t\tif (text) {\n\t\t\t\t\t\t\t\toptions.push({\n\t\t\t\t\t\t\t\t\ttext: text,\n\t\t\t\t\t\t\t\t\tvalue: item.getAttribute(\'data-value\') || text,\n\t\t\t\t\t\t\t\t\tindex: idx,\n\t\t\t\t\t\t\t\t\tselected: item.classList.contains(\'selected\') || item.classList.contains(\'active\')\n\t\t\t\t\t\t\t\t});\n\t\t\t\t\t\t\t}\n\t\t\t\t\t\t});\n\n\t\t\t\t\t\tif (options.length > 0) {\n\t\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\t\ttype: \'custom\',\n\t\t\t\t\t\t\t\toptions: options,\n\t\t\t\t\t\t\t\tid: element.id || \'\',\n\t\t\t\t\t\t\t\tname: element.getAttribute(\'aria-label\') || \'\',\n\t\t\t\t\t\t\t\tsource: \'target\'\n\t\t\t\t\t\t\t};\n\t\t\t\t\t\t}\n\t\t\t\t\t}\n\n\t\t\t\t\treturn null;\n\t\t\t\t}\n\n\t\t\t\t// Function to recursively search children up to specified depth\n\t\t\t\tfunction searchChildrenForDropdowns(element, maxDepth, currentDepth = 0) {\n\t\t\t\t\tif (currentDepth >= maxDepth) return null;\n\n\t\t\t\t\t// Check all direct children\n\t\t\t\t\tfor (let child of element.children) {\n\t\t\t\t\t\t// Check if this child is a dropdown\n\t\t\t\t\t\tconst result = checkDropdownElement(child);\n\t\t\t\t\t\tif (result) {\n\t\t\t\t\t\t\tresult.source = `child-depth-${currentDepth + 1}`;\n\t\t\t\t\t\t\treturn result;\n\t\t\t\t\t\t}\n\n\t\t\t\t\t\t// Recursively check this child\'s children\n\t\t\t\t\t\tconst childResult = searchChildrenForDropdowns(child, maxDepth, currentDepth + 1);\n\t\t\t\t\t\tif (childResult) {\n\t\t\t\t\t\t\treturn childResult;\n\t\t\t\t\t\t}\n\t\t\t\t\t}\n\n\t\t\t\t\treturn null;\n\t\t\t\t}\n\n\t\t\t\t// First check the target element itself\n\t\t\t\tlet dropdownResult = checkDropdownElement(startElement);\n\t\t\t\tif (dropdownResult) {\n\t\t\t\t\treturn dropdownResult;\n\t\t\t\t}\n\n\t\t\t\t// If target element is not a dropdown, search children up to depth 4\n\t\t\t\tdropdownResult = searchChildrenForDropdowns(startElement, 4);\n\t\t\t\tif (dropdownResult) {\n\t\t\t\t\treturn dropdownResult;\n\t\t\t\t}\n\n\t\t\t\treturn {\n\t\t\t\t\terror: `Element and its children (depth 4) are not recognizable dropdown types (tag: ${startElement.tagName}, role: ${startElement.getAttribute(\'role\')}, classes: ${startElement.className})`\n\t\t\t\t};\n\t\t\t}\n\t\t\t'
            result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': options_script, 'objectId': object_id, 'returnByValue': True}, session_id=cdp_session.session_id)
            dropdown_data = result.get('result', {}).get('value', {})
            if dropdown_data.get('error'):
                raise BrowserError(message=dropdown_data['error'], long_term_memory=dropdown_data['error'])
            if not dropdown_data.get('options'):
                msg = f'No options found in dropdown at index {index_for_logging}'
                return {'error': msg, 'short_term_memory': msg, 'long_term_memory': msg, 'backend_node_id': str(index_for_logging)}
            formatted_options = []
            for opt in dropdown_data['options']:
                encoded_text = json.dumps(opt['text'])
                status = ' (selected)' if opt.get('selected') else ''
                formatted_options.append(f"{opt['index']}: text={encoded_text}, value={json.dumps(opt['value'])}{status}")
            dropdown_type = dropdown_data.get('type', 'select')
            element_info = f"Index: {index_for_logging}, Type: {dropdown_type}, ID: {dropdown_data.get('id', 'none')}, Name: {dropdown_data.get('name', 'none')}"
            source_info = dropdown_data.get('source', 'unknown')
            if source_info == 'target':
                msg = f'Found {dropdown_type} dropdown ({element_info}):\n' + '\n'.join(formatted_options)
            else:
                msg = f'Found {dropdown_type} dropdown in {source_info} ({element_info}):\n' + '\n'.join(formatted_options)
            msg += f'\n\nUse the exact text or value string (without quotes) in select_dropdown(index={index_for_logging}, text=...)'
            if source_info == 'target':
                self.logger.info(f"📋 Found {len(dropdown_data['options'])} dropdown options for index {index_for_logging}")
            else:
                self.logger.info(f"📋 Found {len(dropdown_data['options'])} dropdown options for index {index_for_logging} in {source_info}")
            short_term_memory = msg
            long_term_memory = f'Got dropdown options for index {index_for_logging}'
            return {'type': dropdown_type, 'options': json.dumps(dropdown_data['options']), 'element_info': element_info, 'source': source_info, 'formatted_options': '\n'.join(formatted_options), 'message': msg, 'short_term_memory': short_term_memory, 'long_term_memory': long_term_memory, 'backend_node_id': str(index_for_logging)}
        except BrowserError:
            raise
        except TimeoutError:
            msg = f'Failed to get dropdown options for index {index_for_logging} due to timeout.'
            self.logger.error(msg)
            raise BrowserError(message=msg, long_term_memory=msg)
        except Exception as e:
            msg = 'Failed to get dropdown options'
            error_msg = f'{msg}: {str(e)}'
            self.logger.error(error_msg)
            raise BrowserError(message=error_msg, long_term_memory=f'Failed to get dropdown options for index {index_for_logging}.')

    async def _handle_aria_combobox_options(self, cdp_session, object_id: str, combobox_info: dict, index_for_logging: int | str) -> dict[str, str]:
        aria_controls_id = combobox_info.get('ariaControls')
        was_expanded = combobox_info.get('isExpanded', False)
        if not was_expanded:
            expand_script = "\n\t\t\tfunction() {\n\t\t\t\tconst element = this;\n\t\t\t\t\n\t\t\t\t// Dispatch focus event properly\n\t\t\t\tconst focusEvent = new FocusEvent('focus', { bubbles: true, cancelable: true });\n\t\t\t\telement.dispatchEvent(focusEvent);\n\t\t\t\t\n\t\t\t\t// Also call native focus\n\t\t\t\telement.focus();\n\t\t\t\t\n\t\t\t\t// Dispatch focusin event (bubbles, unlike focus)\n\t\t\t\tconst focusInEvent = new FocusEvent('focusin', { bubbles: true, cancelable: true });\n\t\t\t\telement.dispatchEvent(focusInEvent);\n\t\t\t\t\n\t\t\t\t// For some comboboxes, a click is needed\n\t\t\t\tconst clickEvent = new MouseEvent('click', {\n\t\t\t\t\tbubbles: true,\n\t\t\t\t\tcancelable: true,\n\t\t\t\t\tview: window\n\t\t\t\t});\n\t\t\t\telement.dispatchEvent(clickEvent);\n\t\t\t\t\n\t\t\t\t// Some comboboxes respond to mousedown\n\t\t\t\tconst mousedownEvent = new MouseEvent('mousedown', {\n\t\t\t\t\tbubbles: true,\n\t\t\t\t\tcancelable: true,\n\t\t\t\t\tview: window\n\t\t\t\t});\n\t\t\t\telement.dispatchEvent(mousedownEvent);\n\t\t\t\t\n\t\t\t\treturn {\n\t\t\t\t\tsuccess: true,\n\t\t\t\t\tariaExpanded: element.getAttribute('aria-expanded')\n\t\t\t\t};\n\t\t\t}\n\t\t\t"
            await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': expand_script, 'objectId': object_id, 'returnByValue': True}, session_id=cdp_session.session_id)
            await asyncio.sleep(0.5)
        extract_options_script = '\n\t\tfunction(ariaControlsId) {\n\t\t\tconst combobox = this;\n\t\t\t\n\t\t\t// Find the listbox element referenced by aria-controls\n\t\t\tconst listbox = document.getElementById(ariaControlsId);\n\t\t\t\n\t\t\tif (!listbox) {\n\t\t\t\treturn {\n\t\t\t\t\terror: `Could not find listbox element with id "${ariaControlsId}" referenced by aria-controls`,\n\t\t\t\t\tariaControlsId: ariaControlsId\n\t\t\t\t};\n\t\t\t}\n\t\t\t\n\t\t\t// Find all option elements in the listbox\n\t\t\tconst optionElements = listbox.querySelectorAll(\'[role="option"]\');\n\t\t\tconst options = [];\n\t\t\t\n\t\t\toptionElements.forEach((item, idx) => {\n\t\t\t\tconst text = item.textContent ? item.textContent.trim() : \'\';\n\t\t\t\tif (text) {\n\t\t\t\t\toptions.push({\n\t\t\t\t\t\ttext: text,\n\t\t\t\t\t\tvalue: item.getAttribute(\'data-value\') || item.getAttribute(\'value\') || text,\n\t\t\t\t\t\tindex: idx,\n\t\t\t\t\t\tselected: item.getAttribute(\'aria-selected\') === \'true\' || item.classList.contains(\'selected\')\n\t\t\t\t\t});\n\t\t\t\t}\n\t\t\t});\n\t\t\t\n\t\t\t// If no options with role="option", try other common patterns\n\t\t\tif (options.length === 0) {\n\t\t\t\t// Try li elements inside\n\t\t\t\tconst liElements = listbox.querySelectorAll(\'li\');\n\t\t\t\tliElements.forEach((item, idx) => {\n\t\t\t\t\tconst text = item.textContent ? item.textContent.trim() : \'\';\n\t\t\t\t\tif (text) {\n\t\t\t\t\t\toptions.push({\n\t\t\t\t\t\t\ttext: text,\n\t\t\t\t\t\t\tvalue: item.getAttribute(\'data-value\') || item.getAttribute(\'value\') || text,\n\t\t\t\t\t\t\tindex: idx,\n\t\t\t\t\t\t\tselected: item.getAttribute(\'aria-selected\') === \'true\' || item.classList.contains(\'selected\')\n\t\t\t\t\t\t});\n\t\t\t\t\t}\n\t\t\t\t});\n\t\t\t}\n\t\t\t\n\t\t\treturn {\n\t\t\t\ttype: \'aria-combobox\',\n\t\t\t\toptions: options,\n\t\t\t\tid: combobox.id || \'\',\n\t\t\t\tname: combobox.getAttribute(\'aria-label\') || combobox.getAttribute(\'name\') || \'\',\n\t\t\t\tlistboxId: ariaControlsId,\n\t\t\t\tsource: \'aria-controls\'\n\t\t\t};\n\t\t}\n\t\t'
        result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': extract_options_script, 'objectId': object_id, 'arguments': [{'value': aria_controls_id}], 'returnByValue': True}, session_id=cdp_session.session_id)
        dropdown_data = result.get('result', {}).get('value', {})
        if not was_expanded:
            collapse_script = "\n\t\t\tfunction() {\n\t\t\t\tthis.blur();\n\t\t\t\t// Also dispatch escape key to close dropdowns\n\t\t\t\tconst escEvent = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true });\n\t\t\t\tthis.dispatchEvent(escEvent);\n\t\t\t\treturn true;\n\t\t\t}\n\t\t\t"
            await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': collapse_script, 'objectId': object_id, 'returnByValue': True}, session_id=cdp_session.session_id)
        if dropdown_data.get('error'):
            raise BrowserError(message=dropdown_data['error'], long_term_memory=dropdown_data['error'])
        if not dropdown_data.get('options'):
            msg = f'No options found in ARIA combobox at index {index_for_logging} (listbox: {aria_controls_id})'
            return {'error': msg, 'short_term_memory': msg, 'long_term_memory': msg, 'backend_node_id': str(index_for_logging)}
        formatted_options = []
        for opt in dropdown_data['options']:
            encoded_text = json.dumps(opt['text'])
            status = ' (selected)' if opt.get('selected') else ''
            formatted_options.append(f"{opt['index']}: text={encoded_text}, value={json.dumps(opt['value'])}{status}")
        dropdown_type = dropdown_data.get('type', 'aria-combobox')
        element_info = f"Index: {index_for_logging}, Type: {dropdown_type}, ID: {dropdown_data.get('id', 'none')}, Name: {dropdown_data.get('name', 'none')}"
        source_info = f'aria-controls → {aria_controls_id}'
        msg = f'Found {dropdown_type} dropdown ({element_info}):\n' + '\n'.join(formatted_options)
        msg += f'\n\nUse the exact text or value string (without quotes) in select_dropdown(index={index_for_logging}, text=...)'
        self.logger.info(f"📋 Found {len(dropdown_data['options'])} options in ARIA combobox at index {index_for_logging}")
        return {'type': dropdown_type, 'options': json.dumps(dropdown_data['options']), 'element_info': element_info, 'source': source_info, 'formatted_options': '\n'.join(formatted_options), 'message': msg, 'short_term_memory': msg, 'long_term_memory': f'Got dropdown options for ARIA combobox at index {index_for_logging}', 'backend_node_id': str(index_for_logging)}

    async def on_SelectDropdownOptionEvent(self, event: SelectDropdownOptionEvent) -> dict[str, str]:
        try:
            element_node = event.node
            index_for_logging = element_node.backend_node_id or 'unknown'
            target_text = event.text
            cdp_session = await self.browser_session.cdp_client_for_node(element_node)
            try:
                object_result = await cdp_session.cdp_client.send.DOM.resolveNode(params={'backendNodeId': element_node.backend_node_id}, session_id=cdp_session.session_id)
                remote_object = object_result.get('object', {})
                object_id = remote_object.get('objectId')
                if not object_id:
                    raise ValueError('Could not get object ID from resolved node')
            except Exception as e:
                raise ValueError(f'Failed to resolve node to object: {e}') from e
            try:
                selection_script = '\n\t\t\t\tfunction(targetText) {\n\t\t\t\t\tconst startElement = this;\n\n\t\t\t\t\t// Function to attempt selection on a dropdown element\n\t\t\t\t\tfunction attemptSelection(element) {\n\t\t\t\t\t\t// Handle native select elements\n\t\t\t\t\t\tif (element.tagName.toLowerCase() === \'select\') {\n\t\t\t\t\t\t\tconst options = Array.from(element.options);\n\t\t\t\t\t\t\tconst targetTextLower = targetText.toLowerCase();\n\n\t\t\t\t\t\t\tfor (const option of options) {\n\t\t\t\t\t\t\t\tconst optionTextLower = option.text.trim().toLowerCase();\n\t\t\t\t\t\t\t\tconst optionValueLower = option.value.toLowerCase();\n\n\t\t\t\t\t\t\t\t// Match against both text and value (case-insensitive)\n\t\t\t\t\t\t\t\tif (optionTextLower === targetTextLower || optionValueLower === targetTextLower) {\n\t\t\t\t\t\t\t\t\tconst expectedValue = option.value;\n\n\t\t\t\t\t\t\t\t\t// Focus the element FIRST (important for Svelte/Vue/React and other reactive frameworks)\n\t\t\t\t\t\t\t\t\t// This simulates the user focusing on the dropdown before changing it\n\t\t\t\t\t\t\t\t\telement.focus();\n\n\t\t\t\t\t\t\t\t\t// Then set the value using multiple methods for maximum compatibility\n\t\t\t\t\t\t\t\t\telement.value = expectedValue;\n\t\t\t\t\t\t\t\t\toption.selected = true;\n\t\t\t\t\t\t\t\t\telement.selectedIndex = option.index;\n\n\t\t\t\t\t\t\t\t\t// Trigger all necessary events for reactive frameworks\n\t\t\t\t\t\t\t\t\t// 1. input event - critical for Vue\'s v-model and Svelte\'s bind:value\n\t\t\t\t\t\t\t\t\tconst inputEvent = new Event(\'input\', { bubbles: true, cancelable: true });\n\t\t\t\t\t\t\t\t\telement.dispatchEvent(inputEvent);\n\n\t\t\t\t\t\t\t\t\t// 2. change event - traditional form validation and framework reactivity\n\t\t\t\t\t\t\t\t\tconst changeEvent = new Event(\'change\', { bubbles: true, cancelable: true });\n\t\t\t\t\t\t\t\t\telement.dispatchEvent(changeEvent);\n\n\t\t\t\t\t\t\t\t\t// 3. blur event - completes the interaction, triggers validation\n\t\t\t\t\t\t\t\t\telement.blur();\n\n\t\t\t\t\t\t\t\t\t// Verification: Check if the selection actually stuck (avoid intercepting and resetting the value)\n\t\t\t\t\t\t\t\t\tif (element.value !== expectedValue) {\n\t\t\t\t\t\t\t\t\t\t// Selection was reverted - need to try clicking instead\n\t\t\t\t\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\t\t\t\t\tsuccess: false,\n\t\t\t\t\t\t\t\t\t\t\terror: `Selection was set but reverted by page framework. The dropdown may require clicking.`,\n\t\t\t\t\t\t\t\t\t\t\tselectionReverted: true,\n\t\t\t\t\t\t\t\t\t\t\ttargetOption: {\n\t\t\t\t\t\t\t\t\t\t\t\ttext: option.text.trim(),\n\t\t\t\t\t\t\t\t\t\t\t\tvalue: expectedValue,\n\t\t\t\t\t\t\t\t\t\t\t\tindex: option.index\n\t\t\t\t\t\t\t\t\t\t\t},\n\t\t\t\t\t\t\t\t\t\t\tavailableOptions: Array.from(element.options).map(opt => ({\n\t\t\t\t\t\t\t\t\t\t\t\ttext: opt.text.trim(),\n\t\t\t\t\t\t\t\t\t\t\t\tvalue: opt.value\n\t\t\t\t\t\t\t\t\t\t\t}))\n\t\t\t\t\t\t\t\t\t\t};\n\t\t\t\t\t\t\t\t\t}\n\n\t\t\t\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\t\t\t\tsuccess: true,\n\t\t\t\t\t\t\t\t\t\tmessage: `Selected option: ${option.text.trim()} (value: ${option.value})`,\n\t\t\t\t\t\t\t\t\t\tvalue: option.value\n\t\t\t\t\t\t\t\t\t};\n\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t}\n\n\t\t\t\t\t\t\t// Return available options as separate field\n\t\t\t\t\t\t\tconst availableOptions = options.map(opt => ({\n\t\t\t\t\t\t\t\ttext: opt.text.trim(),\n\t\t\t\t\t\t\t\tvalue: opt.value\n\t\t\t\t\t\t\t}));\n\n\t\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\t\tsuccess: false,\n\t\t\t\t\t\t\t\terror: `Option with text or value \'${targetText}\' not found in select element`,\n\t\t\t\t\t\t\t\tavailableOptions: availableOptions\n\t\t\t\t\t\t\t};\n\t\t\t\t\t\t}\n\n\t\t\t\t\t\t// Handle ARIA dropdowns/menus\n\t\t\t\t\t\tconst role = element.getAttribute(\'role\');\n\t\t\t\t\t\tif (role === \'menu\' || role === \'listbox\' || role === \'combobox\') {\n\t\t\t\t\t\t\tconst menuItems = element.querySelectorAll(\'[role="menuitem"], [role="option"]\');\n\t\t\t\t\t\t\tconst targetTextLower = targetText.toLowerCase();\n\n\t\t\t\t\t\t\tfor (const item of menuItems) {\n\t\t\t\t\t\t\t\tif (item.textContent) {\n\t\t\t\t\t\t\t\t\tconst itemTextLower = item.textContent.trim().toLowerCase();\n\t\t\t\t\t\t\t\t\tconst itemValueLower = (item.getAttribute(\'data-value\') || \'\').toLowerCase();\n\n\t\t\t\t\t\t\t\t\t// Match against both text and data-value (case-insensitive)\n\t\t\t\t\t\t\t\t\tif (itemTextLower === targetTextLower || itemValueLower === targetTextLower) {\n\t\t\t\t\t\t\t\t\t\t// Clear previous selections\n\t\t\t\t\t\t\t\t\t\tmenuItems.forEach(mi => {\n\t\t\t\t\t\t\t\t\t\t\tmi.setAttribute(\'aria-selected\', \'false\');\n\t\t\t\t\t\t\t\t\t\t\tmi.classList.remove(\'selected\');\n\t\t\t\t\t\t\t\t\t\t});\n\n\t\t\t\t\t\t\t\t\t\t// Select this item\n\t\t\t\t\t\t\t\t\t\titem.setAttribute(\'aria-selected\', \'true\');\n\t\t\t\t\t\t\t\t\t\titem.classList.add(\'selected\');\n\n\t\t\t\t\t\t\t\t\t\t// Trigger click and change events\n\t\t\t\t\t\t\t\t\t\titem.click();\n\t\t\t\t\t\t\t\t\t\tconst clickEvent = new MouseEvent(\'click\', { view: window, bubbles: true, cancelable: true });\n\t\t\t\t\t\t\t\t\t\titem.dispatchEvent(clickEvent);\n\n\t\t\t\t\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\t\t\t\t\tsuccess: true,\n\t\t\t\t\t\t\t\t\t\t\tmessage: `Selected ARIA menu item: ${item.textContent.trim()}`\n\t\t\t\t\t\t\t\t\t\t};\n\t\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t}\n\n\t\t\t\t\t\t\t// Return available options as separate field\n\t\t\t\t\t\t\tconst availableOptions = Array.from(menuItems).map(item => ({\n\t\t\t\t\t\t\t\ttext: item.textContent ? item.textContent.trim() : \'\',\n\t\t\t\t\t\t\t\tvalue: item.getAttribute(\'data-value\') || \'\'\n\t\t\t\t\t\t\t})).filter(opt => opt.text || opt.value);\n\n\t\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\t\tsuccess: false,\n\t\t\t\t\t\t\t\terror: `Menu item with text or value \'${targetText}\' not found`,\n\t\t\t\t\t\t\t\tavailableOptions: availableOptions\n\t\t\t\t\t\t\t};\n\t\t\t\t\t\t}\n\n\t\t\t\t\t\t// Handle Semantic UI or custom dropdowns\n\t\t\t\t\t\tif (element.classList.contains(\'dropdown\') || element.classList.contains(\'ui\')) {\n\t\t\t\t\t\t\tconst menuItems = element.querySelectorAll(\'.item, .option, [data-value]\');\n\t\t\t\t\t\t\tconst targetTextLower = targetText.toLowerCase();\n\n\t\t\t\t\t\t\tfor (const item of menuItems) {\n\t\t\t\t\t\t\t\tif (item.textContent) {\n\t\t\t\t\t\t\t\t\tconst itemTextLower = item.textContent.trim().toLowerCase();\n\t\t\t\t\t\t\t\t\tconst itemValueLower = (item.getAttribute(\'data-value\') || \'\').toLowerCase();\n\n\t\t\t\t\t\t\t\t\t// Match against both text and data-value (case-insensitive)\n\t\t\t\t\t\t\t\t\tif (itemTextLower === targetTextLower || itemValueLower === targetTextLower) {\n\t\t\t\t\t\t\t\t\t\t// Clear previous selections\n\t\t\t\t\t\t\t\t\t\tmenuItems.forEach(mi => {\n\t\t\t\t\t\t\t\t\t\t\tmi.classList.remove(\'selected\', \'active\');\n\t\t\t\t\t\t\t\t\t\t});\n\n\t\t\t\t\t\t\t\t\t\t// Select this item\n\t\t\t\t\t\t\t\t\t\titem.classList.add(\'selected\', \'active\');\n\n\t\t\t\t\t\t\t\t\t\t// Update dropdown text if there\'s a text element\n\t\t\t\t\t\t\t\t\t\tconst textElement = element.querySelector(\'.text\');\n\t\t\t\t\t\t\t\t\t\tif (textElement) {\n\t\t\t\t\t\t\t\t\t\t\ttextElement.textContent = item.textContent.trim();\n\t\t\t\t\t\t\t\t\t\t}\n\n\t\t\t\t\t\t\t\t\t\t// Trigger click and change events\n\t\t\t\t\t\t\t\t\t\titem.click();\n\t\t\t\t\t\t\t\t\t\tconst clickEvent = new MouseEvent(\'click\', { view: window, bubbles: true, cancelable: true });\n\t\t\t\t\t\t\t\t\t\titem.dispatchEvent(clickEvent);\n\n\t\t\t\t\t\t\t\t\t\t// Also dispatch on the main dropdown element\n\t\t\t\t\t\t\t\t\t\tconst dropdownChangeEvent = new Event(\'change\', { bubbles: true });\n\t\t\t\t\t\t\t\t\t\telement.dispatchEvent(dropdownChangeEvent);\n\n\t\t\t\t\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\t\t\t\t\tsuccess: true,\n\t\t\t\t\t\t\t\t\t\t\tmessage: `Selected custom dropdown item: ${item.textContent.trim()}`\n\t\t\t\t\t\t\t\t\t\t};\n\t\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t}\n\n\t\t\t\t\t\t\t// Return available options as separate field\n\t\t\t\t\t\t\tconst availableOptions = Array.from(menuItems).map(item => ({\n\t\t\t\t\t\t\t\ttext: item.textContent ? item.textContent.trim() : \'\',\n\t\t\t\t\t\t\t\tvalue: item.getAttribute(\'data-value\') || \'\'\n\t\t\t\t\t\t\t})).filter(opt => opt.text || opt.value);\n\n\t\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\t\tsuccess: false,\n\t\t\t\t\t\t\t\terror: `Custom dropdown item with text or value \'${targetText}\' not found`,\n\t\t\t\t\t\t\t\tavailableOptions: availableOptions\n\t\t\t\t\t\t\t};\n\t\t\t\t\t\t}\n\n\t\t\t\t\t\treturn null; // Not a dropdown element\n\t\t\t\t\t}\n\n\t\t\t\t\t// Function to recursively search children for dropdowns\n\t\t\t\t\tfunction searchChildrenForSelection(element, maxDepth, currentDepth = 0) {\n\t\t\t\t\t\tif (currentDepth >= maxDepth) return null;\n\n\t\t\t\t\t\t// Check all direct children\n\t\t\t\t\t\tfor (let child of element.children) {\n\t\t\t\t\t\t\t// Try selection on this child\n\t\t\t\t\t\t\tconst result = attemptSelection(child);\n\t\t\t\t\t\t\tif (result && result.success) {\n\t\t\t\t\t\t\t\treturn result;\n\t\t\t\t\t\t\t}\n\n\t\t\t\t\t\t\t// Recursively check this child\'s children\n\t\t\t\t\t\t\tconst childResult = searchChildrenForSelection(child, maxDepth, currentDepth + 1);\n\t\t\t\t\t\t\tif (childResult && childResult.success) {\n\t\t\t\t\t\t\t\treturn childResult;\n\t\t\t\t\t\t\t}\n\t\t\t\t\t\t}\n\n\t\t\t\t\t\treturn null;\n\t\t\t\t\t}\n\n\t\t\t\t\t// First try the target element itself\n\t\t\t\t\tlet selectionResult = attemptSelection(startElement);\n\t\t\t\t\tif (selectionResult) {\n\t\t\t\t\t\t// If attemptSelection returned a result (success or failure), use it\n\t\t\t\t\t\t// Don\'t search children if we found a dropdown element but selection failed\n\t\t\t\t\t\treturn selectionResult;\n\t\t\t\t\t}\n\n\t\t\t\t\t// Only search children if target element is not a dropdown element\n\t\t\t\t\tselectionResult = searchChildrenForSelection(startElement, 4);\n\t\t\t\t\tif (selectionResult && selectionResult.success) {\n\t\t\t\t\t\treturn selectionResult;\n\t\t\t\t\t}\n\n\t\t\t\t\treturn {\n\t\t\t\t\t\tsuccess: false,\n\t\t\t\t\t\terror: `Element and its children (depth 4) do not contain a dropdown with option \'${targetText}\' (tag: ${startElement.tagName}, role: ${startElement.getAttribute(\'role\')}, classes: ${startElement.className})`\n\t\t\t\t\t};\n\t\t\t\t}\n\t\t\t\t'
                result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': selection_script, 'arguments': [{'value': target_text}], 'objectId': object_id, 'returnByValue': True}, session_id=cdp_session.session_id)
                selection_result = result.get('result', {}).get('value', {})
                if not selection_result.get('success'):
                    available_options = selection_result.get('availableOptions', [])
                    all_empty = available_options and all((not opt.get('text', '').strip() and (not opt.get('value', '').strip()) if isinstance(opt, dict) else not str(opt).strip() for opt in available_options))
                    if all_empty:
                        self.logger.info('⚠️ All dropdown options are empty — options may be lazily loaded. Focusing element and retrying...')
                        try:
                            await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': 'function() { this.focus(); }', 'objectId': object_id}, session_id=cdp_session.session_id)
                        except Exception:
                            pass
                        await asyncio.sleep(1.0)
                        retry_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': selection_script, 'arguments': [{'value': target_text}], 'objectId': object_id, 'returnByValue': True}, session_id=cdp_session.session_id)
                        selection_result = retry_result.get('result', {}).get('value', {})
                if selection_result.get('selectionReverted'):
                    self.logger.info('⚠️ Selection was reverted by page framework, trying click fallback...')
                    target_option = selection_result.get('targetOption', {})
                    option_index = target_option.get('index', 0)
                    click_fallback_script = "\n\t\t\t\t\tfunction(optionIndex) {\n\t\t\t\t\t\tconst select = this;\n\t\t\t\t\t\tif (select.tagName.toLowerCase() !== 'select') return { success: false, error: 'Not a select element' };\n\n\t\t\t\t\t\tconst option = select.options[optionIndex];\n\t\t\t\t\t\tif (!option) return { success: false, error: 'Option not found at index ' + optionIndex };\n\n\t\t\t\t\t\t// Method 1: Try using the native selectedIndex setter with a small delay\n\t\t\t\t\t\tconst originalValue = select.value;\n\n\t\t\t\t\t\t// Simulate opening the dropdown (some frameworks need this)\n\t\t\t\t\t\tselect.focus();\n\t\t\t\t\t\tconst mouseDown = new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window });\n\t\t\t\t\t\tselect.dispatchEvent(mouseDown);\n\n\t\t\t\t\t\t// Set using selectedIndex (more reliable for some frameworks)\n\t\t\t\t\t\tselect.selectedIndex = optionIndex;\n\n\t\t\t\t\t\t// Click the option\n\t\t\t\t\t\toption.selected = true;\n\t\t\t\t\t\tconst optionClick = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });\n\t\t\t\t\t\toption.dispatchEvent(optionClick);\n\n\t\t\t\t\t\t// Close dropdown\n\t\t\t\t\t\tconst mouseUp = new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window });\n\t\t\t\t\t\tselect.dispatchEvent(mouseUp);\n\n\t\t\t\t\t\t// Fire change event\n\t\t\t\t\t\tconst changeEvent = new Event('change', { bubbles: true, cancelable: true });\n\t\t\t\t\t\tselect.dispatchEvent(changeEvent);\n\n\t\t\t\t\t\t// Blur to finalize\n\t\t\t\t\t\tselect.blur();\n\n\t\t\t\t\t\t// Verify\n\t\t\t\t\t\tif (select.value === option.value || select.selectedIndex === optionIndex) {\n\t\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\t\tsuccess: true,\n\t\t\t\t\t\t\t\tmessage: 'Selected via click fallback: ' + option.text.trim(),\n\t\t\t\t\t\t\t\tvalue: option.value\n\t\t\t\t\t\t\t};\n\t\t\t\t\t\t}\n\n\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\tsuccess: false,\n\t\t\t\t\t\t\terror: 'Click fallback also failed - framework may block all programmatic selection',\n\t\t\t\t\t\t\tfinalValue: select.value,\n\t\t\t\t\t\t\texpectedValue: option.value\n\t\t\t\t\t\t};\n\t\t\t\t\t}\n\t\t\t\t\t"
                    fallback_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'functionDeclaration': click_fallback_script, 'arguments': [{'value': option_index}], 'objectId': object_id, 'returnByValue': True}, session_id=cdp_session.session_id)
                    fallback_data = fallback_result.get('result', {}).get('value', {})
                    if fallback_data.get('success'):
                        msg = fallback_data.get('message', f'Selected option via click: {target_text}')
                        self.logger.info(f'✅ {msg}')
                        return {'success': 'true', 'message': msg, 'value': fallback_data.get('value', target_text), 'backend_node_id': str(index_for_logging)}
                    else:
                        self.logger.warning(f"⚠️ Click fallback also failed: {fallback_data.get('error', 'unknown')}")
                if selection_result.get('success'):
                    msg = selection_result.get('message', f'Selected option: {target_text}')
                    self.logger.debug(f'{msg}')
                    return {'success': 'true', 'message': msg, 'value': selection_result.get('value', target_text), 'backend_node_id': str(index_for_logging)}
                else:
                    error_msg = selection_result.get('error', f'Failed to select option: {target_text}')
                    available_options = selection_result.get('availableOptions', [])
                    self.logger.error(f'❌ {error_msg}')
                    self.logger.debug(f'Available options from JavaScript: {available_options}')
                    if available_options:
                        short_term_options = []
                        for opt in available_options:
                            if isinstance(opt, dict):
                                text = opt.get('text', '').strip()
                                value = opt.get('value', '').strip()
                                if text:
                                    short_term_options.append(f'- {text}')
                                elif value:
                                    short_term_options.append(f'- {value}')
                            elif isinstance(opt, str):
                                short_term_options.append(f'- {opt}')
                        if short_term_options:
                            short_term_memory = 'Available dropdown options  are:\n' + '\n'.join(short_term_options)
                            long_term_memory = f"Couldn't select the dropdown option as '{target_text}' is not one of the available options."
                            return {'success': 'false', 'error': error_msg, 'short_term_memory': short_term_memory, 'long_term_memory': long_term_memory, 'backend_node_id': str(index_for_logging)}
                    return {'success': 'false', 'error': error_msg, 'backend_node_id': str(index_for_logging)}
            except Exception as e:
                error_msg = f'Failed to select dropdown option: {str(e)}'
                self.logger.error(error_msg)
                raise ValueError(error_msg) from e
        except Exception as e:
            error_msg = f'Failed to select dropdown option "{target_text}" for element {index_for_logging}: {str(e)}'
            self.logger.error(error_msg)
            raise ValueError(error_msg) from e