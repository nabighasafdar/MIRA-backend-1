import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any
from system.dom.serializer.html_serializer import HTMLSerializer
from system.dom.service import DomService
from system.dom.views import MarkdownChunk
if TYPE_CHECKING:
    from system.browser.session import BrowserSession
    from system.browser.watchdogs.dom_watchdog import DOMWatchdog

async def extract_clean_markdown(browser_session: 'BrowserSession | None'=None, dom_service: DomService | None=None, target_id: str | None=None, extract_links: bool=False) -> tuple[str, dict[str, Any]]:
    if browser_session is not None:
        if dom_service is not None or target_id is not None:
            raise ValueError('Cannot specify both browser_session and dom_service/target_id')
        enhanced_dom_tree = await _get_enhanced_dom_tree_from_browser_session(browser_session)
        current_url = await browser_session.get_current_page_url()
        method = 'enhanced_dom_tree'
    elif dom_service is not None and target_id is not None:
        enhanced_dom_tree, _ = await dom_service.get_dom_tree(target_id=target_id, all_frames=None)
        current_url = None
        method = 'dom_service'
    else:
        raise ValueError('Must provide either browser_session or both dom_service and target_id')
    html_serializer = HTMLSerializer(extract_links=extract_links)
    page_html = html_serializer.serialize(enhanced_dom_tree)
    original_html_length = len(page_html)
    from markdownify import markdownify as md
    content = md(page_html, heading_style='ATX', strip=['script', 'style'], bullets='-', code_language='', escape_asterisks=False, escape_underscores=False, escape_misc=False, autolinks=False, default_title=False, keep_inline_images_in=[])
    initial_markdown_length = len(content)
    content = re.sub('%[0-9A-Fa-f]{2}', '', content)
    content, chars_filtered = _preprocess_markdown_content(content)
    final_filtered_length = len(content)
    stats = {'method': method, 'original_html_chars': original_html_length, 'initial_markdown_chars': initial_markdown_length, 'filtered_chars_removed': chars_filtered, 'final_filtered_chars': final_filtered_length}
    if current_url:
        stats['url'] = current_url
    return (content, stats)

async def _get_enhanced_dom_tree_from_browser_session(browser_session: 'BrowserSession'):
    dom_watchdog: DOMWatchdog | None = browser_session._dom_watchdog
    assert dom_watchdog is not None, 'DOMWatchdog not available'
    if dom_watchdog.enhanced_dom_tree is not None:
        return dom_watchdog.enhanced_dom_tree
    await dom_watchdog._build_dom_tree_without_highlights()
    enhanced_dom_tree = dom_watchdog.enhanced_dom_tree
    assert enhanced_dom_tree is not None, 'Enhanced DOM tree not available'
    return enhanced_dom_tree

def _preprocess_markdown_content(content: str, max_newlines: int=3) -> tuple[str, int]:
    original_length = len(content)
    content = re.sub('`\\{["\\w].*?\\}`', '', content, flags=re.DOTALL)
    content = re.sub('\\{"\\$type":[^}]{100,}\\}', '', content)
    content = re.sub('\\{"[^"]{5,}":\\{[^}]{100,}\\}', '', content)
    content = re.sub('\\n{4,}', '\n' * max_newlines, content)
    lines = content.split('\n')
    filtered_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            if (stripped.startswith('{') or stripped.startswith('[')) and len(stripped) > 100:
                continue
            filtered_lines.append(line)
    content = '\n'.join(filtered_lines)
    content = content.strip()
    chars_filtered = original_length - len(content)
    return (content, chars_filtered)

class _BlockType(Enum):
    HEADER = auto()
    CODE_FENCE = auto()
    TABLE = auto()
    LIST_ITEM = auto()
    PARAGRAPH = auto()
    BLANK = auto()

@dataclass(slots=True)
class _AtomicBlock:
    block_type: _BlockType
    lines: list[str]
    char_start: int
    char_end: int
_TABLE_ROW_RE = re.compile('^\\s*\\|.*\\|\\s*$')
_LIST_ITEM_RE = re.compile('^(\\s*)([-*+]|\\d+[.)]) ')
_LIST_CONTINUATION_RE = re.compile('^(\\s{2,}|\\t)')

