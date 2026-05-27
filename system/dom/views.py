import hashlib
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from cdp_use.cdp.accessibility.commands import GetFullAXTreeReturns
from cdp_use.cdp.accessibility.types import AXPropertyName
from cdp_use.cdp.dom.commands import GetDocumentReturns
from cdp_use.cdp.dom.types import ShadowRootType
from cdp_use.cdp.domsnapshot.commands import CaptureSnapshotReturns
from cdp_use.cdp.target.types import SessionID, TargetID, TargetInfo
from uuid_extensions import uuid7str
from system.dom.utils import cap_text_length
from system.observability import observe_debug
DEFAULT_INCLUDE_ATTRIBUTES = ['title', 'type', 'checked', 'id', 'name', 'role', 'value', 'placeholder', 'data-date-format', 'alt', 'aria-label', 'aria-expanded', 'data-state', 'aria-checked', 'aria-valuemin', 'aria-valuemax', 'aria-valuenow', 'aria-placeholder', 'pattern', 'min', 'max', 'minlength', 'maxlength', 'step', 'accept', 'multiple', 'inputmode', 'autocomplete', 'aria-autocomplete', 'list', 'data-mask', 'data-inputmask', 'data-datepicker', 'format', 'expected_format', 'contenteditable', 'pseudo', 'checked', 'selected', 'expanded', 'pressed', 'disabled', 'invalid', 'valuemin', 'valuemax', 'valuenow', 'keyshortcuts', 'haspopup', 'multiselectable', 'required', 'valuetext', 'level', 'busy', 'live', 'ax_name']
STATIC_ATTRIBUTES = {'class', 'id', 'name', 'type', 'placeholder', 'aria-label', 'title', 'role', 'data-testid', 'data-test', 'data-cy', 'data-selenium', 'for', 'required', 'disabled', 'readonly', 'checked', 'selected', 'multiple', 'accept', 'href', 'target', 'rel', 'aria-describedby', 'aria-labelledby', 'aria-controls', 'aria-owns', 'aria-live', 'aria-atomic', 'aria-busy', 'aria-disabled', 'aria-hidden', 'aria-pressed', 'aria-autocomplete', 'aria-checked', 'aria-selected', 'list', 'tabindex', 'alt', 'src', 'lang', 'itemscope', 'itemtype', 'itemprop', 'pseudo', 'aria-valuemin', 'aria-valuemax', 'aria-valuenow', 'aria-placeholder'}
DYNAMIC_CLASS_PATTERNS = frozenset({'focus', 'hover', 'active', 'selected', 'disabled', 'animation', 'transition', 'loading', 'open', 'closed', 'expanded', 'collapsed', 'visible', 'hidden', 'pressed', 'checked', 'highlighted', 'current', 'entering', 'leaving'})

class MatchLevel(Enum):
    EXACT = 1
    STABLE = 2
    XPATH = 3
    AX_NAME = 4
    ATTRIBUTE = 5

def filter_dynamic_classes(class_str: str | None) -> str:
    if not class_str:
        return ''
    classes = class_str.split()
    stable = [c for c in classes if not any((pattern in c.lower() for pattern in DYNAMIC_CLASS_PATTERNS))]
    return ' '.join(sorted(stable))

@dataclass
class CurrentPageTargets:
    page_session: TargetInfo
    iframe_sessions: list[TargetInfo]
    '\n\tIframe sessions are ALL the iframes sessions of all the pages (not just the current page)\n\t'

@dataclass
class TargetAllTrees:
    snapshot: CaptureSnapshotReturns
    dom_tree: GetDocumentReturns
    ax_tree: GetFullAXTreeReturns
    device_pixel_ratio: float
    cdp_timing: dict[str, float]
    js_click_listener_backend_ids: set[int] | None = None
    'Backend node IDs of elements with JS click/mouse event listeners (detected via CDP getEventListeners).'

@dataclass(slots=True)
class PropagatingBounds:
    tag: str
    bounds: 'DOMRect'
    node_id: int
    depth: int

