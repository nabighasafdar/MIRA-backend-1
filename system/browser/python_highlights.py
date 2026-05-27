import asyncio
import base64
import io
import logging
import os
from PIL import Image, ImageDraw, ImageFont
from system.dom.views import DOMSelectorMap, EnhancedDOMTreeNode
from system.utils import time_execution_async
logger = logging.getLogger(__name__)
_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont | None] = {}
_FONT_PATHS = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf', '/System/Library/Fonts/Arial.ttf', 'C:\\Windows\\Fonts\\arial.ttf', 'arial.ttf', 'Arial Bold.ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf']

def get_cross_platform_font(font_size: int) -> ImageFont.FreeTypeFont | None:
    cache_key = ('system_font', font_size)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]
    font = None
    for font_path in _FONT_PATHS:
        try:
            font = ImageFont.truetype(font_path, font_size)
            break
        except OSError:
            continue
    _FONT_CACHE[cache_key] = font
    return font

def cleanup_font_cache() -> None:
    global _FONT_CACHE
    _FONT_CACHE.clear()
ELEMENT_COLORS = {'button': '#FF6B6B', 'input': '#4ECDC4', 'select': '#45B7D1', 'a': '#96CEB4', 'textarea': '#FF8C42', 'default': '#DDA0DD'}
ELEMENT_TYPE_MAP = {'button': 'button', 'input': 'input', 'select': 'select', 'a': 'a', 'textarea': 'textarea'}

def get_element_color(tag_name: str, element_type: str | None=None) -> str:
    if tag_name == 'input' and element_type:
        if element_type in ['button', 'submit']:
            return ELEMENT_COLORS['button']
    return ELEMENT_COLORS.get(tag_name.lower(), ELEMENT_COLORS['default'])

def should_show_index_overlay(backend_node_id: int | None) -> bool:
    return backend_node_id is not None

def draw_enhanced_bounding_box_with_text(draw, bbox: tuple[int, int, int, int], color: str, text: str | None=None, font: ImageFont.FreeTypeFont | None=None, element_type: str='div', image_size: tuple[int, int]=(2000, 1500), device_pixel_ratio: float=1.0) -> None:
    x1, y1, x2, y2 = bbox
    dash_length = 4
    gap_length = 8
    line_width = 2

    def draw_dashed_line(start_x, start_y, end_x, end_y):
        if start_x == end_x:
            y = start_y
            while y < end_y:
                dash_end = min(y + dash_length, end_y)
                draw.line([(start_x, y), (start_x, dash_end)], fill=color, width=line_width)
                y += dash_length + gap_length
        else:
            x = start_x
            while x < end_x:
                dash_end = min(x + dash_length, end_x)
                draw.line([(x, start_y), (dash_end, start_y)], fill=color, width=line_width)
                x += dash_length + gap_length
    draw_dashed_line(x1, y1, x2, y1)
    draw_dashed_line(x2, y1, x2, y2)
    draw_dashed_line(x2, y2, x1, y2)
    draw_dashed_line(x1, y2, x1, y1)
    if text:
        try:
            img_width, img_height = image_size
            css_width = img_width
            base_font_size = max(10, min(20, int(css_width * 0.01)))
            big_font = get_cross_platform_font(base_font_size)
            if big_font is None:
                big_font = font
            if big_font:
                bbox_text = draw.textbbox((0, 0), text, font=big_font)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
            else:
                bbox_text = draw.textbbox((0, 0), text)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
            padding = max(4, min(10, int(css_width * 0.005)))
            element_width = x2 - x1
            element_height = y2 - y1
            container_width = text_width + padding * 2
            container_height = text_height + padding * 2
            bg_x1 = x1 + (element_width - container_width) // 2
            if element_width < 60 or element_height < 30:
                bg_y1 = max(0, y1 - container_height - 5)
            else:
                bg_y1 = y1 + 2
            bg_x2 = bg_x1 + container_width
            bg_y2 = bg_y1 + container_height
            text_x = bg_x1 + (container_width - text_width) // 2
            text_y = bg_y1 + (container_height - text_height) // 2 - bbox_text[1]
            img_width, img_height = image_size
            if bg_x1 < 0:
                offset = -bg_x1
                bg_x1 += offset
                bg_x2 += offset
                text_x += offset
            if bg_y1 < 0:
                offset = -bg_y1
                bg_y1 += offset
                bg_y2 += offset
                text_y += offset
            if bg_x2 > img_width:
                offset = bg_x2 - img_width
                bg_x1 -= offset
                bg_x2 -= offset
                text_x -= offset
            if bg_y2 > img_height:
                offset = bg_y2 - img_height
                bg_y1 -= offset
                bg_y2 -= offset
                text_y -= offset
            draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=color, outline='white', width=2)
            draw.text((text_x, text_y), text, fill='white', font=big_font or font)
        except Exception as e:
            logger.debug(f'Failed to draw enhanced text overlay: {e}')

