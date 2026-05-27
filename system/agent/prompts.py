import importlib.resources
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Optional
from system.dom.views import NodeType, SimplifiedNode
from system.llm.messages import ContentPartImageParam, ContentPartTextParam, ImageURL, SystemMessage, UserMessage
from system.observability import observe_debug
from system.utils import is_new_tab_page, sanitize_surrogates
if TYPE_CHECKING:
    from system.agent.views import AgentStepInfo
    from system.browser.views import BrowserStateSummary
    from system.filesystem.file_system import FileSystem

def _is_anthropic_4_5_model(model_name: str | None) -> bool:
    if not model_name:
        return False
    model_lower = model_name.lower()
    is_opus_4_5 = 'opus' in model_lower and ('4.5' in model_lower or '4-5' in model_lower)
    is_haiku_4_5 = 'haiku' in model_lower and ('4.5' in model_lower or '4-5' in model_lower)
    return is_opus_4_5 or is_haiku_4_5

class SystemPrompt:

    def __init__(self, max_actions_per_step: int=3, override_system_message: str | None=None, extend_system_message: str | None=None, use_thinking: bool=True, flash_mode: bool=False, is_anthropic: bool=False, is_system_model: bool=False, model_name: str | None=None):
        self.max_actions_per_step = max_actions_per_step
        self.use_thinking = use_thinking
        self.flash_mode = flash_mode
        self.is_anthropic = is_anthropic
        self.is_system_model = is_system_model
        self.model_name = model_name
        self.is_anthropic_4_5 = _is_anthropic_4_5_model(model_name)
        prompt = ''
        if override_system_message is not None:
            prompt = override_system_message
        else:
            self._load_prompt_template()
            prompt = self.prompt_template.format(max_actions=self.max_actions_per_step)
        if extend_system_message:
            prompt += f'\n{extend_system_message}'
        self.system_message = SystemMessage(content=prompt, cache=True)

    def _load_prompt_template(self) -> None:
        try:
            if self.is_system_model:
                if self.flash_mode:
                    template_filename = 'system_prompt_system_flash.md'
                elif self.use_thinking:
                    template_filename = 'system_prompt_system.md'
                else:
                    template_filename = 'system_prompt_system_no_thinking.md'
            elif self.is_anthropic_4_5 and self.flash_mode:
                template_filename = 'system_prompt_anthropic_flash.md'
            elif self.flash_mode and self.is_anthropic:
                template_filename = 'system_prompt_flash_anthropic.md'
            elif self.flash_mode:
                template_filename = 'system_prompt_flash.md'
            elif self.use_thinking:
                template_filename = 'system_prompt.md'
            else:
                template_filename = 'system_prompt_no_thinking.md'
            with importlib.resources.files('system.agent.system_prompts').joinpath(template_filename).open('r', encoding='utf-8') as f:
                self.prompt_template = f.read()
        except Exception as e:
            raise RuntimeError(f'Failed to load system prompt template: {e}')

    def get_system_message(self) -> SystemMessage:
        return self.system_message

