from cdp_use.cdp.domsnapshot.commands import CaptureSnapshotReturns
from cdp_use.cdp.domsnapshot.types import LayoutTreeSnapshot, NodeTreeSnapshot, RareBooleanData
from system.dom.views import DOMRect, EnhancedSnapshotNode
REQUIRED_COMPUTED_STYLES = ['display', 'visibility', 'opacity', 'overflow', 'overflow-x', 'overflow-y', 'cursor', 'pointer-events', 'position', 'background-color']

def _parse_rare_boolean_data(rare_data: RareBooleanData, index: int) -> bool | None:
    return index in rare_data['index']

def _parse_computed_styles(strings: list[str], style_indices: list[int]) -> dict[str, str]:
    styles = {}
    for i, style_index in enumerate(style_indices):
        if i < len(REQUIRED_COMPUTED_STYLES) and 0 <= style_index < len(strings):
            styles[REQUIRED_COMPUTED_STYLES[i]] = strings[style_index]
    return styles

def build_snapshot_lookup(snapshot: CaptureSnapshotReturns, device_pixel_ratio: float=1.0) -> dict[int, EnhancedSnapshotNode]:
    import logging
    logger = logging.getLogger('system.dom.enhanced_snapshot')
    snapshot_lookup: dict[int, EnhancedSnapshotNode] = {}
    if not snapshot['documents']:
        return snapshot_lookup
    strings = snapshot['strings']
    logger.debug(f"🔍 SNAPSHOT: Processing {len(snapshot['documents'])} documents with {len(strings)} strings")
    for doc_idx, document in enumerate(snapshot['documents']):
        nodes: NodeTreeSnapshot = document['nodes']
        layout: LayoutTreeSnapshot = document['layout']
        backend_node_to_snapshot_index = {}
        if 'backendNodeId' in nodes:
            for i, backend_node_id in enumerate(nodes['backendNodeId']):
                backend_node_to_snapshot_index[backend_node_id] = i
        doc_url = strings[document.get('documentURL', 0)] if document.get('documentURL', 0) < len(strings) else 'N/A'
        logger.debug(f"🔍 SNAPSHOT doc[{doc_idx}]: url={doc_url[:80]}... has {len(backend_node_to_snapshot_index)} nodes, layout has {len(layout.get('nodeIndex', []))} entries")
        layout_index_map = {}
        if layout and 'nodeIndex' in layout:
            for layout_idx, node_index in enumerate(layout['nodeIndex']):
                if node_index not in layout_index_map:
                    layout_index_map[node_index] = layout_idx
        for backend_node_id, snapshot_index in backend_node_to_snapshot_index.items():
            is_clickable = None
            if 'isClickable' in nodes:
                is_clickable = _parse_rare_boolean_data(nodes['isClickable'], snapshot_index)
            cursor_style = None
            is_visible = None
            bounding_box = None
            computed_styles = {}
            paint_order = None
            client_rects = None
            scroll_rects = None
            stacking_contexts = None
            if snapshot_index in layout_index_map:
                layout_idx = layout_index_map[snapshot_index]
                if layout_idx < len(layout.get('bounds', [])):
                    bounds = layout['bounds'][layout_idx]
                    if len(bounds) >= 4:
                        raw_x, raw_y, raw_width, raw_height = (bounds[0], bounds[1], bounds[2], bounds[3])
                        bounding_box = DOMRect(x=raw_x / device_pixel_ratio, y=raw_y / device_pixel_ratio, width=raw_width / device_pixel_ratio, height=raw_height / device_pixel_ratio)
                    if layout_idx < len(layout.get('styles', [])):
                        style_indices = layout['styles'][layout_idx]
                        computed_styles = _parse_computed_styles(strings, style_indices)
                        cursor_style = computed_styles.get('cursor')
                    if layout_idx < len(layout.get('paintOrders', [])):
                        paint_order = layout.get('paintOrders', [])[layout_idx]
                    client_rects_data = layout.get('clientRects', [])
                    if layout_idx < len(client_rects_data):
                        client_rect_data = client_rects_data[layout_idx]
                        if client_rect_data and len(client_rect_data) >= 4:
                            client_rects = DOMRect(x=client_rect_data[0], y=client_rect_data[1], width=client_rect_data[2], height=client_rect_data[3])
                    scroll_rects_data = layout.get('scrollRects', [])
                    if layout_idx < len(scroll_rects_data):
                        scroll_rect_data = scroll_rects_data[layout_idx]
                        if scroll_rect_data and len(scroll_rect_data) >= 4:
                            scroll_rects = DOMRect(x=scroll_rect_data[0], y=scroll_rect_data[1], width=scroll_rect_data[2], height=scroll_rect_data[3])
                    if layout_idx < len(layout.get('stackingContexts', [])):
                        stacking_contexts = layout.get('stackingContexts', {}).get('index', [])[layout_idx]
            snapshot_lookup[backend_node_id] = EnhancedSnapshotNode(is_clickable=is_clickable, cursor_style=cursor_style, bounds=bounding_box, clientRects=client_rects, scrollRects=scroll_rects, computed_styles=computed_styles if computed_styles else None, paint_order=paint_order, stacking_contexts=stacking_contexts)
    with_bounds = sum((1 for n in snapshot_lookup.values() if n.bounds))
    logger.debug(f'🔍 SNAPSHOT: Built lookup with {len(snapshot_lookup)} total entries, {with_bounds} have bounds')
    return snapshot_lookup