@dataclass(slots=True)
class SimplifiedNode:
    original_node: 'EnhancedDOMTreeNode'
    children: list['SimplifiedNode']
    should_display: bool = True
    is_interactive: bool = False
    is_new: bool = False
    ignored_by_paint_order: bool = False
    excluded_by_parent: bool = False
    is_shadow_host: bool = False
    is_compound_component: bool = False

    def _clean_original_node_json(self, node_json: dict) -> dict:
        if 'children_nodes' in node_json:
            del node_json['children_nodes']
        if 'shadow_roots' in node_json:
            del node_json['shadow_roots']
        if node_json.get('content_document'):
            node_json['content_document'] = self._clean_original_node_json(node_json['content_document'])
        return node_json

    def __json__(self) -> dict:
        original_node_json = self.original_node.__json__()
        cleaned_original_node_json = self._clean_original_node_json(original_node_json)
        return {'should_display': self.should_display, 'is_interactive': self.is_interactive, 'ignored_by_paint_order': self.ignored_by_paint_order, 'excluded_by_parent': self.excluded_by_parent, 'original_node': cleaned_original_node_json, 'children': [c.__json__() for c in self.children]}

class NodeType(int, Enum):
    ELEMENT_NODE = 1
    ATTRIBUTE_NODE = 2
    TEXT_NODE = 3
    CDATA_SECTION_NODE = 4
    ENTITY_REFERENCE_NODE = 5
    ENTITY_NODE = 6
    PROCESSING_INSTRUCTION_NODE = 7
    COMMENT_NODE = 8
    DOCUMENT_NODE = 9
    DOCUMENT_TYPE_NODE = 10
    DOCUMENT_FRAGMENT_NODE = 11
    NOTATION_NODE = 12

@dataclass(slots=True)
class DOMRect:
    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict[str, Any]:
        return {'x': self.x, 'y': self.y, 'width': self.width, 'height': self.height}

    def __json__(self) -> dict:
        return self.to_dict()

@dataclass(slots=True)
class EnhancedAXProperty:
    name: AXPropertyName
    value: str | bool | None

@dataclass(slots=True)
class EnhancedAXNode:
    ax_node_id: str
    'Not to be confused the DOM node_id. Only useful for AX node tree'
    ignored: bool
    role: str | None
    name: str | None
    description: str | None
    properties: list[EnhancedAXProperty] | None
    child_ids: list[str] | None

@dataclass(slots=True)
class EnhancedSnapshotNode:
    is_clickable: bool | None
    cursor_style: str | None
    bounds: DOMRect | None
    "\n\tDocument coordinates (origin = top-left of the page, ignores current scroll).\n\tEquivalent JS API: layoutNode.boundingBox in the older API.\n\tTypical use: Quick hit-test that doesn't care about scroll position.\n\t"
    clientRects: DOMRect | None
    '\n\tViewport coordinates (origin = top-left of the visible scrollport).\n\tEquivalent JS API: element.getClientRects() / getBoundingClientRect().\n\tTypical use: Pixel-perfect hit-testing on screen, taking current scroll into account.\n\t'
    scrollRects: DOMRect | None
    '\n\tScrollable area of the element.\n\t'
    computed_styles: dict[str, str] | None
    'Computed styles from the layout tree'
    paint_order: int | None
    'Paint order from the layout tree'
    stacking_contexts: int | None
    'Stacking contexts from the layout tree'