class AgentMessagePrompt:
    vision_detail_level: Literal['auto', 'low', 'high']

    def __init__(self, browser_state_summary: 'BrowserStateSummary', file_system: 'FileSystem', agent_history_description: str | None=None, read_state_description: str | None=None, task: str | None=None, include_attributes: list[str] | None=None, step_info: Optional['AgentStepInfo']=None, page_filtered_actions: str | None=None, max_clickable_elements_length: int=40000, sensitive_data: str | None=None, available_file_paths: list[str] | None=None, screenshots: list[str] | None=None, vision_detail_level: Literal['auto', 'low', 'high']='auto', include_recent_events: bool=False, sample_images: list[ContentPartTextParam | ContentPartImageParam] | None=None, read_state_images: list[dict] | None=None, llm_screenshot_size: tuple[int, int] | None=None, unavailable_skills_info: str | None=None, plan_description: str | None=None):
        self.browser_state: 'BrowserStateSummary' = browser_state_summary
        self.file_system: 'FileSystem | None' = file_system
        self.agent_history_description: str | None = agent_history_description
        self.read_state_description: str | None = read_state_description
        self.task: str | None = task
        self.include_attributes = include_attributes
        self.step_info = step_info
        self.page_filtered_actions: str | None = page_filtered_actions
        self.max_clickable_elements_length: int = max_clickable_elements_length
        self.sensitive_data: str | None = sensitive_data
        self.available_file_paths: list[str] | None = available_file_paths
        self.screenshots = screenshots or []
        self.vision_detail_level = vision_detail_level
        self.include_recent_events = include_recent_events
        self.sample_images = sample_images or []
        self.read_state_images = read_state_images or []
        self.unavailable_skills_info: str | None = unavailable_skills_info
        self.plan_description: str | None = plan_description
        self.llm_screenshot_size = llm_screenshot_size
        assert self.browser_state

    def _extract_page_statistics(self) -> dict[str, int]:
        stats = {'links': 0, 'iframes': 0, 'shadow_open': 0, 'shadow_closed': 0, 'scroll_containers': 0, 'images': 0, 'interactive_elements': 0, 'total_elements': 0}
        if not self.browser_state.dom_state or not self.browser_state.dom_state._root:
            return stats

        def traverse_node(node: SimplifiedNode) -> None:
            if not node or not node.original_node:
                return
            original = node.original_node
            stats['total_elements'] += 1
            if original.node_type == NodeType.ELEMENT_NODE:
                tag = original.tag_name.lower() if original.tag_name else ''
                if tag == 'a':
                    stats['links'] += 1
                elif tag in ('iframe', 'frame'):
                    stats['iframes'] += 1
                elif tag == 'img':
                    stats['images'] += 1
                if original.is_actually_scrollable:
                    stats['scroll_containers'] += 1
                if node.is_interactive:
                    stats['interactive_elements'] += 1
                if node.is_shadow_host:
                    has_closed_shadow = any((child.original_node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE and child.original_node.shadow_root_type and (child.original_node.shadow_root_type.lower() == 'closed') for child in node.children))
                    if has_closed_shadow:
                        stats['shadow_closed'] += 1
                    else:
                        stats['shadow_open'] += 1
            elif original.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
                pass
            for child in node.children:
                traverse_node(child)
        traverse_node(self.browser_state.dom_state._root)
        return stats

    @observe_debug(ignore_input=True, ignore_output=True, name='_get_browser_state_description')
    def _get_browser_state_description(self) -> str:
        page_stats = self._extract_page_statistics()
        stats_text = '<page_stats>'
        if page_stats['total_elements'] < 10:
            stats_text += 'Page appears empty (SPA not loaded?) - '
        stats_text += f"{page_stats['links']} links, {page_stats['interactive_elements']} interactive, "
        stats_text += f"{page_stats['iframes']} iframes"
        if page_stats['shadow_open'] > 0 or page_stats['shadow_closed'] > 0:
            stats_text += f", {page_stats['shadow_open']} shadow(open), {page_stats['shadow_closed']} shadow(closed)"
        if page_stats['images'] > 0:
            stats_text += f", {page_stats['images']} images"
        stats_text += f", {page_stats['total_elements']} total elements"
        stats_text += '</page_stats>\n'
        elements_text = self.browser_state.dom_state.llm_representation(include_attributes=self.include_attributes)
        if len(elements_text) > self.max_clickable_elements_length:
            elements_text = elements_text[:self.max_clickable_elements_length]
            truncated_text = f' (truncated to {self.max_clickable_elements_length} characters)'
        else:
            truncated_text = ''
        has_content_above = False
        has_content_below = False
        page_info_text = ''
        if self.browser_state.page_info:
            pi = self.browser_state.page_info
            pages_above = pi.pixels_above / pi.viewport_height if pi.viewport_height > 0 else 0
            pages_below = pi.pixels_below / pi.viewport_height if pi.viewport_height > 0 else 0
            has_content_above = pages_above > 0
            has_content_below = pages_below > 0
            total_pages = pi.page_height / pi.viewport_height if pi.viewport_height > 0 else 0
            current_page_position = pi.scroll_y / max(pi.page_height - pi.viewport_height, 1)
            page_info_text = '<page_info>'
            page_info_text += f'{pages_above:.1f} above, '
            page_info_text += f'{pages_below:.1f} below '
            page_info_text += '</page_info>\n'
        if elements_text != '':
            if not has_content_above:
                elements_text = f'[Start of page]\n{elements_text}'
            if not has_content_below:
                elements_text = f'{elements_text}\n[End of page]'
        else:
            elements_text = 'empty page'
        tabs_text = ''
        current_tab_candidates = []
        for tab in self.browser_state.tabs:
            if tab.url == self.browser_state.url and tab.title == self.browser_state.title:
                current_tab_candidates.append(tab.target_id)
        current_target_id = current_tab_candidates[0] if len(current_tab_candidates) == 1 else None
        for tab in self.browser_state.tabs:
            tabs_text += f'Tab {tab.target_id[-4:]}: {tab.url} - {tab.title[:30]}\n'
        current_tab_text = f'Current tab: {current_target_id[-4:]}' if current_target_id is not None else ''
        pdf_message = ''
        if self.browser_state.is_pdf_viewer:
            pdf_message = 'PDF viewer cannot be rendered. In this page, DO NOT use the extract action as PDF content cannot be rendered. '
            pdf_message += 'Use the read_file action on the downloaded PDF in available_file_paths to read the full text content.\n\n'
        recent_events_text = ''
        if self.include_recent_events and self.browser_state.recent_events:
            recent_events_text = f'Recent browser events: {self.browser_state.recent_events}\n'
        closed_popups_text = ''
        if self.browser_state.closed_popup_messages:
            closed_popups_text = 'Auto-closed JavaScript dialogs:\n'
            for popup_msg in self.browser_state.closed_popup_messages:
                closed_popups_text += f'  - {popup_msg}\n'
            closed_popups_text += '\n'
        browser_state = f'{stats_text}{current_tab_text}\nAvailable tabs:\n{tabs_text}\n{page_info_text}\n{recent_events_text}{closed_popups_text}{pdf_message}Interactive elements{truncated_text}:\n{elements_text}\n'
        return browser_state

    def _get_agent_state_description(self) -> str:
        if self.step_info:
            step_info_description = f'Step{self.step_info.step_number + 1} maximum:{self.step_info.max_steps}\n'
        else:
            step_info_description = ''
        time_str = datetime.now().strftime('%Y-%m-%d')
        step_info_description += f'Today:{time_str}'
        _todo_contents = self.file_system.get_todo_contents() if self.file_system else ''
        if not len(_todo_contents):
            _todo_contents = '[empty todo.md, fill it when applicable]'
        agent_state = f"\n<user_request>\n{self.task}\n</user_request>\n<file_system>\n{(self.file_system.describe() if self.file_system else 'No file system available')}\n</file_system>\n<todo_contents>\n{_todo_contents}\n</todo_contents>\n"
        if self.plan_description:
            agent_state += f'<plan>\n{self.plan_description}\n</plan>\n'
        if self.sensitive_data:
            agent_state += f'<sensitive_data>{self.sensitive_data}</sensitive_data>\n'
        agent_state += f'<step_info>{step_info_description}</step_info>\n'
        if self.available_file_paths:
            available_file_paths_text = '\n'.join(self.available_file_paths)
            agent_state += f'<available_file_paths>{available_file_paths_text}\nUse with absolute paths</available_file_paths>\n'
        return agent_state

    def _resize_screenshot(self, screenshot_b64: str) -> str:
        if not self.llm_screenshot_size:
            return screenshot_b64
        try:
            import base64
            import logging
            from io import BytesIO
            from PIL import Image
            img = Image.open(BytesIO(base64.b64decode(screenshot_b64)))
            if img.size == self.llm_screenshot_size:
                return screenshot_b64
            logging.getLogger(__name__).info(f'🔄 Resizing screenshot from {img.size[0]}x{img.size[1]} to {self.llm_screenshot_size[0]}x{self.llm_screenshot_size[1]} for LLM')
            img_resized = img.resize(self.llm_screenshot_size, Image.Resampling.LANCZOS)
            buffer = BytesIO()
            img_resized.save(buffer, format='PNG')
            return base64.b64encode(buffer.getvalue()).decode('utf-8')
        except Exception as e:
            logging.getLogger(__name__).warning(f'Failed to resize screenshot: {e}, using original')
            return screenshot_b64

    @observe_debug(ignore_input=True, ignore_output=True, name='get_user_message')
    def get_user_message(self, use_vision: bool=True) -> UserMessage:
        if is_new_tab_page(self.browser_state.url) and self.step_info is not None and (self.step_info.step_number == 0) and (len(self.browser_state.tabs) == 1):
            use_vision = False
        state_description = '<agent_history>\n' + (self.agent_history_description.strip('\n') if self.agent_history_description else '') + '\n</agent_history>\n\n'
        state_description += '<agent_state>\n' + self._get_agent_state_description().strip('\n') + '\n</agent_state>\n'
        state_description += '<browser_state>\n' + self._get_browser_state_description().strip('\n') + '\n</browser_state>\n'
        read_state_description = self.read_state_description.strip('\n').strip() if self.read_state_description else ''
        if read_state_description:
            state_description += '<read_state>\n' + read_state_description + '\n</read_state>\n'
        if self.page_filtered_actions:
            state_description += '<page_specific_actions>\n'
            state_description += self.page_filtered_actions + '\n'
            state_description += '</page_specific_actions>\n'
        if self.unavailable_skills_info:
            state_description += '\n' + self.unavailable_skills_info + '\n'
        state_description = sanitize_surrogates(state_description)
        has_images = bool(self.read_state_images)
        if use_vision is True and self.screenshots or has_images:
            content_parts: list[ContentPartTextParam | ContentPartImageParam] = [ContentPartTextParam(text=state_description)]
            content_parts.extend(self.sample_images)
            for i, screenshot in enumerate(self.screenshots):
                if i == len(self.screenshots) - 1:
                    label = 'Current screenshot:'
                else:
                    label = 'Previous screenshot:'
                content_parts.append(ContentPartTextParam(text=label))
                processed_screenshot = self._resize_screenshot(screenshot)
                content_parts.append(ContentPartImageParam(image_url=ImageURL(url=f'data:image/png;base64,{processed_screenshot}', media_type='image/png', detail=self.vision_detail_level)))
            for img_data in self.read_state_images:
                img_name = img_data.get('name', 'unknown')
                img_base64 = img_data.get('data', '')
                if not img_base64:
                    continue
                if img_name.lower().endswith('.png'):
                    media_type = 'image/png'
                else:
                    media_type = 'image/jpeg'
                content_parts.append(ContentPartTextParam(text=f'Image from file: {img_name}'))
                content_parts.append(ContentPartImageParam(image_url=ImageURL(url=f'data:{media_type};base64,{img_base64}', media_type=media_type, detail=self.vision_detail_level)))
            return UserMessage(content=content_parts, cache=True)
        return UserMessage(content=state_description, cache=True)

