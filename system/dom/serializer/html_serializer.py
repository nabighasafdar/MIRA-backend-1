from system.dom.views import EnhancedDOMTreeNode, NodeType

class HTMLSerializer:

    def __init__(self, extract_links: bool=False):
        self.extract_links = extract_links

    def serialize(self, node: EnhancedDOMTreeNode, depth: int=0) -> str:
        if node.node_type == NodeType.DOCUMENT_NODE:
            parts = []
            for child in node.children_and_shadow_roots:
                child_html = self.serialize(child, depth)
                if child_html:
                    parts.append(child_html)
            return ''.join(parts)
        elif node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
            parts = []
            shadow_type = node.shadow_root_type or 'open'
            parts.append(f'<template shadowroot="{shadow_type.lower()}">')
            for child in node.children:
                child_html = self.serialize(child, depth + 1)
                if child_html:
                    parts.append(child_html)
            parts.append('</template>')
            return ''.join(parts)
        elif node.node_type == NodeType.ELEMENT_NODE:
            parts = []
            tag_name = node.tag_name.lower()
            if tag_name in {'style', 'script', 'head', 'meta', 'link', 'title'}:
                return ''
            if tag_name == 'code' and node.attributes:
                style = node.attributes.get('style', '')
                if 'display:none' in style.replace(' ', '') or 'display: none' in style:
                    return ''
                element_id = node.attributes.get('id', '')
                if 'bpr-guid' in element_id or 'data' in element_id or 'state' in element_id:
                    return ''
            if tag_name == 'img' and node.attributes:
                src = node.attributes.get('src', '')
                if src.startswith('data:image/'):
                    return ''
            parts.append(f'<{tag_name}')
            if node.attributes:
                attrs = self._serialize_attributes(node.attributes)
                if attrs:
                    parts.append(' ' + attrs)
            void_elements = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
            if tag_name in void_elements:
                parts.append(' />')
                return ''.join(parts)
            parts.append('>')
            if tag_name == 'table':
                if node.shadow_roots:
                    for shadow_root in node.shadow_roots:
                        child_html = self.serialize(shadow_root, depth + 1)
                        if child_html:
                            parts.append(child_html)
                table_html = self._serialize_table_children(node, depth)
                parts.append(table_html)
            elif tag_name in {'iframe', 'frame'} and node.content_document:
                for child in node.content_document.children_nodes or []:
                    child_html = self.serialize(child, depth + 1)
                    if child_html:
                        parts.append(child_html)
            else:
                if node.shadow_roots:
                    for shadow_root in node.shadow_roots:
                        child_html = self.serialize(shadow_root, depth + 1)
                        if child_html:
                            parts.append(child_html)
                for child in node.children:
                    child_html = self.serialize(child, depth + 1)
                    if child_html:
                        parts.append(child_html)
            parts.append(f'</{tag_name}>')
            return ''.join(parts)
        elif node.node_type == NodeType.TEXT_NODE:
            if node.node_value:
                return self._escape_html(node.node_value)
            return ''
        elif node.node_type == NodeType.COMMENT_NODE:
            return ''
        else:
            return ''

    def _serialize_table_children(self, table_node: EnhancedDOMTreeNode, depth: int) -> str:
        children = table_node.children
        if not children:
            return ''
        child_tags = [c.tag_name for c in children if c.node_type == NodeType.ELEMENT_NODE]
        has_thead = 'thead' in child_tags
        has_tbody = 'tbody' in child_tags
        if has_thead or not child_tags:
            parts = []
            for child in children:
                child_html = self.serialize(child, depth + 1)
                if child_html:
                    parts.append(child_html)
            return ''.join(parts)
        first_tr = None
        first_tr_idx = -1
        for i, child in enumerate(children):
            if child.node_type == NodeType.ELEMENT_NODE and child.tag_name == 'tr':
                has_th = any((c.node_type == NodeType.ELEMENT_NODE and c.tag_name == 'th' for c in child.children))
                if has_th:
                    first_tr = child
                    first_tr_idx = i
                break
        if first_tr is None:
            parts = []
            for child in children:
                child_html = self.serialize(child, depth + 1)
                if child_html:
                    parts.append(child_html)
            return ''.join(parts)
        parts = []
        for child in children[:first_tr_idx]:
            child_html = self.serialize(child, depth + 1)
            if child_html:
                parts.append(child_html)
        parts.append('<thead>')
        parts.append(self.serialize(first_tr, depth + 2))
        parts.append('</thead>')
        remaining = children[first_tr_idx + 1:]
        if remaining and (not has_tbody):
            parts.append('<tbody>')
            for child in remaining:
                child_html = self.serialize(child, depth + 2)
                if child_html:
                    parts.append(child_html)
            parts.append('</tbody>')
        else:
            for child in remaining:
                child_html = self.serialize(child, depth + 1)
                if child_html:
                    parts.append(child_html)
        return ''.join(parts)

    def _serialize_attributes(self, attributes: dict[str, str]) -> str:
        parts = []
        for key, value in attributes.items():
            if not self.extract_links and key == 'href':
                continue
            if key.startswith('data-'):
                continue
            if value == '' or value is None:
                parts.append(key)
            else:
                escaped_value = self._escape_attribute(value)
                parts.append(f'{key}="{escaped_value}"')
        return ' '.join(parts)

    def _escape_html(self, text: str) -> str:
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    def _escape_attribute(self, value: str) -> str:
        return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#x27;')