def draw_bounding_box_with_text(draw, bbox: tuple[int, int, int, int], color: str, text: str | None=None, font: ImageFont.FreeTypeFont | None=None) -> None:
    x1, y1, x2, y2 = bbox
    dash_length = 2
    gap_length = 6
    x = x1
    while x < x2:
        end_x = min(x + dash_length, x2)
        draw.line([(x, y1), (end_x, y1)], fill=color, width=2)
        draw.line([(x, y1 + 1), (end_x, y1 + 1)], fill=color, width=2)
        x += dash_length + gap_length
    x = x1
    while x < x2:
        end_x = min(x + dash_length, x2)
        draw.line([(x, y2), (end_x, y2)], fill=color, width=2)
        draw.line([(x, y2 - 1), (end_x, y2 - 1)], fill=color, width=2)
        x += dash_length + gap_length
    y = y1
    while y < y2:
        end_y = min(y + dash_length, y2)
        draw.line([(x1, y), (x1, end_y)], fill=color, width=2)
        draw.line([(x1 + 1, y), (x1 + 1, end_y)], fill=color, width=2)
        y += dash_length + gap_length
    y = y1
    while y < y2:
        end_y = min(y + dash_length, y2)
        draw.line([(x2, y), (x2, end_y)], fill=color, width=2)
        draw.line([(x2 - 1, y), (x2 - 1, end_y)], fill=color, width=2)
        y += dash_length + gap_length
    if text:
        try:
            if font:
                bbox_text = draw.textbbox((0, 0), text, font=font)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
            else:
                bbox_text = draw.textbbox((0, 0), text)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
            padding = 5
            element_width = x2 - x1
            element_height = y2 - y1
            element_area = element_width * element_height
            index_box_area = (text_width + padding * 2) * (text_height + padding * 2)
            size_ratio = element_area / max(index_box_area, 1)
            if size_ratio < 4:
                text_x = x2 + padding
                text_y = y2 - text_height
                text_x = min(text_x, 1200 - text_width - padding)
                text_y = max(text_y, 0)
            elif size_ratio < 16:
                text_x = x2 - text_width - padding
                text_y = y2 - text_height - padding
            else:
                text_x = x1 + (element_width - text_width) // 2
                text_y = y1 + (element_height - text_height) // 2
            text_x = max(0, min(text_x, 1200 - text_width))
            text_y = max(0, min(text_y, 800 - text_height))
            bg_x1 = text_x - padding
            bg_y1 = text_y - padding
            bg_x2 = text_x + text_width + padding
            bg_y2 = text_y + text_height + padding
            draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill='white', outline='black', width=2)
            draw.text((text_x, text_y), text, fill='black', font=font)
        except Exception as e:
            logger.debug(f'Failed to draw text overlay: {e}')

def process_element_highlight(element_id: int, element: EnhancedDOMTreeNode, draw, device_pixel_ratio: float, font, filter_highlight_ids: bool, image_size: tuple[int, int]) -> None:
    try:
        if not element.absolute_position:
            return
        bounds = element.absolute_position
        x1 = int(bounds.x * device_pixel_ratio)
        y1 = int(bounds.y * device_pixel_ratio)
        x2 = int((bounds.x + bounds.width) * device_pixel_ratio)
        y2 = int((bounds.y + bounds.height) * device_pixel_ratio)
        img_width, img_height = image_size
        x1 = max(0, min(x1, img_width))
        y1 = max(0, min(y1, img_height))
        x2 = max(x1, min(x2, img_width))
        y2 = max(y1, min(y2, img_height))
        if x2 - x1 < 2 or y2 - y1 < 2:
            return
        tag_name = element.tag_name if hasattr(element, 'tag_name') else 'div'
        element_type = None
        if hasattr(element, 'attributes') and element.attributes:
            element_type = element.attributes.get('type')
        color = get_element_color(tag_name, element_type)
        backend_node_id = getattr(element, 'backend_node_id', None)
        index_text = None
        if backend_node_id is not None:
            if filter_highlight_ids:
                meaningful_text = element.get_meaningful_text_for_llm()
                if len(meaningful_text) < 3:
                    index_text = str(backend_node_id)
            else:
                index_text = str(backend_node_id)
        draw_enhanced_bounding_box_with_text(draw, (x1, y1, x2, y2), color, index_text, font, tag_name, image_size, device_pixel_ratio)
    except Exception as e:
        logger.debug(f'Failed to draw highlight for element {element_id}: {e}')

