from system.dom.utils import cap_text_length
from system.dom.views import EnhancedDOMTreeNode, NodeType, SimplifiedNode
EVAL_KEY_ATTRIBUTES = ['id', 'class', 'name', 'type', 'placeholder', 'aria-label', 'role', 'value', 'data-testid', 'alt', 'title', 'checked', 'selected', 'disabled', 'required', 'readonly', 'aria-expanded', 'aria-pressed', 'aria-checked', 'aria-selected', 'aria-invalid', 'pattern', 'min', 'max', 'minlength', 'maxlength', 'step', 'aria-valuemin', 'aria-valuemax', 'aria-valuenow']
SEMANTIC_ELEMENTS = {'html', 'body', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'button', 'input', 'textarea', 'select', 'form', 'label', 'nav', 'header', 'footer', 'main', 'article', 'section', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'ul', 'ol', 'li', 'img', 'iframe', 'video', 'audio'}
COLLAPSIBLE_CONTAINERS = {'div', 'span', 'section', 'article'}
SVG_ELEMENTS = {'path', 'rect', 'g', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'use', 'defs', 'clipPath', 'mask', 'pattern', 'image', 'text', 'tspan'}

class DOMEvalSerializer:

    @staticmethod
    def serialize_tree(node: SimplifiedNode | None, include_attributes: list[str], depth: int=0) -> str:
        if not node:
            return ''
        if hasattr(node, 'excluded_by_parent') and node.excluded_by_parent:
            return DOMEvalSerializer._serialize_children(node, include_attributes, depth)
        if not node.should_display:
            return DOMEvalSerializer._serialize_children(node, include_attributes, depth)
        formatted_text = []
        depth_str = depth * '\t'
        if node.original_node.node_type == NodeType.ELEMENT_NODE:
            tag = node.original_node.tag_name.lower()
            is_visible = node.original_node.snapshot_node and node.original_node.is_visible
            container_tags = {'html', 'body', 'div', 'main', 'section', 'article', 'aside', 'header', 'footer', 'nav'}
            if not is_visible and tag not in container_tags and (tag not in ['iframe', 'frame']):
                return DOMEvalSerializer._serialize_children(node, include_attributes, depth)
            if tag in ['iframe', 'frame']:
                return DOMEvalSerializer._serialize_iframe(node, include_attributes, depth)
            if tag == 'svg':
                line = f'{depth_str}'
                if node.is_interactive:
                    line += f'[i_{node.original_node.backend_node_id}] '
                line += '<svg'
                attributes_str = DOMEvalSerializer._build_compact_attributes(node.original_node)
                if attributes_str:
                    line += f' {attributes_str}'
                line += ' /> <!-- SVG content collapsed -->'
                return line
            if tag in SVG_ELEMENTS:
                return ''
            attributes_str = DOMEvalSerializer._build_compact_attributes(node.original_node)
            is_semantic = tag in SEMANTIC_ELEMENTS
            has_useful_attrs = bool(attributes_str)
            has_text_content = DOMEvalSerializer._has_direct_text(node)
            has_children = len(node.children) > 0
            line = f'{depth_str}'
            if node.is_interactive:
                line += f'[i_{node.original_node.backend_node_id}] '
            line += f'<{tag}'
            if attributes_str:
                line += f' {attributes_str}'
            if node.original_node.should_show_scroll_info:
                scroll_text = node.original_node.get_scroll_info_text()
                if scroll_text:
                    line += f' scroll="{scroll_text}"'
            inline_text = DOMEvalSerializer._get_inline_text(node)
            is_container = tag in container_tags
            if inline_text and (not is_container):
                line += f'>{inline_text}'
            else:
                line += ' />'
            formatted_text.append(line)
            if has_children and (is_container or not inline_text):
                children_text = DOMEvalSerializer._serialize_children(node, include_attributes, depth + 1)
                if children_text:
                    formatted_text.append(children_text)
        elif node.original_node.node_type == NodeType.TEXT_NODE:
            pass
        elif node.original_node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
            if node.children:
                formatted_text.append(f'{depth_str}#shadow')
                children_text = DOMEvalSerializer._serialize_children(node, include_attributes, depth + 1)
                if children_text:
                    formatted_text.append(children_text)
        return '\n'.join(formatted_text)

    @staticmethod
    def _serialize_children(node: SimplifiedNode, include_attributes: list[str], depth: int) -> str:
        children_output = []
        is_list_container = node.original_node.node_type == NodeType.ELEMENT_NODE and node.original_node.tag_name.lower() in ['ul', 'ol']
        li_count = 0
        max_list_items = 50
        consecutive_link_count = 0
        max_consecutive_links = 50
        total_links_skipped = 0
        for child in node.children:
            current_tag = None
            if child.original_node.node_type == NodeType.ELEMENT_NODE:
                current_tag = child.original_node.tag_name.lower()
            if is_list_container and current_tag == 'li':
                li_count += 1
                if li_count > max_list_items:
                    continue
            if current_tag == 'a':
                consecutive_link_count += 1
                if consecutive_link_count > max_consecutive_links:
                    total_links_skipped += 1
                    continue
            else:
                if total_links_skipped > 0:
                    depth_str = depth * '\t'
                    children_output.append(f'{depth_str}... ({total_links_skipped} more links in this list)')
                    total_links_skipped = 0
                consecutive_link_count = 0
            child_text = DOMEvalSerializer.serialize_tree(child, include_attributes, depth)
            if child_text:
                children_output.append(child_text)
        if is_list_container and li_count > max_list_items:
            depth_str = depth * '\t'
            children_output.append(f'{depth_str}... ({li_count - max_list_items} more items in this list (truncated) use evaluate to get more.')
        if total_links_skipped > 0:
            depth_str = depth * '\t'
            children_output.append(f'{depth_str}... ({total_links_skipped} more links in this list) (truncated) use evaluate to get more.')
        return '\n'.join(children_output)

    @staticmethod
    def _build_compact_attributes(node: EnhancedDOMTreeNode) -> str:
        attrs = []
        if node.attributes:
            for attr in EVAL_KEY_ATTRIBUTES:
                if attr in node.attributes:
                    value = str(node.attributes[attr]).strip()
                    if not value:
                        continue
                    if attr == 'class':
                        classes = value.split()[:3]
                        value = ' '.join(classes)
                    elif attr == 'href':
                        value = cap_text_length(value, 80)
                    else:
                        value = cap_text_length(value, 80)
                    attrs.append(f'{attr}="{value}"')
        return ' '.join(attrs)

    @staticmethod
    def _has_direct_text(node: SimplifiedNode) -> bool:
        for child in node.children:
            if child.original_node.node_type == NodeType.TEXT_NODE:
                text = child.original_node.node_value.strip() if child.original_node.node_value else ''
                if len(text) > 1:
                    return True
        return False

    @staticmethod
    def _get_inline_text(node: SimplifiedNode) -> str:
        text_parts = []
        for child in node.children:
            if child.original_node.node_type == NodeType.TEXT_NODE:
                text = child.original_node.node_value.strip() if child.original_node.node_value else ''
                if text and len(text) > 1:
                    text_parts.append(text)
        if not text_parts:
            return ''
        combined = ' '.join(text_parts)
        return cap_text_length(combined, 80)

    @staticmethod
    def _serialize_iframe(node: SimplifiedNode, include_attributes: list[str], depth: int) -> str:
        formatted_text = []
        depth_str = depth * '\t'
        tag = node.original_node.tag_name.lower()
        attributes_str = DOMEvalSerializer._build_compact_attributes(node.original_node)
        line = f'{depth_str}<{tag}'
        if attributes_str:
            line += f' {attributes_str}'
        if node.original_node.should_show_scroll_info:
            scroll_text = node.original_node.get_scroll_info_text()
            if scroll_text:
                line += f' scroll="{scroll_text}"'
        line += ' />'
        formatted_text.append(line)
        if node.original_node.content_document:
            formatted_text.append(f'{depth_str}\t#iframe-content')
            for child_node in node.original_node.content_document.children_nodes or []:
                if child_node.tag_name.lower() == 'html':
                    for html_child in child_node.children:
                        if html_child.tag_name.lower() == 'body':
                            for body_child in html_child.children:
                                DOMEvalSerializer._serialize_document_node(body_child, formatted_text, include_attributes, depth + 2, is_iframe_content=True)
                            break
                else:
                    DOMEvalSerializer._serialize_document_node(child_node, formatted_text, include_attributes, depth + 1, is_iframe_content=True)
        return '\n'.join(formatted_text)

    @staticmethod
    def _serialize_document_node(dom_node: EnhancedDOMTreeNode, output: list[str], include_attributes: list[str], depth: int, is_iframe_content: bool=True) -> None:
        depth_str = depth * '\t'
        if dom_node.node_type == NodeType.ELEMENT_NODE:
            tag = dom_node.tag_name.lower()
            if is_iframe_content:
                is_visible = not dom_node.snapshot_node or dom_node.is_visible
            else:
                is_visible = dom_node.snapshot_node and dom_node.is_visible
            if not is_visible:
                return
            is_semantic = tag in SEMANTIC_ELEMENTS
            attributes_str = DOMEvalSerializer._build_compact_attributes(dom_node)
            if not is_semantic and (not attributes_str):
                for child in dom_node.children:
                    DOMEvalSerializer._serialize_document_node(child, output, include_attributes, depth, is_iframe_content=is_iframe_content)
                return
            line = f'{depth_str}<{tag}'
            if attributes_str:
                line += f' {attributes_str}'
            text_parts = []
            for child in dom_node.children:
                if child.node_type == NodeType.TEXT_NODE and child.node_value:
                    text = child.node_value.strip()
                    if text and len(text) > 1:
                        text_parts.append(text)
            if text_parts:
                combined = ' '.join(text_parts)
                line += f'>{cap_text_length(combined, 100)}'
            else:
                line += ' />'
            output.append(line)
            for child in dom_node.children:
                if child.node_type != NodeType.TEXT_NODE:
                    DOMEvalSerializer._serialize_document_node(child, output, include_attributes, depth + 1, is_iframe_content=is_iframe_content)