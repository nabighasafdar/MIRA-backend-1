import asyncio
import time
from typing import TYPE_CHECKING
from system.browser.events import BrowserErrorEvent, BrowserStateRequestEvent, ScreenshotEvent, TabCreatedEvent
from system.browser.watchdog_base import BaseWatchdog
from system.dom.service import DomService
from system.dom.views import EnhancedDOMTreeNode, SerializedDOMState
from system.observability import observe_debug
from system.utils import create_task_with_error_handling, time_execution_async
if TYPE_CHECKING:
    from system.browser.views import BrowserStateSummary, NetworkRequest, PageInfo, PaginationButton

class DOMWatchdog(BaseWatchdog):
    LISTENS_TO = [TabCreatedEvent, BrowserStateRequestEvent]
    EMITS = [BrowserErrorEvent]
    selector_map: dict[int, EnhancedDOMTreeNode] | None = None
    current_dom_state: SerializedDOMState | None = None
    enhanced_dom_tree: EnhancedDOMTreeNode | None = None
    _dom_service: DomService | None = None
    _pending_requests: dict[str, tuple[str, float, str, str | None]] = {}

    async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
        return None

    def _get_recent_events_str(self, limit: int=10) -> str | None:
        import json
        try:
            all_events = sorted(self.browser_session.event_bus.event_history.values(), key=lambda e: e.event_created_at.timestamp(), reverse=True)
            recent_events_data = []
            for event in all_events[:limit]:
                event_data = {'event_type': event.event_type, 'timestamp': event.event_created_at.isoformat()}
                if hasattr(event, 'url'):
                    event_data['url'] = getattr(event, 'url')
                if hasattr(event, 'error_message'):
                    event_data['error_message'] = getattr(event, 'error_message')
                if hasattr(event, 'target_id'):
                    event_data['target_id'] = getattr(event, 'target_id')
                recent_events_data.append(event_data)
            return json.dumps(recent_events_data)
        except Exception as e:
            self.logger.debug(f'Failed to get recent events: {e}')
        return json.dumps([])

    async def _get_pending_network_requests(self) -> list['NetworkRequest']:
        from system.browser.views import NetworkRequest
        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session(focus=True)
            js_code = "\n(function() {\n\tconst now = performance.now();\n\tconst resources = performance.getEntriesByType('resource');\n\tconst pending = [];\n\n\t// Check document readyState\n\tconst docLoading = document.readyState !== 'complete';\n\n\t// Common ad/tracking domains and patterns to filter out\n\tconst adDomains = [\n\t\t// Standard ad/tracking networks\n\t\t'doubleclick.net', 'googlesyndication.com', 'googletagmanager.com',\n\t\t'facebook.net', 'analytics', 'ads', 'tracking', 'pixel',\n\t\t'hotjar.com', 'clarity.ms', 'mixpanel.com', 'segment.com',\n\t\t// Analytics platforms\n\t\t'demdex.net', 'omtrdc.net', 'adobedtm.com', 'ensighten.com',\n\t\t'newrelic.com', 'nr-data.net', 'google-analytics.com',\n\t\t// Social media trackers\n\t\t'connect.facebook.net', 'platform.twitter.com', 'platform.linkedin.com',\n\t\t// CDN/image hosts (usually not critical for functionality)\n\t\t'.cloudfront.net/image/', '.akamaized.net/image/',\n\t\t// Common tracking paths\n\t\t'/tracker/', '/collector/', '/beacon/', '/telemetry/', '/log/',\n\t\t'/events/', '/eventBatch', '/track.', '/metrics/'\n\t];\n\n\t// Get resources that are still loading (responseEnd is 0)\n\tlet totalResourcesChecked = 0;\n\tlet filteredByResponseEnd = 0;\n\tconst allDomains = new Set();\n\n\tfor (const entry of resources) {\n\t\ttotalResourcesChecked++;\n\n\t\t// Track all domains from recent resources (for logging)\n\t\ttry {\n\t\t\tconst hostname = new URL(entry.name).hostname;\n\t\t\tif (hostname) allDomains.add(hostname);\n\t\t} catch (e) {}\n\n\t\tif (entry.responseEnd === 0) {\n\t\t\tfilteredByResponseEnd++;\n\t\t\tconst url = entry.name;\n\n\t\t\t// Filter out ads and tracking\n\t\t\tconst isAd = adDomains.some(domain => url.includes(domain));\n\t\t\tif (isAd) continue;\n\n\t\t\t// Filter out data: URLs and very long URLs (often inline resources)\n\t\t\tif (url.startsWith('data:') || url.length > 500) continue;\n\n\t\t\tconst loadingDuration = now - entry.startTime;\n\n\t\t\t// Skip requests that have been loading for >10 seconds (likely stuck/polling)\n\t\t\tif (loadingDuration > 10000) continue;\n\n\t\t\tconst resourceType = entry.initiatorType || 'unknown';\n\n\t\t\t// Filter out non-critical resources (images, fonts, icons) if loading >3 seconds\n\t\t\tconst nonCriticalTypes = ['img', 'image', 'icon', 'font'];\n\t\t\tif (nonCriticalTypes.includes(resourceType) && loadingDuration > 3000) continue;\n\n\t\t\t// Filter out image URLs even if type is unknown\n\t\t\tconst isImageUrl = /\\.(jpg|jpeg|png|gif|webp|svg|ico)(\\?|$)/i.test(url);\n\t\t\tif (isImageUrl && loadingDuration > 3000) continue;\n\n\t\t\tpending.push({\n\t\t\t\turl: url,\n\t\t\t\tmethod: 'GET',\n\t\t\t\tloading_duration_ms: Math.round(loadingDuration),\n\t\t\t\tresource_type: resourceType\n\t\t\t});\n\t\t}\n\t}\n\n\treturn {\n\t\tpending_requests: pending,\n\t\tdocument_loading: docLoading,\n\t\tdocument_ready_state: document.readyState,\n\t\tdebug: {\n\t\t\ttotal_resources: totalResourcesChecked,\n\t\t\twith_response_end_zero: filteredByResponseEnd,\n\t\t\tafter_all_filters: pending.length,\n\t\t\tall_domains: Array.from(allDomains)\n\t\t}\n\t};\n})()\n"
            result = await cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': js_code, 'returnByValue': True}, session_id=cdp_session.session_id)
            if result.get('result', {}).get('type') == 'object':
                data = result['result'].get('value', {})
                pending = data.get('pending_requests', [])
                doc_state = data.get('document_ready_state', 'unknown')
                doc_loading = data.get('document_loading', False)
                debug_info = data.get('debug', {})
                all_domains = debug_info.get('all_domains', [])
                all_domains_str = ', '.join(sorted(all_domains)[:5]) if all_domains else 'none'
                if len(all_domains) > 5:
                    all_domains_str += f' +{len(all_domains) - 5} more'
                self.logger.debug(f"🔍 Network check: document.readyState={doc_state}, loading={doc_loading}, total_resources={debug_info.get('total_resources', 0)}, responseEnd=0: {debug_info.get('with_response_end_zero', 0)}, after_filters={len(pending)}, domains=[{all_domains_str}]")
                network_requests = []
                for req in pending[:20]:
                    network_requests.append(NetworkRequest(url=req['url'], method=req.get('method', 'GET'), loading_duration_ms=req.get('loading_duration_ms', 0.0), resource_type=req.get('resource_type')))
                return network_requests
        except Exception as e:
            self.logger.debug(f'Failed to get pending network requests: {e}')
        return []

    @observe_debug(ignore_input=True, ignore_output=True, name='browser_state_request_event')
    async def on_BrowserStateRequestEvent(self, event: BrowserStateRequestEvent) -> 'BrowserStateSummary':
        from system.browser.views import BrowserStateSummary, PageInfo
        self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: STARTING browser state request')
        page_url = await self.browser_session.get_current_page_url()
        self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Got page URL: {page_url}')
        if self.browser_session.agent_focus_target_id:
            self.logger.debug(f'Current page URL: {page_url}, target_id: {self.browser_session.agent_focus_target_id}')
        not_a_meaningful_website = page_url.lower().split(':', 1)[0] not in ('http', 'https')
        pending_requests_before_wait = []
        if not not_a_meaningful_website:
            try:
                pending_requests_before_wait = await self._get_pending_network_requests()
                if pending_requests_before_wait:
                    self.logger.debug(f'🔍 Found {len(pending_requests_before_wait)} pending requests before stability wait')
            except Exception as e:
                self.logger.debug(f'Failed to get pending requests before wait: {e}')
        pending_requests = pending_requests_before_wait
        if not not_a_meaningful_website:
            self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ⏳ Waiting for page stability...')
            try:
                if pending_requests_before_wait:
                    await asyncio.sleep(0.3)
                self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ Page stability complete')
            except Exception as e:
                self.logger.warning(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Network waiting failed: {e}, continuing anyway...')
        self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: Getting tabs info...')
        tabs_info = await self.browser_session.get_tabs()
        self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Got {len(tabs_info)} tabs')
        self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Tabs info: {tabs_info}')
        try:
            if not_a_meaningful_website:
                self.logger.debug(f'⚡ Skipping BuildDOMTree for empty target: {page_url}')
                self.logger.debug(f'📸 Not taking screenshot for empty page: {page_url} (non-http/https URL)')
                content = SerializedDOMState(_root=None, selector_map={})
                screenshot_b64 = None
                try:
                    page_info = await self._get_page_info()
                except Exception as e:
                    self.logger.debug(f'Failed to get page info from CDP for empty page: {e}, using fallback')
                    viewport = self.browser_session.browser_profile.viewport or {'width': 1280, 'height': 720}
                    page_info = PageInfo(viewport_width=viewport['width'], viewport_height=viewport['height'], page_width=viewport['width'], page_height=viewport['height'], scroll_x=0, scroll_y=0, pixels_above=0, pixels_below=0, pixels_left=0, pixels_right=0)
                return BrowserStateSummary(dom_state=content, url=page_url, title='Empty Tab', tabs=tabs_info, screenshot=screenshot_b64, page_info=page_info, pixels_above=0, pixels_below=0, browser_errors=[], is_pdf_viewer=False, recent_events=self._get_recent_events_str() if event.include_recent_events else None, pending_network_requests=[], pagination_buttons=[], closed_popup_messages=self.browser_session._closed_popup_messages.copy())
            dom_task = None
            screenshot_task = None
            if event.include_dom:
                self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: 🌳 Starting DOM tree build task...')
                previous_state = self.browser_session._cached_browser_state_summary.dom_state if self.browser_session._cached_browser_state_summary else None
                dom_task = create_task_with_error_handling(self._build_dom_tree_without_highlights(previous_state), name='build_dom_tree', logger_instance=self.logger, suppress_exceptions=True)
            if event.include_screenshot:
                self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: 📸 Starting clean screenshot task...')
                screenshot_task = create_task_with_error_handling(self._capture_clean_screenshot(), name='capture_screenshot', logger_instance=self.logger, suppress_exceptions=True)
            content = None
            screenshot_b64 = None
            if dom_task:
                try:
                    content = await dom_task
                    self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ DOM tree build completed')
                except Exception as e:
                    self.logger.warning(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: DOM build failed: {e}, using minimal state')
                    content = SerializedDOMState(_root=None, selector_map={})
            else:
                content = SerializedDOMState(_root=None, selector_map={})
            if screenshot_task:
                try:
                    screenshot_b64 = await screenshot_task
                    self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ Clean screenshot captured')
                except Exception as e:
                    self.logger.warning(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Clean screenshot failed: {e}')
                    screenshot_b64 = None
            if content and content.selector_map and self.browser_session.browser_profile.dom_highlight_elements:
                try:
                    self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: 🎨 Adding browser-side highlights...')
                    await self.browser_session.add_highlights(content.selector_map)
                    self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ Added browser highlights for {len(content.selector_map)} elements')
                except Exception as e:
                    self.logger.warning(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Browser highlighting failed: {e}')
            if not content:
                content = SerializedDOMState(_root=None, selector_map={})
            try:
                self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: Getting page title...')
                title = await asyncio.wait_for(self.browser_session.get_current_page_title(), timeout=1.0)
                self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Got title: {title}')
            except Exception as e:
                self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Failed to get title: {e}')
                title = 'Page'
            try:
                self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: Getting page info from CDP...')
                page_info = await asyncio.wait_for(self._get_page_info(), timeout=1.0)
                self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Got page info from CDP: {page_info}')
            except Exception as e:
                self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Failed to get page info from CDP: {e}, using fallback')
                viewport = self.browser_session.browser_profile.viewport or {'width': 1280, 'height': 720}
                page_info = PageInfo(viewport_width=viewport['width'], viewport_height=viewport['height'], page_width=viewport['width'], page_height=viewport['height'], scroll_x=0, scroll_y=0, pixels_above=0, pixels_below=0, pixels_left=0, pixels_right=0)
            is_pdf_viewer = page_url.endswith('.pdf') or '/pdf/' in page_url
            pagination_buttons_data = []
            if content and content.selector_map:
                pagination_buttons_data = self._detect_pagination_buttons(content.selector_map)
            if screenshot_b64:
                self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: 📸 Creating BrowserStateSummary with screenshot, length: {len(screenshot_b64)}')
            else:
                self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: 📸 Creating BrowserStateSummary WITHOUT screenshot')
            browser_state = BrowserStateSummary(dom_state=content, url=page_url, title=title, tabs=tabs_info, screenshot=screenshot_b64, page_info=page_info, pixels_above=0, pixels_below=0, browser_errors=[], is_pdf_viewer=is_pdf_viewer, recent_events=self._get_recent_events_str() if event.include_recent_events else None, pending_network_requests=pending_requests, pagination_buttons=pagination_buttons_data, closed_popup_messages=self.browser_session._closed_popup_messages.copy())
            self.browser_session._cached_browser_state_summary = browser_state
            if page_info:
                self.browser_session._original_viewport_size = (page_info.viewport_width, page_info.viewport_height)
            self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ COMPLETED - Returning browser state')
            return browser_state
        except Exception as e:
            self.logger.error(f'Failed to get browser state: {e}')
            return BrowserStateSummary(dom_state=SerializedDOMState(_root=None, selector_map={}), url=page_url if 'page_url' in locals() else '', title='Error', tabs=[], screenshot=None, page_info=PageInfo(viewport_width=1280, viewport_height=720, page_width=1280, page_height=720, scroll_x=0, scroll_y=0, pixels_above=0, pixels_below=0, pixels_left=0, pixels_right=0), pixels_above=0, pixels_below=0, browser_errors=[str(e)], is_pdf_viewer=False, recent_events=None, pending_network_requests=[], pagination_buttons=[], closed_popup_messages=self.browser_session._closed_popup_messages.copy() if hasattr(self, 'browser_session') and self.browser_session is not None else [])

    @time_execution_async('build_dom_tree_without_highlights')
    @observe_debug(ignore_input=True, ignore_output=True, name='build_dom_tree_without_highlights')
    async def _build_dom_tree_without_highlights(self, previous_state: SerializedDOMState | None=None) -> SerializedDOMState:
        try:
            self.logger.debug('🔍 DOMWatchdog._build_dom_tree_without_highlights: STARTING DOM tree build')
            if self._dom_service is None:
                self._dom_service = DomService(browser_session=self.browser_session, logger=self.logger, cross_origin_iframes=self.browser_session.browser_profile.cross_origin_iframes, paint_order_filtering=self.browser_session.browser_profile.paint_order_filtering, max_iframes=self.browser_session.browser_profile.max_iframes, max_iframe_depth=self.browser_session.browser_profile.max_iframe_depth)
            self.logger.debug('🔍 DOMWatchdog._build_dom_tree_without_highlights: Calling DomService.get_serialized_dom_tree...')
            start = time.time()
            self.current_dom_state, self.enhanced_dom_tree, timing_info = await self._dom_service.get_serialized_dom_tree(previous_cached_state=previous_state)
            end = time.time()
            total_time_ms = (end - start) * 1000
            self.logger.debug('🔍 DOMWatchdog._build_dom_tree_without_highlights: ✅ DomService.get_serialized_dom_tree completed')
            timing_lines = [f'⏱️ Total DOM tree time: {total_time_ms:.2f}ms', '📊 Timing breakdown:']
            get_all_trees_ms = timing_info.get('get_all_trees_total_ms', 0)
            if get_all_trees_ms > 0:
                timing_lines.append(f'  ├─ get_all_trees: {get_all_trees_ms:.2f}ms')
                iframe_scroll_ms = timing_info.get('iframe_scroll_detection_ms', 0)
                cdp_parallel_ms = timing_info.get('cdp_parallel_calls_ms', 0)
                snapshot_proc_ms = timing_info.get('snapshot_processing_ms', 0)
                if iframe_scroll_ms > 0.01:
                    timing_lines.append(f'  │  ├─ iframe_scroll_detection: {iframe_scroll_ms:.2f}ms')
                if cdp_parallel_ms > 0.01:
                    timing_lines.append(f'  │  ├─ cdp_parallel_calls: {cdp_parallel_ms:.2f}ms')
                if snapshot_proc_ms > 0.01:
                    timing_lines.append(f'  │  └─ snapshot_processing: {snapshot_proc_ms:.2f}ms')
            build_ax_ms = timing_info.get('build_ax_lookup_ms', 0)
            if build_ax_ms > 0.01:
                timing_lines.append(f'  ├─ build_ax_lookup: {build_ax_ms:.2f}ms')
            build_snapshot_ms = timing_info.get('build_snapshot_lookup_ms', 0)
            if build_snapshot_ms > 0.01:
                timing_lines.append(f'  ├─ build_snapshot_lookup: {build_snapshot_ms:.2f}ms')
            construct_tree_ms = timing_info.get('construct_enhanced_tree_ms', 0)
            if construct_tree_ms > 0.01:
                timing_lines.append(f'  ├─ construct_enhanced_tree: {construct_tree_ms:.2f}ms')
            serialize_total_ms = timing_info.get('serialize_accessible_elements_total_ms', 0)
            if serialize_total_ms > 0.01:
                timing_lines.append(f'  ├─ serialize_accessible_elements: {serialize_total_ms:.2f}ms')
                create_simp_ms = timing_info.get('create_simplified_tree_ms', 0)
                paint_order_ms = timing_info.get('calculate_paint_order_ms', 0)
                optimize_ms = timing_info.get('optimize_tree_ms', 0)
                bbox_ms = timing_info.get('bbox_filtering_ms', 0)
                assign_idx_ms = timing_info.get('assign_interactive_indices_ms', 0)
                clickable_ms = timing_info.get('clickable_detection_time_ms', 0)
                if create_simp_ms > 0.01:
                    timing_lines.append(f'  │  ├─ create_simplified_tree: {create_simp_ms:.2f}ms')
                    if clickable_ms > 0.01:
                        timing_lines.append(f'  │  │  └─ clickable_detection: {clickable_ms:.2f}ms')
                if paint_order_ms > 0.01:
                    timing_lines.append(f'  │  ├─ calculate_paint_order: {paint_order_ms:.2f}ms')
                if optimize_ms > 0.01:
                    timing_lines.append(f'  │  ├─ optimize_tree: {optimize_ms:.2f}ms')
                if bbox_ms > 0.01:
                    timing_lines.append(f'  │  ├─ bbox_filtering: {bbox_ms:.2f}ms')
                if assign_idx_ms > 0.01:
                    timing_lines.append(f'  │  └─ assign_interactive_indices: {assign_idx_ms:.2f}ms')
            get_dom_overhead_ms = timing_info.get('get_dom_tree_overhead_ms', 0)
            serialize_overhead_ms = timing_info.get('serialization_overhead_ms', 0)
            get_serialized_overhead_ms = timing_info.get('get_serialized_dom_tree_overhead_ms', 0)
            if get_dom_overhead_ms > 0.1:
                timing_lines.append(f'  ├─ get_dom_tree_overhead: {get_dom_overhead_ms:.2f}ms')
            if serialize_overhead_ms > 0.1:
                timing_lines.append(f'  ├─ serialization_overhead: {serialize_overhead_ms:.2f}ms')
            if get_serialized_overhead_ms > 0.1:
                timing_lines.append(f'  └─ get_serialized_dom_tree_overhead: {get_serialized_overhead_ms:.2f}ms')
            main_operations_ms = get_all_trees_ms + build_ax_ms + build_snapshot_ms + construct_tree_ms + serialize_total_ms + get_dom_overhead_ms + serialize_overhead_ms + get_serialized_overhead_ms
            untracked_time_ms = total_time_ms - main_operations_ms
            if untracked_time_ms > 1.0:
                timing_lines.append(f'  ⚠️  untracked_time: {untracked_time_ms:.2f}ms')
            self.logger.debug('\n'.join(timing_lines))
            self.logger.debug('🔍 DOMWatchdog._build_dom_tree_without_highlights: Updating selector maps...')
            self.selector_map = self.current_dom_state.selector_map
            if self.browser_session:
                self.browser_session.update_cached_selector_map(self.selector_map)
            self.logger.debug(f'🔍 DOMWatchdog._build_dom_tree_without_highlights: ✅ Selector maps updated, {len(self.selector_map)} elements')
            self.logger.debug('🔍 DOMWatchdog._build_dom_tree_without_highlights: ✅ COMPLETED DOM tree build (no JS highlights)')
            return self.current_dom_state
        except Exception as e:
            self.logger.error(f'Failed to build DOM tree without highlights: {e}')
            self.event_bus.dispatch(BrowserErrorEvent(error_type='DOMBuildFailed', message=str(e)))
            raise

    @time_execution_async('capture_clean_screenshot')
    @observe_debug(ignore_input=True, ignore_output=True, name='capture_clean_screenshot')
    async def _capture_clean_screenshot(self) -> str:
        try:
            self.logger.debug('🔍 DOMWatchdog._capture_clean_screenshot: Capturing clean screenshot...')
            await self.browser_session.get_or_create_cdp_session(target_id=self.browser_session.agent_focus_target_id, focus=True)
            handlers = self.event_bus.handlers.get('ScreenshotEvent', [])
            handler_names = [getattr(h, '__name__', str(h)) for h in handlers]
            self.logger.debug(f'📸 ScreenshotEvent handlers registered: {len(handlers)} - {handler_names}')
            screenshot_event = self.event_bus.dispatch(ScreenshotEvent(full_page=False))
            self.logger.debug('📸 Dispatched ScreenshotEvent, waiting for event to complete...')
            await screenshot_event
            screenshot_b64 = await screenshot_event.event_result(raise_if_any=True, raise_if_none=True)
            if screenshot_b64 is None:
                raise RuntimeError('Screenshot handler returned None')
            self.logger.debug('🔍 DOMWatchdog._capture_clean_screenshot: ✅ Clean screenshot captured successfully')
            return str(screenshot_b64)
        except TimeoutError:
            self.logger.warning('📸 Clean screenshot timed out after 6 seconds - no handler registered or slow page?')
            raise
        except Exception as e:
            self.logger.warning(f'📸 Clean screenshot failed: {type(e).__name__}: {e}')
            raise

    def _detect_pagination_buttons(self, selector_map: dict[int, EnhancedDOMTreeNode]) -> list['PaginationButton']:
        from system.browser.views import PaginationButton
        pagination_buttons_data = []
        try:
            self.logger.debug('🔍 DOMWatchdog._detect_pagination_buttons: Detecting pagination buttons...')
            pagination_buttons_raw = DomService.detect_pagination_buttons(selector_map)
            pagination_buttons_data = [PaginationButton(button_type=btn['button_type'], backend_node_id=btn['backend_node_id'], text=btn['text'], selector=btn['selector'], is_disabled=btn['is_disabled']) for btn in pagination_buttons_raw]
            if pagination_buttons_data:
                self.logger.debug(f'🔍 DOMWatchdog._detect_pagination_buttons: Found {len(pagination_buttons_data)} pagination buttons')
        except Exception as e:
            self.logger.warning(f'🔍 DOMWatchdog._detect_pagination_buttons: Pagination detection failed: {e}')
        return pagination_buttons_data

    async def _get_page_info(self) -> 'PageInfo':
        from system.browser.views import PageInfo
        cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=self.browser_session.agent_focus_target_id, focus=True)
        metrics = await asyncio.wait_for(cdp_session.cdp_client.send.Page.getLayoutMetrics(session_id=cdp_session.session_id), timeout=10.0)
        layout_viewport = metrics.get('layoutViewport', {})
        visual_viewport = metrics.get('visualViewport', {})
        css_visual_viewport = metrics.get('cssVisualViewport', {})
        css_layout_viewport = metrics.get('cssLayoutViewport', {})
        content_size = metrics.get('contentSize', {})
        css_width = css_visual_viewport.get('clientWidth', css_layout_viewport.get('clientWidth', 1280.0))
        device_width = visual_viewport.get('clientWidth', css_width)
        device_pixel_ratio = device_width / css_width if css_width > 0 else 1.0
        viewport_width = int(css_layout_viewport.get('clientWidth') or layout_viewport.get('clientWidth', 1280))
        viewport_height = int(css_layout_viewport.get('clientHeight') or layout_viewport.get('clientHeight', 720))
        raw_page_width = content_size.get('width', viewport_width * device_pixel_ratio)
        raw_page_height = content_size.get('height', viewport_height * device_pixel_ratio)
        page_width = int(raw_page_width / device_pixel_ratio)
        page_height = int(raw_page_height / device_pixel_ratio)
        scroll_x = int(css_visual_viewport.get('pageX') or css_layout_viewport.get('pageX', 0))
        scroll_y = int(css_visual_viewport.get('pageY') or css_layout_viewport.get('pageY', 0))
        pixels_above = scroll_y
        pixels_below = max(0, page_height - viewport_height - scroll_y)
        pixels_left = scroll_x
        pixels_right = max(0, page_width - viewport_width - scroll_x)
        page_info = PageInfo(viewport_width=viewport_width, viewport_height=viewport_height, page_width=page_width, page_height=page_height, scroll_x=scroll_x, scroll_y=scroll_y, pixels_above=pixels_above, pixels_below=pixels_below, pixels_left=pixels_left, pixels_right=pixels_right)
        return page_info

    async def get_element_by_index(self, index: int) -> EnhancedDOMTreeNode | None:
        if not self.selector_map:
            await self._build_dom_tree_without_highlights()
        return self.selector_map.get(index) if self.selector_map else None

    def clear_cache(self) -> None:
        self.selector_map = None
        self.current_dom_state = None
        self.enhanced_dom_tree = None

    def is_file_input(self, element: EnhancedDOMTreeNode) -> bool:
        return element.node_name.upper() == 'INPUT' and element.attributes.get('type', '').lower() == 'file'

    @staticmethod
    def is_element_visible_according_to_all_parents(node: EnhancedDOMTreeNode, html_frames: list[EnhancedDOMTreeNode]) -> bool:
        return DomService.is_element_visible_according_to_all_parents(node, html_frames)

    async def __aexit__(self, exc_type, exc_value, traceback):
        if self._dom_service:
            await self._dom_service.__aexit__(exc_type, exc_value, traceback)
            self._dom_service = None

    def __del__(self):
        super().__del__()
        self._dom_service = None