@time_execution_async('create_highlighted_screenshot')
async def create_highlighted_screenshot(screenshot_b64: str, selector_map: DOMSelectorMap, device_pixel_ratio: float=1.0, viewport_offset_x: int=0, viewport_offset_y: int=0, filter_highlight_ids: bool=True) -> str:
    try:
        screenshot_data = base64.b64decode(screenshot_b64)
        image = Image.open(io.BytesIO(screenshot_data)).convert('RGBA')
        draw = ImageDraw.Draw(image)
        font = get_cross_platform_font(12)
        for element_id, element in selector_map.items():
            process_element_highlight(element_id, element, draw, device_pixel_ratio, font, filter_highlight_ids, image.size)
        output_buffer = io.BytesIO()
        try:
            image.save(output_buffer, format='PNG')
            output_buffer.seek(0)
            highlighted_b64 = base64.b64encode(output_buffer.getvalue()).decode('utf-8')
            logger.debug(f'Successfully created highlighted screenshot with {len(selector_map)} elements')
            return highlighted_b64
        finally:
            output_buffer.close()
            if 'image' in locals():
                image.close()
    except Exception as e:
        logger.error(f'Failed to create highlighted screenshot: {e}')
        if 'image' in locals():
            image.close()
        return screenshot_b64

async def get_viewport_info_from_cdp(cdp_session) -> tuple[float, int, int]:
    try:
        metrics = await cdp_session.cdp_client.send.Page.getLayoutMetrics(session_id=cdp_session.session_id)
        visual_viewport = metrics.get('visualViewport', {})
        css_visual_viewport = metrics.get('cssVisualViewport', {})
        css_layout_viewport = metrics.get('cssLayoutViewport', {})
        css_width = css_visual_viewport.get('clientWidth', css_layout_viewport.get('clientWidth', 1280.0))
        device_width = visual_viewport.get('clientWidth', css_width)
        device_pixel_ratio = device_width / css_width if css_width > 0 else 1.0
        scroll_x = int(css_visual_viewport.get('pageX', 0))
        scroll_y = int(css_visual_viewport.get('pageY', 0))
        return (float(device_pixel_ratio), scroll_x, scroll_y)
    except Exception as e:
        logger.debug(f'Failed to get viewport info from CDP: {e}')
        return (1.0, 0, 0)

@time_execution_async('create_highlighted_screenshot_async')
async def create_highlighted_screenshot_async(screenshot_b64: str, selector_map: DOMSelectorMap, cdp_session=None, filter_highlight_ids: bool=True) -> str:
    device_pixel_ratio = 1.0
    viewport_offset_x = 0
    viewport_offset_y = 0
    if cdp_session:
        try:
            device_pixel_ratio, viewport_offset_x, viewport_offset_y = await get_viewport_info_from_cdp(cdp_session)
        except Exception as e:
            logger.debug(f'Failed to get viewport info from CDP: {e}')
    final_screenshot = await create_highlighted_screenshot(screenshot_b64, selector_map, device_pixel_ratio, viewport_offset_x, viewport_offset_y, filter_highlight_ids)
    filename = os.getenv('BROWSER_USE_SCREENSHOT_FILE')
    if filename:

        def _write_screenshot():
            try:
                with open(filename, 'wb') as f:
                    f.write(base64.b64decode(final_screenshot))
                logger.debug('Saved screenshot to ' + str(filename))
            except Exception as e:
                logger.warning(f'Failed to save screenshot to {filename}: {e}')
        await asyncio.to_thread(_write_screenshot)
    return final_screenshot
__all__ = ['create_highlighted_screenshot', 'create_highlighted_screenshot_async', 'cleanup_font_cache']