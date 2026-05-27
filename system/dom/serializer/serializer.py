from typing import Any
from system.dom.serializer.clickable_elements import ClickableElementDetector
from system.dom.serializer.paint_order import PaintOrderRemover
from system.dom.utils import cap_text_length
from system.dom.views import DOMRect, DOMSelectorMap, EnhancedDOMTreeNode, NodeType, PropagatingBounds, SerializedDOMState, SimplifiedNode
DISABLED_ELEMENTS = {'style', 'script', 'head', 'meta', 'link', 'title'}
SVG_ELEMENTS = {'path', 'rect', 'g', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'use', 'defs', 'clipPath', 'mask', 'pattern', 'image', 'text', 'tspan'}

class DOMTreeSerializer:
    PROPAGATING_ELEMENTS = [{'tag': 'a', 'role': None}, {'tag': 'button', 'role': None}, {'tag': 'div', 'role': 'button'}, {'tag': 'div', 'role': 'combobox'}, {'tag': 'span', 'role': 'button'}, {'tag': 'span', 'role': 'combobox'}, {'tag': 'input', 'role': 'combobox'}, {'tag': 'input', 'role': 'combobox'}]
    DEFAULT_CONTAINMENT_THRESHOLD = 0.99

    def __init__(self, root_node: EnhancedDOMTreeNode, previous_cached_state: SerializedDOMState | None=None, enable_bbox_filtering: bool=True, containment_threshold: float | None=None, paint_order_filtering: bool=True, session_id: str | None=None):
        self.root_node = root_node
        self._interactive_counter = 1
        self._selector_map: DOMSelectorMap = {}
        self._previous_cached_selector_map = previous_cached_state.selector_map if previous_cached_state else None
        self.timing_info: dict[str, float] = {}
        self._clickable_cache: dict[int, bool] = {}
        self.enable_bbox_filtering = enable_bbox_filtering
        self.containment_threshold = containment_threshold or self.DEFAULT_CONTAINMENT_THRESHOLD
        self.paint_order_filtering = paint_order_filtering
        self.session_id = session_id

    def _safe_parse_number(self, value_str: str, default: float) -> float:
        try:
            return float(value_str)
        except (ValueError, TypeError):
            return default

    def _safe_parse_optional_number(self, value_str: str | None) -> float | None:
        if not value_str:
            return None
        try:
            return float(value_str)
        except (ValueError, TypeError):
            return None

    def serialize_accessible_elements(self) -> tuple[SerializedDOMState, dict[str, float]]:
        import time
        start_total = time.time()
        self._interactive_counter = 1
        self._selector_map = {}
        self._semantic_groups = []
        self._clickable_cache = {}
        start_step1 = time.time()
        simplified_tree = self._create_simplified_tree(self.root_node)
        end_step1 = time.time()
        self.timing_info['create_simplified_tree'] = end_step1 - start_step1
        start_step3 = time.time()
        if self.paint_order_filtering and simplified_tree:
            PaintOrderRemover(simplified_tree).calculate_paint_order()
        end_step3 = time.time()
        self.timing_info['calculate_paint_order'] = end_step3 - start_step3
        start_step2 = time.time()
        optimized_tree = self._optimize_tree(simplified_tree)
        end_step2 = time.time()
        self.timing_info['optimize_tree'] = end_step2 - start_step2
        if self.enable_bbox_filtering and optimized_tree:
            start_step3 = time.time()
            filtered_tree = self._apply_bounding_box_filtering(optimized_tree)
            end_step3 = time.time()
            self.timing_info['bbox_filtering'] = end_step3 - start_step3
        else:
            filtered_tree = optimized_tree
        start_step4 = time.time()
        self._assign_interactive_indices_and_mark_new_nodes(filtered_tree)
        end_step4 = time.time()
        self.timing_info['assign_interactive_indices'] = end_step4 - start_step4
        end_total = time.time()
        self.timing_info['serialize_accessible_elements_total'] = end_total - start_total
        return (SerializedDOMState(_root=filtered_tree, selector_map=self._selector_map), self.timing_info)

    def _add_compound_components(self, simplified: SimplifiedNode, node: EnhancedDOMTreeNode) -> None:
        if node.tag_name not in ['input', 'select', 'details', 'audio', 'video']:
            return
        if node.tag_name == 'input':
            if not node.attributes or node.attributes.get('type') not in ['date', 'time', 'datetime-local', 'month', 'week', 'range', 'number', 'color', 'file']:
                return
        elif not node.ax_node or not node.ax_node.child_ids:
            return
        element_type = node.tag_name
        input_type = node.attributes.get('type', '') if node.attributes else ''
        if element_type == 'input':
            if input_type in ['date', 'time', 'datetime-local', 'month', 'week']:
                pass
            elif input_type == 'range':
                min_val = node.attributes.get('min', '0') if node.attributes else '0'
                max_val = node.attributes.get('max', '100') if node.attributes else '100'
                node._compound_children.append({'role': 'slider', 'name': 'Value', 'valuemin': self._safe_parse_number(min_val, 0.0), 'valuemax': self._safe_parse_number(max_val, 100.0), 'valuenow': None})
                simplified.is_compound_component = True
            elif input_type == 'number':
                min_val = node.attributes.get('min') if node.attributes else None
                max_val = node.attributes.get('max') if node.attributes else None
                node._compound_children.extend([{'role': 'button', 'name': 'Increment', 'valuemin': None, 'valuemax': None, 'valuenow': None}, {'role': 'button', 'name': 'Decrement', 'valuemin': None, 'valuemax': None, 'valuenow': None}, {'role': 'textbox', 'name': 'Value', 'valuemin': self._safe_parse_optional_number(min_val), 'valuemax': self._safe_parse_optional_number(max_val), 'valuenow': None}])
                simplified.is_compound_component = True
            elif input_type == 'color':
                node._compound_children.extend([{'role': 'textbox', 'name': 'Hex Value', 'valuemin': None, 'valuemax': None, 'valuenow': None}, {'role': 'button', 'name': 'Color Picker', 'valuemin': None, 'valuemax': None, 'valuenow': None}])
                simplified.is_compound_component = True
            elif input_type == 'file':
                multiple = 'multiple' in node.attributes if node.attributes else False
                current_value = 'None'
                if node.ax_node and node.ax_node.properties:
                    for prop in node.ax_node.properties:
                        if prop.name == 'valuetext' and prop.value:
                            value_str = str(prop.value).strip()
                            if value_str and value_str.lower() not in ['', 'no file chosen', 'no file selected']:
                                current_value = value_str
                            break
                        elif prop.name == 'value' and prop.value:
                            value_str = str(prop.value).strip()
                            if value_str:
                                if '\\' in value_str:
                                    current_value = value_str.split('\\')[-1]
                                elif '/' in value_str:
                                    current_value = value_str.split('/')[-1]
                                else:
                                    current_value = value_str
                                break
                node._compound_children.extend([{'role': 'button', 'name': 'Browse Files', 'valuemin': None, 'valuemax': None, 'valuenow': None}, {'role': 'textbox', 'name': f"{('Files' if multiple else 'File')} Selected", 'valuemin': None, 'valuemax': None, 'valuenow': current_value}])
                simplified.is_compound_component = True
        elif element_type == 'select':
            base_components = [{'role': 'button', 'name': 'Dropdown Toggle', 'valuemin': None, 'valuemax': None, 'valuenow': None}]
            options_info = self._extract_select_options(node)
            if options_info:
                options_component = {'role': 'listbox', 'name': 'Options', 'valuemin': None, 'valuemax': None, 'valuenow': None, 'options_count': options_info['count'], 'first_options': options_info['first_options']}
                if options_info['format_hint']:
                    options_component['format_hint'] = options_info['format_hint']
                base_components.append(options_component)
            else:
                base_components.append({'role': 'listbox', 'name': 'Options', 'valuemin': None, 'valuemax': None, 'valuenow': None})
            node._compound_children.extend(base_components)
            simplified.is_compound_component = True
        elif element_type == 'details':
            node._compound_children.extend([{'role': 'button', 'name': 'Toggle Disclosure', 'valuemin': None, 'valuemax': None, 'valuenow': None}, {'role': 'region', 'name': 'Content Area', 'valuemin': None, 'valuemax': None, 'valuenow': None}])
            simplified.is_compound_component = True
        elif element_type == 'audio':
            node._compound_children.extend([{'role': 'button', 'name': 'Play/Pause', 'valuemin': None, 'valuemax': None, 'valuenow': None}, {'role': 'slider', 'name': 'Progress', 'valuemin': 0, 'valuemax': 100, 'valuenow': None}, {'role': 'button', 'name': 'Mute', 'valuemin': None, 'valuemax': None, 'valuenow': None}, {'role': 'slider', 'name': 'Volume', 'valuemin': 0, 'valuemax': 100, 'valuenow': None}])
            simplified.is_compound_component = True
        elif element_type == 'video':
            node._compound_children.extend([{'role': 'button', 'name': 'Play/Pause', 'valuemin': None, 'valuemax': None, 'valuenow': None}, {'role': 'slider', 'name': 'Progress', 'valuemin': 0, 'valuemax': 100, 'valuenow': None}, {'role': 'button', 'name': 'Mute', 'valuemin': None, 'valuemax': None, 'valuenow': None}, {'role': 'slider', 'name': 'Volume', 'valuemin': 0, 'valuemax': 100, 'valuenow': None}, {'role': 'button', 'name': 'Fullscreen', 'valuemin': None, 'valuemax': None, 'valuenow': None}])
            simplified.is_compound_component = True

    def _extract_select_options(self, select_node: EnhancedDOMTreeNode) -> dict[str, Any] | None:
        if not select_node.children:
            return None
        options = []
        option_values = []

        def extract_options_recursive(node: EnhancedDOMTreeNode) -> None:
            if node.tag_name.lower() == 'option':
                option_text = ''
                option_value = ''
                if node.attributes and 'value' in node.attributes:
                    option_value = str(node.attributes['value']).strip()

                def get_direct_text_content(n: EnhancedDOMTreeNode) -> str:
                    text = ''
                    for child in n.children:
                        if child.node_type == NodeType.TEXT_NODE and child.node_value:
                            text += child.node_value.strip() + ' '
                    return text.strip()
                option_text = get_direct_text_content(node)
                if not option_value and option_text:
                    option_value = option_text
                if option_text or option_value:
                    options.append({'text': option_text, 'value': option_value})
                    option_values.append(option_value)
            elif node.tag_name.lower() == 'optgroup':
                for child in node.children:
                    extract_options_recursive(child)
            else:
                for child in node.children:
                    extract_options_recursive(child)
        for child in select_node.children:
            extract_options_recursive(child)
        if not options:
            return None
        first_options = []
        for option in options[:4]:
            display_text = option['text'] if option['text'] else option['value']
            if display_text:
                text = display_text[:30] + ('...' if len(display_text) > 30 else '')
                first_options.append(text)
        if len(options) > 4:
            first_options.append(f'... {len(options) - 4} more options...')
        format_hint = None
        if len(option_values) >= 2:
            if all((val.isdigit() for val in option_values[:5] if val)):
                format_hint = 'numeric'
            elif all((len(val) == 2 and val.isupper() for val in option_values[:5] if val)):
                format_hint = 'country/state codes'
            elif all(('/' in val or '-' in val for val in option_values[:5] if val)):
                format_hint = 'date/path format'
            elif any(('@' in val for val in option_values[:5] if val)):
                format_hint = 'email addresses'
        return {'count': len(options), 'first_options': first_options, 'format_hint': format_hint}

    def _is_interactive_cached(self, node: EnhancedDOMTreeNode) -> bool:
        if node.node_id not in self._clickable_cache:
            import time
            start_time = time.time()
            result = ClickableElementDetector.is_interactive(node)
            end_time = time.time()
            if 'clickable_detection_time' not in self.timing_info:
                self.timing_info['clickable_detection_time'] = 0
            self.timing_info['clickable_detection_time'] += end_time - start_time
            self._clickable_cache[node.node_id] = result
        return self._clickable_cache[node.node_id]

    def _create_simplified_tree(self, node: EnhancedDOMTreeNode, depth: int=0) -> SimplifiedNode | None:
        if node.node_type == NodeType.DOCUMENT_NODE:
            for child in node.children_and_shadow_roots:
                simplified_child = self._create_simplified_tree(child, depth + 1)
                if simplified_child:
                    return simplified_child
            return None
        if node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
            simplified = SimplifiedNode(original_node=node, children=[])
            for child in node.children_and_shadow_roots:
                simplified_child = self._create_simplified_tree(child, depth + 1)
                if simplified_child:
                    simplified.children.append(simplified_child)
            return simplified if simplified.children else SimplifiedNode(original_node=node, children=[])
        elif node.node_type == NodeType.ELEMENT_NODE:
            if node.node_name.lower() in DISABLED_ELEMENTS:
                return None
            if node.node_name.lower() in SVG_ELEMENTS:
                return None
            attributes = node.attributes or {}
            exclude_attr = None
            attr_type = None
            if self.session_id:
                session_specific_attr = f'data-browser-use-exclude-{self.session_id}'
                exclude_attr = attributes.get(session_specific_attr)
                if exclude_attr:
                    attr_type = 'session-specific'
            if not exclude_attr:
                exclude_attr = attributes.get('data-browser-use-exclude')
            if isinstance(exclude_attr, str) and exclude_attr.lower() == 'true':
                return None
            if node.node_name == 'IFRAME' or node.node_name == 'FRAME':
                if node.content_document:
                    simplified = SimplifiedNode(original_node=node, children=[])
                    for child in node.content_document.children_nodes or []:
                        simplified_child = self._create_simplified_tree(child, depth + 1)
                        if simplified_child is not None:
                            simplified.children.append(simplified_child)
                    return simplified
            is_visible = node.is_visible
            is_scrollable = node.is_actually_scrollable
            has_shadow_content = bool(node.children_and_shadow_roots)
            is_shadow_host = any((child.node_type == NodeType.DOCUMENT_FRAGMENT_NODE for child in node.children_and_shadow_roots))
            if not is_visible and node.attributes:
                has_validation_attrs = any((attr.startswith(('aria-', 'pseudo')) for attr in node.attributes.keys()))
                if has_validation_attrs:
                    is_visible = True
            is_file_input = node.tag_name and node.tag_name.lower() == 'input' and node.attributes and (node.attributes.get('type') == 'file')
            if not is_visible and is_file_input:
                is_visible = True
            if is_visible or is_scrollable or has_shadow_content or is_shadow_host:
                simplified = SimplifiedNode(original_node=node, children=[], is_shadow_host=is_shadow_host)
                for child in node.children_and_shadow_roots:
                    simplified_child = self._create_simplified_tree(child, depth + 1)
                    if simplified_child:
                        simplified.children.append(simplified_child)
                self._add_compound_components(simplified, node)
                if is_shadow_host and simplified.children:
                    return simplified
                if is_visible or is_scrollable or simplified.children:
                    return simplified
        elif node.node_type == NodeType.TEXT_NODE:
            is_visible = node.snapshot_node and node.is_visible
            if is_visible and node.node_value and node.node_value.strip() and (len(node.node_value.strip()) > 1):
                return SimplifiedNode(original_node=node, children=[])
        return None

    def _optimize_tree(self, node: SimplifiedNode | None) -> SimplifiedNode | None:
        if not node:
            return None
        optimized_children = []
        for child in node.children:
            optimized_child = self._optimize_tree(child)
            if optimized_child:
                optimized_children.append(optimized_child)
        node.children = optimized_children
        is_visible = node.original_node.snapshot_node and node.original_node.is_visible
        is_file_input = node.original_node.tag_name and node.original_node.tag_name.lower() == 'input' and node.original_node.attributes and (node.original_node.attributes.get('type') == 'file')
        if is_visible or node.original_node.is_actually_scrollable or node.original_node.node_type == NodeType.TEXT_NODE or node.children or is_file_input:
            return node
        return None

    def _collect_interactive_elements(self, node: SimplifiedNode, elements: list[SimplifiedNode]) -> None:
        is_interactive = self._is_interactive_cached(node.original_node)
        is_visible = node.original_node.snapshot_node and node.original_node.is_visible
        if is_interactive and is_visible:
            elements.append(node)
        for child in node.children:
            self._collect_interactive_elements(child, elements)

    def _has_interactive_descendants(self, node: SimplifiedNode) -> bool:
        for child in node.children:
            if self._is_interactive_cached(child.original_node):
                return True
            if self._has_interactive_descendants(child):
                return True
        return False

    def _is_inside_shadow_dom(self, node: SimplifiedNode) -> bool:
        current = node.original_node.parent_node
        while current is not None:
            if current.node_type == NodeType.DOCUMENT_FRAGMENT_NODE and current.shadow_root_type is not None:
                return True
            current = current.parent_node
        return False

    def _assign_interactive_indices_and_mark_new_nodes(self, node: SimplifiedNode | None) -> None:
        if not node:
            return
        if not node.excluded_by_parent and (not node.ignored_by_paint_order):
            is_interactive_assign = self._is_interactive_cached(node.original_node)
            is_visible = node.original_node.snapshot_node and node.original_node.is_visible
            is_scrollable = node.original_node.is_actually_scrollable
            if is_interactive_assign and (not node.original_node.snapshot_node):
                import logging
                logger = logging.getLogger('system.dom.serializer')
                attrs = node.original_node.attributes or {}
                attr_str = f"name={attrs.get('name', '')} id={attrs.get('id', '')} type={attrs.get('type', '')}"
                in_shadow = self._is_inside_shadow_dom(node)
                if in_shadow and node.original_node.tag_name and (node.original_node.tag_name.lower() in ['input', 'button', 'select', 'textarea', 'a']):
                    logger.debug(f'🔍 INCLUDING shadow DOM <{node.original_node.tag_name}> (no snapshot_node but in shadow DOM): backendNodeId={node.original_node.backend_node_id} {attr_str}')
                else:
                    logger.debug(f'🔍 SKIPPING interactive <{node.original_node.tag_name}> (no snapshot_node, not in shadow DOM): backendNodeId={node.original_node.backend_node_id} {attr_str}')
            is_file_input = node.original_node.tag_name and node.original_node.tag_name.lower() == 'input' and node.original_node.attributes and (node.original_node.attributes.get('type') == 'file')
            is_shadow_dom_element = is_interactive_assign and (not node.original_node.snapshot_node) and node.original_node.tag_name and (node.original_node.tag_name.lower() in ['input', 'button', 'select', 'textarea', 'a']) and self._is_inside_shadow_dom(node)
            should_make_interactive = False
            if is_scrollable:
                attrs = node.original_node.attributes or {}
                role = attrs.get('role', '').lower()
                tag_name = (node.original_node.tag_name or '').lower()
                class_attr = attrs.get('class', '').lower()
                class_list = class_attr.split() if class_attr else []
                is_dropdown_by_role = role in ('listbox', 'menu', 'combobox', 'menubar', 'tree', 'grid')
                is_dropdown_by_tag = tag_name == 'select'
                is_dropdown_by_class = 'dropdown' in class_list or 'dropdown-menu' in class_list or 'select-menu' in class_list or ('ui' in class_list and 'dropdown' in class_attr)
                is_dropdown_container = is_dropdown_by_role or is_dropdown_by_tag or is_dropdown_by_class
                if is_dropdown_container:
                    should_make_interactive = True
                else:
                    has_interactive_desc = self._has_interactive_descendants(node)
                    if not has_interactive_desc:
                        should_make_interactive = True
            elif is_interactive_assign and (is_visible or is_file_input or is_shadow_dom_element):
                should_make_interactive = True
            if should_make_interactive:
                node.is_interactive = True
                self._selector_map[node.original_node.backend_node_id] = node.original_node
                self._interactive_counter += 1
                if node.is_compound_component:
                    node.is_new = True
                elif self._previous_cached_selector_map:
                    previous_backend_node_ids = {node.backend_node_id for node in self._previous_cached_selector_map.values()}
                    if node.original_node.backend_node_id not in previous_backend_node_ids:
                        node.is_new = True
        for child in node.children:
            self._assign_interactive_indices_and_mark_new_nodes(child)

    def _apply_bounding_box_filtering(self, node: SimplifiedNode | None) -> SimplifiedNode | None:
        if not node:
            return None
        self._filter_tree_recursive(node, active_bounds=None, depth=0)
        excluded_count = self._count_excluded_nodes(node)
        if excluded_count > 0:
            import logging
            logging.debug(f'BBox filtering excluded {excluded_count} nodes')
        return node

    def _filter_tree_recursive(self, node: SimplifiedNode, active_bounds: PropagatingBounds | None=None, depth: int=0):
        if active_bounds and self._should_exclude_child(node, active_bounds):
            node.excluded_by_parent = True
        new_bounds = None
        tag = node.original_node.tag_name.lower()
        role = node.original_node.attributes.get('role') if node.original_node.attributes else None
        attributes = {'tag': tag, 'role': role}
        if self._is_propagating_element(attributes):
            if node.original_node.snapshot_node and node.original_node.snapshot_node.bounds:
                new_bounds = PropagatingBounds(tag=tag, bounds=node.original_node.snapshot_node.bounds, node_id=node.original_node.node_id, depth=depth)
        propagate_bounds = new_bounds if new_bounds else active_bounds
        for child in node.children:
            self._filter_tree_recursive(child, propagate_bounds, depth + 1)

    def _should_exclude_child(self, node: SimplifiedNode, active_bounds: PropagatingBounds) -> bool:
        if node.original_node.node_type == NodeType.TEXT_NODE:
            return False
        if not node.original_node.snapshot_node or not node.original_node.snapshot_node.bounds:
            return False
        child_bounds = node.original_node.snapshot_node.bounds
        if not self._is_contained(child_bounds, active_bounds.bounds, self.containment_threshold):
            return False
        child_tag = node.original_node.tag_name.lower()
        child_role = node.original_node.attributes.get('role') if node.original_node.attributes else None
        child_attributes = {'tag': child_tag, 'role': child_role}
        if child_tag in ['input', 'select', 'textarea', 'label']:
            return False
        if self._is_propagating_element(child_attributes):
            return False
        if node.original_node.attributes and 'onclick' in node.original_node.attributes:
            return False
        if node.original_node.attributes:
            aria_label = node.original_node.attributes.get('aria-label')
            if aria_label and aria_label.strip():
                return False
        if node.original_node.attributes:
            role = node.original_node.attributes.get('role')
            if role in ['button', 'link', 'checkbox', 'radio', 'tab', 'menuitem', 'option']:
                return False
        return True

    def _is_contained(self, child: DOMRect, parent: DOMRect, threshold: float) -> bool:
        x_overlap = max(0, min(child.x + child.width, parent.x + parent.width) - max(child.x, parent.x))
        y_overlap = max(0, min(child.y + child.height, parent.y + parent.height) - max(child.y, parent.y))
        intersection_area = x_overlap * y_overlap
        child_area = child.width * child.height
        if child_area == 0:
            return False
        containment_ratio = intersection_area / child_area
        return containment_ratio >= threshold

    def _count_excluded_nodes(self, node: SimplifiedNode, count: int=0) -> int:
        if hasattr(node, 'excluded_by_parent') and node.excluded_by_parent:
            count += 1
        for child in node.children:
            count = self._count_excluded_nodes(child, count)
        return count

    def _is_propagating_element(self, attributes: dict[str, str | None]) -> bool:
        keys_to_check = ['tag', 'role']
        for pattern in self.PROPAGATING_ELEMENTS:
            check = [pattern.get(key) is None or pattern.get(key) == attributes.get(key) for key in keys_to_check]
            if all(check):
                return True
        return False

    @staticmethod
    def serialize_tree(node: SimplifiedNode | None, include_attributes: list[str], depth: int=0) -> str:
        if not node:
            return ''
        if hasattr(node, 'excluded_by_parent') and node.excluded_by_parent:
            formatted_text = []
            for child in node.children:
                child_text = DOMTreeSerializer.serialize_tree(child, include_attributes, depth)
                if child_text:
                    formatted_text.append(child_text)
            return '\n'.join(formatted_text)
        formatted_text = []
        depth_str = depth * '\t'
        next_depth = depth
        if node.original_node.node_type == NodeType.ELEMENT_NODE:
            if not node.should_display:
                for child in node.children:
                    child_text = DOMTreeSerializer.serialize_tree(child, include_attributes, depth)
                    if child_text:
                        formatted_text.append(child_text)
                return '\n'.join(formatted_text)
            if node.original_node.tag_name.lower() == 'svg':
                shadow_prefix = ''
                if node.is_shadow_host:
                    has_closed_shadow = any((child.original_node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE and child.original_node.shadow_root_type and (child.original_node.shadow_root_type.lower() == 'closed') for child in node.children))
                    shadow_prefix = '|SHADOW(closed)|' if has_closed_shadow else '|SHADOW(open)|'
                line = f'{depth_str}{shadow_prefix}'
                if node.is_interactive:
                    new_prefix = '*' if node.is_new else ''
                    line += f'{new_prefix}[{node.original_node.backend_node_id}]'
                line += '<svg'
                attributes_html_str = DOMTreeSerializer._build_attributes_string(node.original_node, include_attributes, '')
                if attributes_html_str:
                    line += f' {attributes_html_str}'
                line += ' /> <!-- SVG content collapsed -->'
                formatted_text.append(line)
                return '\n'.join(formatted_text)
            is_any_scrollable = node.original_node.is_actually_scrollable or node.original_node.is_scrollable
            should_show_scroll = node.original_node.should_show_scroll_info
            if node.is_interactive or is_any_scrollable or node.original_node.tag_name.upper() == 'IFRAME' or (node.original_node.tag_name.upper() == 'FRAME'):
                next_depth += 1
                text_content = ''
                attributes_html_str = DOMTreeSerializer._build_attributes_string(node.original_node, include_attributes, text_content)
                if node.original_node._compound_children:
                    compound_info = []
                    for child_info in node.original_node._compound_children:
                        parts = []
                        if child_info['name']:
                            parts.append(f"name={child_info['name']}")
                        if child_info['role']:
                            parts.append(f"role={child_info['role']}")
                        if child_info['valuemin'] is not None:
                            parts.append(f"min={child_info['valuemin']}")
                        if child_info['valuemax'] is not None:
                            parts.append(f"max={child_info['valuemax']}")
                        if child_info['valuenow'] is not None:
                            parts.append(f"current={child_info['valuenow']}")
                        if 'options_count' in child_info and child_info['options_count'] is not None:
                            parts.append(f"count={child_info['options_count']}")
                        if 'first_options' in child_info and child_info['first_options']:
                            options_str = '|'.join(child_info['first_options'][:4])
                            parts.append(f'options={options_str}')
                        if 'format_hint' in child_info and child_info['format_hint']:
                            parts.append(f"format={child_info['format_hint']}")
                        if parts:
                            compound_info.append(f"({','.join(parts)})")
                    if compound_info:
                        compound_attr = f"compound_components={','.join(compound_info)}"
                        if attributes_html_str:
                            attributes_html_str += f' {compound_attr}'
                        else:
                            attributes_html_str = compound_attr
                shadow_prefix = ''
                if node.is_shadow_host:
                    has_closed_shadow = any((child.original_node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE and child.original_node.shadow_root_type and (child.original_node.shadow_root_type.lower() == 'closed') for child in node.children))
                    shadow_prefix = '|SHADOW(closed)|' if has_closed_shadow else '|SHADOW(open)|'
                if should_show_scroll and (not node.is_interactive):
                    line = f'{depth_str}{shadow_prefix}|scroll element|<{node.original_node.tag_name}'
                elif node.is_interactive:
                    new_prefix = '*' if node.is_new else ''
                    scroll_prefix = '|scroll element[' if should_show_scroll else '['
                    line = f'{depth_str}{shadow_prefix}{new_prefix}{scroll_prefix}{node.original_node.backend_node_id}]<{node.original_node.tag_name}'
                elif node.original_node.tag_name.upper() == 'IFRAME':
                    line = f'{depth_str}{shadow_prefix}|IFRAME|<{node.original_node.tag_name}'
                elif node.original_node.tag_name.upper() == 'FRAME':
                    line = f'{depth_str}{shadow_prefix}|FRAME|<{node.original_node.tag_name}'
                else:
                    line = f'{depth_str}{shadow_prefix}<{node.original_node.tag_name}'
                if attributes_html_str:
                    line += f' {attributes_html_str}'
                line += ' />'
                if should_show_scroll:
                    scroll_info_text = node.original_node.get_scroll_info_text()
                    if scroll_info_text:
                        line += f' ({scroll_info_text})'
                formatted_text.append(line)
        elif node.original_node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
            if node.original_node.shadow_root_type and node.original_node.shadow_root_type.lower() == 'closed':
                formatted_text.append(f'{depth_str}Closed Shadow')
            else:
                formatted_text.append(f'{depth_str}Open Shadow')
            next_depth += 1
            for child in node.children:
                child_text = DOMTreeSerializer.serialize_tree(child, include_attributes, next_depth)
                if child_text:
                    formatted_text.append(child_text)
            if node.children:
                formatted_text.append(f'{depth_str}Shadow End')
        elif node.original_node.node_type == NodeType.TEXT_NODE:
            is_visible = node.original_node.snapshot_node and node.original_node.is_visible
            if is_visible and node.original_node.node_value and node.original_node.node_value.strip() and (len(node.original_node.node_value.strip()) > 1):
                clean_text = node.original_node.node_value.strip()
                formatted_text.append(f'{depth_str}{clean_text}')
        if node.original_node.node_type != NodeType.DOCUMENT_FRAGMENT_NODE:
            for child in node.children:
                child_text = DOMTreeSerializer.serialize_tree(child, include_attributes, next_depth)
                if child_text:
                    formatted_text.append(child_text)
            if node.original_node.node_type == NodeType.ELEMENT_NODE and node.original_node.tag_name and (node.original_node.tag_name.upper() in ('IFRAME', 'FRAME')):
                if node.original_node.hidden_elements_info:
                    hidden = node.original_node.hidden_elements_info
                    hint_lines = [f'{depth_str}... ({len(hidden)} more elements below - scroll to reveal):']
                    for elem in hidden:
                        hint_lines.append(f'''{depth_str}    <{elem['tag']}> "{elem['text']}" ~{elem['pages']} pages down''')
                    formatted_text.extend(hint_lines)
                elif node.original_node.has_hidden_content:
                    formatted_text.append(f'{depth_str}... (more content below viewport - scroll to reveal)')
        return '\n'.join(formatted_text)

    @staticmethod
    def _build_attributes_string(node: EnhancedDOMTreeNode, include_attributes: list[str], text: str) -> str:
        attributes_to_include = {}
        if node.attributes:
            attributes_to_include.update({key: str(value).strip() for key, value in node.attributes.items() if key in include_attributes and str(value).strip() != ''})
        if node.tag_name and node.tag_name.lower() == 'input' and node.attributes:
            input_type = node.attributes.get('type', '').lower()
            if input_type in ['date', 'time', 'datetime-local', 'month', 'week']:
                format_map = {'date': 'YYYY-MM-DD', 'time': 'HH:MM', 'datetime-local': 'YYYY-MM-DDTHH:MM', 'month': 'YYYY-MM', 'week': 'YYYY-W##'}
                attributes_to_include['format'] = format_map[input_type]
            if 'placeholder' in include_attributes and 'placeholder' not in attributes_to_include:
                if input_type == 'date':
                    attributes_to_include['placeholder'] = 'YYYY-MM-DD'
                elif input_type == 'time':
                    attributes_to_include['placeholder'] = 'HH:MM'
                elif input_type == 'datetime-local':
                    attributes_to_include['placeholder'] = 'YYYY-MM-DDTHH:MM'
                elif input_type == 'month':
                    attributes_to_include['placeholder'] = 'YYYY-MM'
                elif input_type == 'week':
                    attributes_to_include['placeholder'] = 'YYYY-W##'
                elif input_type == 'tel' and 'pattern' not in attributes_to_include:
                    attributes_to_include['placeholder'] = '123-456-7890'
                elif input_type in {'text', ''}:
                    class_attr = node.attributes.get('class', '').lower()
                    if 'uib-datepicker-popup' in node.attributes:
                        date_format = node.attributes.get('uib-datepicker-popup', '')
                        if date_format:
                            attributes_to_include['expected_format'] = date_format
                            attributes_to_include['format'] = date_format
                    elif any((indicator in class_attr for indicator in ['datepicker', 'datetimepicker', 'daterangepicker'])):
                        date_format = node.attributes.get('data-date-format', '')
                        if date_format:
                            attributes_to_include['placeholder'] = date_format
                            attributes_to_include['format'] = date_format
                        else:
                            attributes_to_include['placeholder'] = 'mm/dd/yyyy'
                            attributes_to_include['format'] = 'mm/dd/yyyy'
                    elif any((attr in node.attributes for attr in ['data-datepicker'])):
                        date_format = node.attributes.get('data-date-format', '')
                        if date_format:
                            attributes_to_include['placeholder'] = date_format
                            attributes_to_include['format'] = date_format
                        else:
                            attributes_to_include['placeholder'] = 'mm/dd/yyyy'
                            attributes_to_include['format'] = 'mm/dd/yyyy'
        if node.ax_node and node.ax_node.properties:
            for prop in node.ax_node.properties:
                try:
                    if prop.name in include_attributes and prop.value is not None:
                        if isinstance(prop.value, bool):
                            attributes_to_include[prop.name] = str(prop.value).lower()
                        else:
                            prop_value_str = str(prop.value).strip()
                            if prop_value_str:
                                attributes_to_include[prop.name] = prop_value_str
                except (AttributeError, ValueError):
                    continue
        if node.tag_name and node.tag_name.lower() in ['input', 'textarea', 'select']:
            if node.ax_node and node.ax_node.properties:
                for prop in node.ax_node.properties:
                    if prop.name == 'valuetext' and prop.value:
                        value_str = str(prop.value).strip()
                        if value_str:
                            attributes_to_include['value'] = value_str
                            break
                    elif prop.name == 'value' and prop.value:
                        value_str = str(prop.value).strip()
                        if value_str:
                            attributes_to_include['value'] = value_str
                            break
        if not attributes_to_include:
            return ''
        ordered_keys = [key for key in include_attributes if key in attributes_to_include]
        if len(ordered_keys) > 1:
            keys_to_remove = set()
            seen_values = {}
            protected_attrs = {'format', 'expected_format', 'placeholder', 'value', 'aria-label', 'title'}
            for key in ordered_keys:
                value = attributes_to_include[key]
                if len(value) > 5:
                    if value in seen_values and key not in protected_attrs:
                        keys_to_remove.add(key)
                    else:
                        seen_values[value] = key
            for key in keys_to_remove:
                del attributes_to_include[key]
        role = node.ax_node.role if node.ax_node else None
        if role and node.node_name == role:
            attributes_to_include.pop('role', None)
        if 'type' in attributes_to_include and attributes_to_include['type'].lower() == node.node_name.lower():
            del attributes_to_include['type']
        if 'invalid' in attributes_to_include and attributes_to_include['invalid'].lower() == 'false':
            del attributes_to_include['invalid']
        boolean_attrs = {'required'}
        for attr in boolean_attrs:
            if attr in attributes_to_include and attributes_to_include[attr].lower() in {'false', '0', 'no'}:
                del attributes_to_include[attr]
        if 'expanded' in attributes_to_include and 'aria-expanded' in attributes_to_include:
            del attributes_to_include['aria-expanded']
        attrs_to_remove_if_text_matches = ['aria-label', 'placeholder', 'title']
        for attr in attrs_to_remove_if_text_matches:
            if attributes_to_include.get(attr) and attributes_to_include.get(attr, '').strip().lower() == text.strip().lower():
                del attributes_to_include[attr]
        if attributes_to_include:
            formatted_attrs = []
            for key, value in attributes_to_include.items():
                capped_value = cap_text_length(value, 100)
                if not capped_value:
                    formatted_attrs.append(f"{key}=''")
                else:
                    formatted_attrs.append(f'{key}={capped_value}')
            return ' '.join(formatted_attrs)
        return ''