def _parse_atomic_blocks(content: str) -> list[_AtomicBlock]:
    lines = content.split('\n')
    blocks: list[_AtomicBlock] = []
    i = 0
    offset = 0
    while i < len(lines):
        line = lines[i]
        line_len = len(line) + 1
        if not line.strip():
            blocks.append(_AtomicBlock(block_type=_BlockType.BLANK, lines=[line], char_start=offset, char_end=offset + line_len))
            offset += line_len
            i += 1
            continue
        if line.strip().startswith('```'):
            fence_lines = [line]
            fence_end = offset + line_len
            i += 1
            while i < len(lines):
                fence_line = lines[i]
                fence_line_len = len(fence_line) + 1
                fence_lines.append(fence_line)
                fence_end += fence_line_len
                i += 1
                if fence_line.strip().startswith('```') and len(fence_lines) > 1:
                    break
            blocks.append(_AtomicBlock(block_type=_BlockType.CODE_FENCE, lines=fence_lines, char_start=offset, char_end=fence_end))
            offset = fence_end
            continue
        if line.lstrip().startswith('#'):
            blocks.append(_AtomicBlock(block_type=_BlockType.HEADER, lines=[line], char_start=offset, char_end=offset + line_len))
            offset += line_len
            i += 1
            continue
        if _TABLE_ROW_RE.match(line):
            header_lines = [line]
            header_end = offset + line_len
            i += 1
            if i < len(lines) and _TABLE_ROW_RE.match(lines[i]) and ('---' in lines[i]):
                sep = lines[i]
                sep_len = len(sep) + 1
                header_lines.append(sep)
                header_end += sep_len
                i += 1
            blocks.append(_AtomicBlock(block_type=_BlockType.TABLE, lines=header_lines, char_start=offset, char_end=header_end))
            offset = header_end
            while i < len(lines) and _TABLE_ROW_RE.match(lines[i]):
                row = lines[i]
                row_len = len(row) + 1
                blocks.append(_AtomicBlock(block_type=_BlockType.TABLE, lines=[row], char_start=offset, char_end=offset + row_len))
                offset += row_len
                i += 1
            continue
        if _LIST_ITEM_RE.match(line):
            list_lines = [line]
            list_end = offset + line_len
            i += 1
            while i < len(lines):
                next_line = lines[i]
                next_len = len(next_line) + 1
                if _LIST_ITEM_RE.match(next_line):
                    list_lines.append(next_line)
                    list_end += next_len
                    i += 1
                    continue
                if next_line.strip() and _LIST_CONTINUATION_RE.match(next_line):
                    list_lines.append(next_line)
                    list_end += next_len
                    i += 1
                    continue
                break
            blocks.append(_AtomicBlock(block_type=_BlockType.LIST_ITEM, lines=list_lines, char_start=offset, char_end=list_end))
            offset = list_end
            continue
        para_lines = [line]
        para_end = offset + line_len
        i += 1
        while i < len(lines) and lines[i].strip():
            nl = lines[i]
            if nl.lstrip().startswith('#') or nl.strip().startswith('```') or _TABLE_ROW_RE.match(nl) or _LIST_ITEM_RE.match(nl):
                break
            nl_len = len(nl) + 1
            para_lines.append(nl)
            para_end += nl_len
            i += 1
        blocks.append(_AtomicBlock(block_type=_BlockType.PARAGRAPH, lines=para_lines, char_start=offset, char_end=para_end))
        offset = para_end
    if blocks and content and (not content.endswith('\n')):
        blocks[-1] = _AtomicBlock(block_type=blocks[-1].block_type, lines=blocks[-1].lines, char_start=blocks[-1].char_start, char_end=len(content))
    return blocks

def _block_text(block: _AtomicBlock) -> str:
    return '\n'.join(block.lines)

def _get_table_header(block: _AtomicBlock) -> str | None:
    assert block.block_type == _BlockType.TABLE
    if len(block.lines) < 2:
        return None
    sep_line = block.lines[1]
    if '---' in sep_line or '- -' in sep_line:
        return block.lines[0] + '\n' + block.lines[1]
    return None

def chunk_markdown_by_structure(content: str, max_chunk_chars: int=100000, overlap_lines: int=5, start_from_char: int=0) -> list[MarkdownChunk]:
    if not content:
        return [MarkdownChunk(content='', chunk_index=0, total_chunks=1, char_offset_start=0, char_offset_end=0, overlap_prefix='', has_more=False)]
    if start_from_char >= len(content):
        return []
    blocks = _parse_atomic_blocks(content)
    if not blocks:
        return []
    raw_chunks: list[list[_AtomicBlock]] = []
    current_chunk: list[_AtomicBlock] = []
    current_size = 0
    for block in blocks:
        block_size = block.char_end - block.char_start
        if current_size + block_size > max_chunk_chars and current_chunk:
            best_split = len(current_chunk)
            for j in range(len(current_chunk) - 1, 0, -1):
                if current_chunk[j].block_type == _BlockType.HEADER:
                    prefix_size = sum((b.char_end - b.char_start for b in current_chunk[:j]))
                    if prefix_size >= max_chunk_chars * 0.5:
                        best_split = j
                        break
            raw_chunks.append(current_chunk[:best_split])
            current_chunk = current_chunk[best_split:]
            current_size = sum((b.char_end - b.char_start for b in current_chunk))
        current_chunk.append(block)
        current_size += block_size
    if current_chunk:
        raw_chunks.append(current_chunk)
    total_chunks = len(raw_chunks)
    chunks: list[MarkdownChunk] = []
    prev_chunk_last_table_header: str | None = None
    for idx, chunk_blocks in enumerate(raw_chunks):
        chunk_text = '\n'.join((_block_text(b) for b in chunk_blocks))
        char_start = chunk_blocks[0].char_start
        char_end = chunk_blocks[-1].char_end
        overlap = ''
        if idx > 0:
            prev_blocks = raw_chunks[idx - 1]
            prev_text = '\n'.join((_block_text(b) for b in prev_blocks))
            prev_lines = prev_text.split('\n')
            first_block = chunk_blocks[0]
            if first_block.block_type == _BlockType.TABLE and prev_chunk_last_table_header:
                trailing = prev_lines[-overlap_lines:] if overlap_lines > 0 else []
                header_lines = prev_chunk_last_table_header.split('\n')
                combined = list(header_lines)
                for tl in trailing:
                    if tl not in combined:
                        combined.append(tl)
                overlap = '\n'.join(combined)
            elif overlap_lines > 0:
                overlap = '\n'.join(prev_lines[-overlap_lines:])
        for b in chunk_blocks:
            if b.block_type == _BlockType.TABLE:
                hdr = _get_table_header(b)
                if hdr is not None:
                    prev_chunk_last_table_header = hdr
        has_more = idx < total_chunks - 1
        chunks.append(MarkdownChunk(content=chunk_text, chunk_index=idx, total_chunks=total_chunks, char_offset_start=char_start, char_offset_end=char_end, overlap_prefix=overlap, has_more=has_more))
    if start_from_char > 0:
        for i, chunk in enumerate(chunks):
            if chunk.char_offset_end > start_from_char:
                return chunks[i:]
        return []
    return chunks