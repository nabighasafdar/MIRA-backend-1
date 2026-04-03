def cap_text_length(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + '...'

def generate_css_selector_for_element(enhanced_node) -> str | None:
    import re
    if not enhanced_node or not hasattr(enhanced_node, 'tag_name') or (not enhanced_node.tag_name):
        return None
    tag_name = enhanced_node.tag_name.lower().strip()
    if not tag_name or not re.match('^[a-zA-Z][a-zA-Z0-9-]*$', tag_name):
        return None
    css_selector = tag_name
    if enhanced_node.attributes and 'id' in enhanced_node.attributes:
        element_id = enhanced_node.attributes['id']
        if element_id and element_id.strip():
            element_id = element_id.strip()
            if re.match('^[a-zA-Z][a-zA-Z0-9_-]*$', element_id):
                return f'#{element_id}'
            else:
                escaped_id = element_id.replace('"', '\\"')
                return f'{tag_name}[id="{escaped_id}"]'
    if enhanced_node.attributes and 'class' in enhanced_node.attributes and enhanced_node.attributes['class']:
        valid_class_name_pattern = re.compile('^[a-zA-Z_][a-zA-Z0-9_-]*$')
        classes = enhanced_node.attributes['class'].split()
        for class_name in classes:
            if not class_name.strip():
                continue
            if valid_class_name_pattern.match(class_name):
                css_selector += f'.{class_name}'
    SAFE_ATTRIBUTES = {'id', 'name', 'type', 'placeholder', 'aria-label', 'aria-labelledby', 'aria-describedby', 'role', 'for', 'autocomplete', 'required', 'readonly', 'alt', 'title', 'src', 'href', 'target'}
    include_dynamic_attributes = True
    if include_dynamic_attributes:
        dynamic_attributes = {'data-id', 'data-qa', 'data-cy', 'data-testid'}
        SAFE_ATTRIBUTES.update(dynamic_attributes)
    if enhanced_node.attributes:
        for attribute, value in enhanced_node.attributes.items():
            if attribute == 'class':
                continue
            if not attribute.strip():
                continue
            if attribute not in SAFE_ATTRIBUTES:
                continue
            safe_attribute = attribute.replace(':', '\\:')
            if value == '':
                css_selector += f'[{safe_attribute}]'
            elif any((char in value for char in '"\'<>`\n\r\t')):
                if '\n' in value:
                    value = value.split('\n')[0]
                collapsed_value = re.sub('\\s+', ' ', value).strip()
                safe_value = collapsed_value.replace('"', '\\"')
                css_selector += f'[{safe_attribute}*="{safe_value}"]'
            else:
                css_selector += f'[{safe_attribute}="{value}"]'
    if css_selector and (not any((char in css_selector for char in ['\n', '\r', '\t']))):
        return css_selector
    return tag_name