@dataclass(slots=True)
class EnhancedDOMTreeNode:
    node_id: int
    backend_node_id: int
    node_type: NodeType
    'Node types, defined in `NodeType` enum.'
    node_name: str
    'Only applicable for `NodeType.ELEMENT_NODE`'
    node_value: str
    'this is where the value from `NodeType.TEXT_NODE` is stored usually'
    attributes: dict[str, str]
    'slightly changed from the original attributes to be more readable'
    is_scrollable: bool | None
    '\n\tWhether the node is scrollable.\n\t'
    is_visible: bool | None
    '\n\tWhether the node is visible according to the upper most frame node.\n\t'
    absolute_position: DOMRect | None
    '\n\tAbsolute position of the node in the document according to the top-left of the page.\n\t'
    target_id: TargetID
    frame_id: str | None
    session_id: SessionID | None
    content_document: 'EnhancedDOMTreeNode | None'
    '\n\tContent document is the document inside a new iframe.\n\t'
    shadow_root_type: ShadowRootType | None
    shadow_roots: list['EnhancedDOMTreeNode'] | None
    '\n\tShadow roots are the shadow DOMs of the element.\n\t'
    parent_node: 'EnhancedDOMTreeNode | None'
    children_nodes: list['EnhancedDOMTreeNode'] | None
    ax_node: EnhancedAXNode | None
    snapshot_node: EnhancedSnapshotNode | None
    _compound_children: list[dict[str, Any]] = field(default_factory=list)
    has_js_click_listener: bool = False
    "\n\tWhether this element has JS click/mouse event listeners attached (detected via CDP getEventListeners)\n\tUsed to identify clicks that don't use native interactive HTML tags\n\t"
    hidden_elements_info: list[dict[str, Any]] = field(default_factory=list)
    '\n\tDetails of interactive elements hidden due to viewport threshold (for iframes).\n\tEach dict contains: tag, text, pages (scroll distance in viewport pages).\n\tUsed to show specific element info in the LLM representation.\n\t'
    has_hidden_content: bool = False
    '\n\tWhether this iframe has hidden non-interactive content below the viewport threshold.\n\t'
    uuid: str = field(default_factory=uuid7str)

    @property
    def parent(self) -> 'EnhancedDOMTreeNode | None':
        return self.parent_node

    @property
    def children(self) -> list['EnhancedDOMTreeNode']:
        return self.children_nodes or []

    @property
    def children_and_shadow_roots(self) -> list['EnhancedDOMTreeNode']:
        children = list(self.children_nodes) if self.children_nodes else []
        if self.shadow_roots:
            children.extend(self.shadow_roots)
        return children

    @property
    def tag_name(self) -> str:
        return self.node_name.lower()

    @property
    def xpath(self) -> str:
        segments = []
        current_element = self
        while current_element and (current_element.node_type == NodeType.ELEMENT_NODE or current_element.node_type == NodeType.DOCUMENT_FRAGMENT_NODE):
            if current_element.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
                current_element = current_element.parent_node
                continue
            if current_element.parent_node and current_element.parent_node.node_name.lower() == 'iframe':
                break
            position = self._get_element_position(current_element)
            tag_name = current_element.node_name.lower()
            xpath_index = f'[{position}]' if position > 0 else ''
            segments.insert(0, f'{tag_name}{xpath_index}')
            current_element = current_element.parent_node
        return '/'.join(segments)

    def _get_element_position(self, element: 'EnhancedDOMTreeNode') -> int:
        if not element.parent_node or not element.parent_node.children_nodes:
            return 0
        same_tag_siblings = [child for child in element.parent_node.children_nodes if child.node_type == NodeType.ELEMENT_NODE and child.node_name.lower() == element.node_name.lower()]
        if len(same_tag_siblings) <= 1:
            return 0
        try:
            position = same_tag_siblings.index(element) + 1
            return position
        except ValueError:
            return 0

    def __json__(self) -> dict:
        return {'node_id': self.node_id, 'backend_node_id': self.backend_node_id, 'node_type': self.node_type.name, 'node_name': self.node_name, 'node_value': self.node_value, 'is_visible': self.is_visible, 'attributes': self.attributes, 'is_scrollable': self.is_scrollable, 'session_id': self.session_id, 'target_id': self.target_id, 'frame_id': self.frame_id, 'content_document': self.content_document.__json__() if self.content_document else None, 'shadow_root_type': self.shadow_root_type, 'ax_node': asdict(self.ax_node) if self.ax_node else None, 'snapshot_node': asdict(self.snapshot_node) if self.snapshot_node else None, 'shadow_roots': [r.__json__() for r in self.shadow_roots] if self.shadow_roots else [], 'children_nodes': [c.__json__() for c in self.children_nodes] if self.children_nodes else []}

    def get_all_children_text(self, max_depth: int=-1) -> str:
        text_parts = []

        def collect_text(node: EnhancedDOMTreeNode, current_depth: int) -> None:
            if max_depth != -1 and current_depth > max_depth:
                return
            if node.node_type == NodeType.TEXT_NODE:
                text_parts.append(node.node_value)
            elif node.node_type == NodeType.ELEMENT_NODE:
                for child in node.children:
                    collect_text(child, current_depth + 1)
        collect_text(self, 0)
        return '\n'.join(text_parts).strip()

    def __repr__(self) -> str:
        attributes = ', '.join([f'{k}={v}' for k, v in self.attributes.items()])
        is_scrollable = getattr(self, 'is_scrollable', False)
        num_children = len(self.children_nodes or [])
        return f'<{self.tag_name} {attributes} is_scrollable={is_scrollable} num_children={num_children} >{self.node_value}</{self.tag_name}>'

    def llm_representation(self, max_text_length: int=100) -> str:
        return f"<{self.tag_name}>{cap_text_length(self.get_all_children_text(), max_text_length) or ''}"

    def get_meaningful_text_for_llm(self) -> str:
        meaningful_text = ''
        if hasattr(self, 'attributes') and self.attributes:
            for attr in ['value', 'aria-label', 'title', 'placeholder', 'alt']:
                if attr in self.attributes and self.attributes[attr]:
                    meaningful_text = self.attributes[attr]
                    break
        if not meaningful_text:
            meaningful_text = self.get_all_children_text()
        return meaningful_text.strip()

    @property
    def is_actually_scrollable(self) -> bool:
        if self.is_scrollable:
            return True
        if not self.snapshot_node:
            return False
        scroll_rects = self.snapshot_node.scrollRects
        client_rects = self.snapshot_node.clientRects
        if scroll_rects and client_rects:
            has_vertical_scroll = scroll_rects.height > client_rects.height + 1
            has_horizontal_scroll = scroll_rects.width > client_rects.width + 1
            if has_vertical_scroll or has_horizontal_scroll:
                if self.snapshot_node.computed_styles:
                    styles = self.snapshot_node.computed_styles
                    overflow = styles.get('overflow', 'visible').lower()
                    overflow_x = styles.get('overflow-x', overflow).lower()
                    overflow_y = styles.get('overflow-y', overflow).lower()
                    allows_scroll = overflow in ['auto', 'scroll', 'overlay'] or overflow_x in ['auto', 'scroll', 'overlay'] or overflow_y in ['auto', 'scroll', 'overlay']
                    return allows_scroll
                else:
                    scrollable_tags = {'div', 'main', 'section', 'article', 'aside', 'body', 'html'}
                    return self.tag_name.lower() in scrollable_tags
        return False

    @property
    def should_show_scroll_info(self) -> bool:
        if self.tag_name.lower() == 'iframe':
            return True
        if not (self.is_scrollable or self.is_actually_scrollable):
            return False
        if self.tag_name.lower() in {'body', 'html'}:
            return True
        if self.parent_node and (self.parent_node.is_scrollable or self.parent_node.is_actually_scrollable):
            return False
        return True

    def _find_html_in_content_document(self) -> 'EnhancedDOMTreeNode | None':
        if not self.content_document:
            return None
        if self.content_document.tag_name.lower() == 'html':
            return self.content_document
        if self.content_document.children_nodes:
            for child in self.content_document.children_nodes:
                if child.tag_name.lower() == 'html':
                    return child
        return None

    @property
    def scroll_info(self) -> dict[str, Any] | None:
        if not self.is_actually_scrollable or not self.snapshot_node:
            return None
        scroll_rects = self.snapshot_node.scrollRects
        client_rects = self.snapshot_node.clientRects
        bounds = self.snapshot_node.bounds
        if not scroll_rects or not client_rects:
            return None
        scroll_top = scroll_rects.y
        scroll_left = scroll_rects.x
        scrollable_height = scroll_rects.height
        scrollable_width = scroll_rects.width
        visible_height = client_rects.height
        visible_width = client_rects.width
        content_above = max(0, scroll_top)
        content_below = max(0, scrollable_height - visible_height - scroll_top)
        content_left = max(0, scroll_left)
        content_right = max(0, scrollable_width - visible_width - scroll_left)
        vertical_scroll_percentage = 0
        horizontal_scroll_percentage = 0
        if scrollable_height > visible_height:
            max_scroll_top = scrollable_height - visible_height
            vertical_scroll_percentage = scroll_top / max_scroll_top * 100 if max_scroll_top > 0 else 0
        if scrollable_width > visible_width:
            max_scroll_left = scrollable_width - visible_width
            horizontal_scroll_percentage = scroll_left / max_scroll_left * 100 if max_scroll_left > 0 else 0
        pages_above = content_above / visible_height if visible_height > 0 else 0
        pages_below = content_below / visible_height if visible_height > 0 else 0
        total_pages = scrollable_height / visible_height if visible_height > 0 else 1
        return {'scroll_top': scroll_top, 'scroll_left': scroll_left, 'scrollable_height': scrollable_height, 'scrollable_width': scrollable_width, 'visible_height': visible_height, 'visible_width': visible_width, 'content_above': content_above, 'content_below': content_below, 'content_left': content_left, 'content_right': content_right, 'vertical_scroll_percentage': round(vertical_scroll_percentage, 1), 'horizontal_scroll_percentage': round(horizontal_scroll_percentage, 1), 'pages_above': round(pages_above, 1), 'pages_below': round(pages_below, 1), 'total_pages': round(total_pages, 1), 'can_scroll_up': content_above > 0, 'can_scroll_down': content_below > 0, 'can_scroll_left': content_left > 0, 'can_scroll_right': content_right > 0}

    def get_scroll_info_text(self) -> str:
        if self.tag_name.lower() == 'iframe':
            if self.content_document:
                html_element = self._find_html_in_content_document()
                if html_element and html_element.scroll_info:
                    info = html_element.scroll_info
                    pages_below = info.get('pages_below', 0)
                    pages_above = info.get('pages_above', 0)
                    v_pct = int(info.get('vertical_scroll_percentage', 0))
                    if pages_below > 0 or pages_above > 0:
                        return f'scroll: {pages_above:.1f}↑ {pages_below:.1f}↓ {v_pct}%'
            return 'scroll'
        scroll_info = self.scroll_info
        if not scroll_info:
            return ''
        parts = []
        if scroll_info['scrollable_height'] > scroll_info['visible_height']:
            parts.append(f"{scroll_info['pages_above']:.1f} pages above, {scroll_info['pages_below']:.1f} pages below")
        if scroll_info['scrollable_width'] > scroll_info['visible_width']:
            parts.append(f"horizontal {scroll_info['horizontal_scroll_percentage']:.0f}%")
        return ' '.join(parts)

    @property
    def element_hash(self) -> int:
        return hash(self)

    def compute_stable_hash(self) -> int:
        parent_branch_path = self._get_parent_branch_path()
        parent_branch_path_string = '/'.join(parent_branch_path)
        filtered_attrs: dict[str, str] = {}
        for k, v in self.attributes.items():
            if k not in STATIC_ATTRIBUTES:
                continue
            if k == 'class':
                v = filter_dynamic_classes(v)
                if not v:
                    continue
            filtered_attrs[k] = v
        attributes_string = ''.join((f'{k}={v}' for k, v in sorted(filtered_attrs.items())))
        ax_name = ''
        if self.ax_node and self.ax_node.name:
            ax_name = f'|ax_name={self.ax_node.name}'
        combined_string = f'{parent_branch_path_string}|{attributes_string}{ax_name}'
        hash_hex = hashlib.sha256(combined_string.encode()).hexdigest()
        return int(hash_hex[:16], 16)

    def __str__(self) -> str:
        return f"[<{self.tag_name}>#{(self.frame_id[-4:] if self.frame_id else '?')}:{self.backend_node_id}]"

    def __hash__(self) -> int:
        parent_branch_path = self._get_parent_branch_path()
        parent_branch_path_string = '/'.join(parent_branch_path)
        attributes_string = ''.join((f'{k}={v}' for k, v in sorted(((k, v) for k, v in self.attributes.items() if k in STATIC_ATTRIBUTES))))
        ax_name = ''
        if self.ax_node and self.ax_node.name:
            ax_name = f'|ax_name={self.ax_node.name}'
        combined_string = f'{parent_branch_path_string}|{attributes_string}{ax_name}'
        element_hash = hashlib.sha256(combined_string.encode()).hexdigest()
        return int(element_hash[:16], 16)

    def parent_branch_hash(self) -> int:
        parent_branch_path = self._get_parent_branch_path()
        parent_branch_path_string = '/'.join(parent_branch_path)
        element_hash = hashlib.sha256(parent_branch_path_string.encode()).hexdigest()
        return int(element_hash[:16], 16)

    def _get_parent_branch_path(self) -> list[str]:
        parents: list['EnhancedDOMTreeNode'] = []
        current_element: 'EnhancedDOMTreeNode | None' = self
        while current_element is not None:
            if current_element.node_type == NodeType.ELEMENT_NODE:
                parents.append(current_element)
            current_element = current_element.parent_node
        parents.reverse()
        return [parent.tag_name for parent in parents]