def get_rerun_summary_prompt(original_task: str, total_steps: int, success_count: int, error_count: int) -> str:
    return f'You are analyzing the completion of a rerun task. Based on the screenshot and execution info, provide a summary.\n\nOriginal task: {original_task}\n\nExecution statistics:\n- Total steps: {total_steps}\n- Successful steps: {success_count}\n- Failed steps: {error_count}\n\nAnalyze the screenshot to determine:\n1. Whether the task completed successfully\n2. What the final state shows\n3. Overall completion status (complete/partial/failed)\n\nRespond with:\n- summary: A clear, concise summary of what happened during the rerun\n- success: Whether the task completed successfully (true/false)\n- completion_status: One of "complete", "partial", or "failed"'

def get_rerun_summary_message(prompt: str, screenshot_b64: str | None=None) -> UserMessage:
    if screenshot_b64:
        content_parts: list[ContentPartTextParam | ContentPartImageParam] = [ContentPartTextParam(type='text', text=prompt), ContentPartImageParam(type='image_url', image_url=ImageURL(url=f'data:image/png;base64,{screenshot_b64}'))]
        return UserMessage(content=content_parts)
    else:
        return UserMessage(content=prompt)

def get_ai_step_system_prompt() -> str:
    return '\nYou are an expert at extracting data from webpages.\n\n<input>\nYou will be given:\n1. A query describing what to extract\n2. The markdown of the webpage (filtered to remove noise)\n3. Optionally, a screenshot of the current page state\n</input>\n\n<instructions>\n- Extract information from the webpage that is relevant to the query\n- ONLY use the information available in the webpage - do not make up information\n- If the information is not available, mention that clearly\n- If the query asks for all items, list all of them\n</instructions>\n\n<output>\n- Present ALL relevant information in a concise way\n- Do not use conversational format - directly output the relevant information\n- If information is unavailable, state that clearly\n</output>\n'.strip()

def get_ai_step_user_prompt(query: str, stats_summary: str, content: str) -> str:
    return f'<query>\n{query}\n</query>\n\n<content_stats>\n{stats_summary}\n</content_stats>\n\n<webpage_content>\n{content}\n</webpage_content>'