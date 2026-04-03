from system.dom.views import EnhancedDOMTreeNode, NodeType

class ClickableElementDetector:

    @staticmethod
    def is_interactive(node: EnhancedDOMTreeNode) -> bool:

        def has_form_control_descendant(element: EnhancedDOMTreeNode, max_depth: int=2) -> bool:
            if max_depth <= 0:
                return False
            for child in element.children_and_shadow_roots:
                if child.node_type != NodeType.ELEMENT_NODE:
                    continue
                tag_name = child.tag_name
                if tag_name in {'input', 'select', 'textarea'}:
                    return True
                if has_form_control_descendant(child, max_depth=max_depth - 1):
                    return True
            return False
        if node.node_type != NodeType.ELEMENT_NODE:
            return False
        if node.tag_name in {'html', 'body'}:
            return False
        if node.has_js_click_listener:
            return True
        if node.tag_name and node.tag_name.upper() == 'IFRAME' or node.tag_name.upper() == 'FRAME':
            if node.snapshot_node and node.snapshot_node.bounds:
                width = node.snapshot_node.bounds.width
                height = node.snapshot_node.bounds.height
                if width > 100 and height > 100:
                    return True
        if node.tag_name == 'label':
            if node.attributes and node.attributes.get('for'):
                return False
            if has_form_control_descendant(node, max_depth=2):
                return True
        if node.tag_name == 'span':
            if has_form_control_descendant(node, max_depth=2):
                return True
        if node.attributes:
            search_indicators = {'search', 'magnify', 'glass', 'lookup', 'find', 'query', 'search-icon', 'search-btn', 'search-button', 'searchbox'}
            class_list = node.attributes.get('class', '').lower().split()
            if any((indicator in ' '.join(class_list) for indicator in search_indicators)):
                return True
            element_id = node.attributes.get('id', '').lower()
            if any((indicator in element_id for indicator in search_indicators)):
                return True
            for attr_name, attr_value in node.attributes.items():
                if attr_name.startswith('data-') and any((indicator in attr_value.lower() for indicator in search_indicators)):
                    return True
        if node.ax_node and node.ax_node.properties:
            for prop in node.ax_node.properties:
                try:
                    if prop.name == 'disabled' and prop.value:
                        return False
                    if prop.name == 'hidden' and prop.value:
                        return False
                    if prop.name in ['focusable', 'editable', 'settable'] and prop.value:
                        return True
                    if prop.name in ['checked', 'expanded', 'pressed', 'selected']:
                        return True
                    if prop.name in ['required', 'autocomplete'] and prop.value:
                        return True
                    if prop.name == 'keyshortcuts' and prop.value:
                        return True
                except (AttributeError, ValueError):
                    continue
        interactive_tags = {'button', 'input', 'select', 'textarea', 'a', 'details', 'summary', 'option', 'optgroup'}
        if node.tag_name and node.tag_name.lower() in interactive_tags:
            return True
        if node.attributes:
            interactive_attributes = {'onclick', 'onmousedown', 'onmouseup', 'onkeydown', 'onkeyup', 'tabindex'}
            if any((attr in node.attributes for attr in interactive_attributes)):
                return True
            if 'role' in node.attributes:
                interactive_roles = {'button', 'link', 'menuitem', 'option', 'radio', 'checkbox', 'tab', 'textbox', 'combobox', 'slider', 'spinbutton', 'search', 'searchbox', 'row', 'cell', 'gridcell'}
                if node.attributes['role'] in interactive_roles:
                    return True
        if node.ax_node and node.ax_node.role:
            interactive_ax_roles = {'button', 'link', 'menuitem', 'option', 'radio', 'checkbox', 'tab', 'textbox', 'combobox', 'slider', 'spinbutton', 'listbox', 'search', 'searchbox', 'row', 'cell', 'gridcell'}
            if node.ax_node.role in interactive_ax_roles:
                return True
        if node.snapshot_node and node.snapshot_node.bounds and (10 <= node.snapshot_node.bounds.width <= 50) and (10 <= node.snapshot_node.bounds.height <= 50):
            if node.attributes:
                icon_attributes = {'class', 'role', 'onclick', 'data-action', 'aria-label'}
                if any((attr in node.attributes for attr in icon_attributes)):
                    return True
        if node.snapshot_node and node.snapshot_node.cursor_style and (node.snapshot_node.cursor_style == 'pointer'):
            return True
        return False