DOMSelectorMap = dict[int, EnhancedDOMTreeNode]

@dataclass(slots=True)
class MarkdownChunk:
    content: str
    chunk_index: int
    total_chunks: int
    char_offset_start: int
    char_offset_end: int
    overlap_prefix: str
    has_more: bool

@dataclass
class SerializedDOMState:
    _root: SimplifiedNode | None
    'Not meant to be used directly, use `llm_representation` instead'
    selector_map: DOMSelectorMap

    @observe_debug(ignore_input=True, ignore_output=True, name='llm_representation')
    def llm_representation(self, include_attributes: list[str] | None=None) -> str:
        from system.dom.serializer.serializer import DOMTreeSerializer
        if not self._root:
            return 'Empty DOM tree (you might have to wait for the page to load)'
        include_attributes = include_attributes or DEFAULT_INCLUDE_ATTRIBUTES
        return DOMTreeSerializer.serialize_tree(self._root, include_attributes)

    @observe_debug(ignore_input=True, ignore_output=True, name='eval_representation')
    def eval_representation(self, include_attributes: list[str] | None=None) -> str:
        from system.dom.serializer.eval_serializer import DOMEvalSerializer
        if not self._root:
            return 'Empty DOM tree (you might have to wait for the page to load)'
        include_attributes = include_attributes or DEFAULT_INCLUDE_ATTRIBUTES
        return DOMEvalSerializer.serialize_tree(self._root, include_attributes)

