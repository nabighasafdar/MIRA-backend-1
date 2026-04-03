from system.dom.service import EnhancedDOMTreeNode

def get_click_description(node: EnhancedDOMTreeNode) -> str:
    parts = []
    parts.append(node.tag_name)
    if node.tag_name == 'input' and node.attributes.get('type'):
        input_type = node.attributes['type']
        parts.append(f'type={input_type}')
        if input_type == 'checkbox':
            is_checked = node.attributes.get('checked', 'false').lower() in ['true', 'checked', '']
            if node.ax_node and node.ax_node.properties:
                for prop in node.ax_node.properties:
                    if prop.name == 'checked':
                        is_checked = prop.value is True or prop.value == 'true'
                        break
            state = 'checked' if is_checked else 'unchecked'
            parts.append(f'checkbox-state={state}')
    if node.attributes.get('role'):
        role = node.attributes['role']
        parts.append(f'role={role}')
        if role == 'checkbox':
            aria_checked = node.attributes.get('aria-checked', 'false').lower()
            is_checked = aria_checked in ['true', 'checked']
            if node.ax_node and node.ax_node.properties:
                for prop in node.ax_node.properties:
                    if prop.name == 'checked':
                        is_checked = prop.value is True or prop.value == 'true'
                        break
            state = 'checked' if is_checked else 'unchecked'
            parts.append(f'checkbox-state={state}')
    if node.tag_name in ['label', 'span', 'div'] and 'type=' not in ' '.join(parts):
        for child in node.children:
            if child.tag_name == 'input' and child.attributes.get('type') == 'checkbox':
                is_hidden = False
                if child.snapshot_node and child.snapshot_node.computed_styles:
                    opacity = child.snapshot_node.computed_styles.get('opacity', '1')
                    if opacity == '0' or opacity == '0.0':
                        is_hidden = True
                if is_hidden or not child.is_visible:
                    is_checked = child.attributes.get('checked', 'false').lower() in ['true', 'checked', '']
                    if child.ax_node and child.ax_node.properties:
                        for prop in child.ax_node.properties:
                            if prop.name == 'checked':
                                is_checked = prop.value is True or prop.value == 'true'
                                break
                    state = 'checked' if is_checked else 'unchecked'
                    parts.append(f'checkbox-state={state}')
                    break
    text = node.get_all_children_text().strip()
    if text:
        short_text = text[:30] + ('...' if len(text) > 30 else '')
        parts.append(f'"{short_text}"')
    for attr in ['id', 'name', 'aria-label']:
        if node.attributes.get(attr):
            parts.append(f'{attr}={node.attributes[attr][:20]}')
    return ' '.join(parts)