@dataclass
class DOMInteractedElement:
    node_id: int
    backend_node_id: int
    frame_id: str | None
    node_type: NodeType
    node_value: str
    node_name: str
    attributes: dict[str, str] | None
    bounds: DOMRect | None
    x_path: str
    element_hash: int
    stable_hash: int | None = None
    ax_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {'node_id': self.node_id, 'backend_node_id': self.backend_node_id, 'frame_id': self.frame_id, 'node_type': self.node_type.value, 'node_value': self.node_value, 'node_name': self.node_name, 'attributes': self.attributes, 'x_path': self.x_path, 'element_hash': self.element_hash, 'stable_hash': self.stable_hash, 'bounds': self.bounds.to_dict() if self.bounds else None, 'ax_name': self.ax_name}

    @classmethod
    def load_from_enhanced_dom_tree(cls, enhanced_dom_tree: EnhancedDOMTreeNode) -> 'DOMInteractedElement':
        ax_name = None
        if enhanced_dom_tree.ax_node and enhanced_dom_tree.ax_node.name:
            ax_name = enhanced_dom_tree.ax_node.name
        return cls(node_id=enhanced_dom_tree.node_id, backend_node_id=enhanced_dom_tree.backend_node_id, frame_id=enhanced_dom_tree.frame_id, node_type=enhanced_dom_tree.node_type, node_value=enhanced_dom_tree.node_value, node_name=enhanced_dom_tree.node_name, attributes=enhanced_dom_tree.attributes, bounds=enhanced_dom_tree.snapshot_node.bounds if enhanced_dom_tree.snapshot_node else None, x_path=enhanced_dom_tree.xpath, element_hash=hash(enhanced_dom_tree), stable_hash=enhanced_dom_tree.compute_stable_hash(), ax_name=ax_name)