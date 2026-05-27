import asyncio
import gc
import inspect
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast
from urllib.parse import urlparse
if TYPE_CHECKING:
    from system.skills.views import Skill
from dotenv import load_dotenv
from system.agent.cloud_events import CreateAgentOutputFileEvent, CreateAgentSessionEvent, CreateAgentStepEvent, CreateAgentTaskEvent, UpdateAgentTaskEvent
from system.agent.message_manager.utils import save_conversation
from system.llm.base import BaseChatModel
from system.llm.exceptions import ModelProviderError, ModelRateLimitError
from system.llm.messages import BaseMessage, ContentPartImageParam, ContentPartTextParam, UserMessage
from system.tokens.service import TokenCost
load_dotenv()
from bubus import EventBus
from pydantic import BaseModel, ValidationError
from uuid_extensions import uuid7str
from system import Browser, BrowserProfile, BrowserSession
from system.agent.judge import construct_judge_messages
from system.agent.message_manager.service import MessageManager
from system.agent.prompts import SystemPrompt
from system.agent.views import ActionLoopDetector, ActionResult, AgentError, AgentHistory, AgentHistoryList, AgentOutput, AgentSettings, AgentState, AgentStepInfo, AgentStructuredOutput, BrowserStateHistory, DetectedVariable, JudgementResult, MessageCompactionSettings, PageFingerprint, PlanItem, StepMetadata
from system.agent.workflow import WorkflowRecorder, WorkflowTemplate, WorkflowExecutor
from system.browser.events import _get_timeout
from system.browser.session import DEFAULT_BROWSER_PROFILE
from system.browser.views import BrowserStateSummary
from system.config import CONFIG
from system.dom.views import DOMInteractedElement, MatchLevel
from system.filesystem.file_system import FileSystem
from system.observability import observe, observe_debug
from system.telemetry.service import ProductTelemetry
from system.telemetry.views import AgentTelemetryEvent
from system.tools.registry.views import ActionModel
from system.tools.service import Tools
from system.utils import URL_PATTERN, _log_pretty_path, check_latest_browser_use_version, get_browser_use_version, time_execution_async, time_execution_sync
logger = logging.getLogger(__name__)

def log_response(response: AgentOutput, registry=None, logger=None) -> None:
    if logger is None:
        logger = logging.getLogger(__name__)
    if response.current_state.thinking:
        logger.debug(f'💡 Thinking:\n{response.current_state.thinking}')
    eval_goal = response.current_state.evaluation_previous_goal
    if eval_goal:
        if 'success' in eval_goal.lower():
            emoji = '👍'
            logger.info(f'  \x1b[32m{emoji} Eval: {eval_goal}\x1b[0m')
        elif 'failure' in eval_goal.lower():
            emoji = '⚠️'
            logger.info(f'  \x1b[31m{emoji} Eval: {eval_goal}\x1b[0m')
        else:
            emoji = '❔'
            logger.info(f'  {emoji} Eval: {eval_goal}')
    if response.current_state.memory:
        logger.info(f'  🧠 Memory: {response.current_state.memory}')
    next_goal = response.current_state.next_goal
    if next_goal:
        logger.info(f'  \x1b[34m🎯 Next goal: {next_goal}\x1b[0m')
Context = TypeVar('Context')
AgentHookFunc = Callable[['Agent'], Awaitable[None]]

class Agent(Generic[Context, AgentStructuredOutput]):

    @time_execution_sync('--init')
    def __init__(self, task: str, llm: BaseChatModel | None=None, browser_profile: BrowserProfile | None=None, browser_session: BrowserSession | None=None, browser: Browser | None=None, tools: Tools[Context] | None=None, controller: Tools[Context] | None=None, skill_ids: list[str | Literal['*']] | None=None, skills: list[str | Literal['*']] | None=None, skill_service: Any | None=None, sensitive_data: dict[str, str | dict[str, str]] | None=None, initial_actions: list[dict[str, dict[str, Any]]] | None=None, register_new_step_callback: Callable[['BrowserStateSummary', 'AgentOutput', int], None] | Callable[['BrowserStateSummary', 'AgentOutput', int], Awaitable[None]] | None=None, register_done_callback: Callable[['AgentHistoryList'], Awaitable[None]] | Callable[['AgentHistoryList'], None] | None=None, register_external_agent_status_raise_error_callback: Callable[[], Awaitable[bool]] | None=None, register_should_stop_callback: Callable[[], Awaitable[bool]] | None=None, output_model_schema: type[AgentStructuredOutput] | None=None, extraction_schema: dict | None=None, use_vision: bool | Literal['auto']=True, save_conversation_path: str | Path | None=None, save_conversation_path_encoding: str | None='utf-8', max_failures: int=5, override_system_message: str | None=None, extend_system_message: str | None=None, generate_gif: bool | str=False, available_file_paths: list[str] | None=None, include_attributes: list[str] | None=None, max_actions_per_step: int=5, use_thinking: bool=True, flash_mode: bool=False, max_history_items: int | None=None, page_extraction_llm: BaseChatModel | None=None, fallback_llm: BaseChatModel | None=None, use_judge: bool=True, ground_truth: str | None=None, judge_llm: BaseChatModel | None=None, injected_agent_state: AgentState | None=None, source: str | None=None, file_system_path: str | None=None, task_id: str | None=None, calculate_cost: bool=False, display_files_in_done_text: bool=True, include_tool_call_examples: bool=False, vision_detail_level: Literal['auto', 'low', 'high']='auto', llm_timeout: int | None=None, step_timeout: int=180, directly_open_url: bool=True, include_recent_events: bool=False, sample_images: list[ContentPartTextParam | ContentPartImageParam] | None=None, final_response_after_failure: bool=True, enable_planning: bool=True, planning_replan_on_stall: int=3, planning_exploration_limit: int=5, loop_detection_window: int=20, loop_detection_enabled: bool=True, llm_screenshot_size: tuple[int, int] | None=None, message_compaction: MessageCompactionSettings | bool | None=True, max_clickable_elements_length: int=40000, _url_shortening_limit: int=25, frontend_event_callback: Callable[[dict], None] | None=None, workflow_template: WorkflowTemplate | None=None, **kwargs):
        if llm_screenshot_size is not None:
            if not isinstance(llm_screenshot_size, tuple) or len(llm_screenshot_size) != 2:
                raise ValueError('llm_screenshot_size must be a tuple of (width, height)')
            width, height = llm_screenshot_size
            if not isinstance(width, int) or not isinstance(height, int):
                raise ValueError('llm_screenshot_size dimensions must be integers')
            if width < 100 or height < 100:
                raise ValueError('llm_screenshot_size dimensions must be at least 100 pixels')
            self.logger.info(f'🖼️  LLM screenshot resizing enabled: {width}x{height}')
        if llm is None:
            default_llm_name = CONFIG.DEFAULT_LLM
            if default_llm_name:
                from system.llm.models import get_llm_by_name
                llm = get_llm_by_name(default_llm_name)
            else:
                from system import ChatBrowserUse
                llm = ChatBrowserUse()
        if llm.provider == 'browser-use':
            flash_mode = True
        if flash_mode:
            enable_planning = False
        if llm_screenshot_size is None:
            model_name = getattr(llm, 'model', '')
            if isinstance(model_name, str) and model_name.startswith('claude-sonnet'):
                llm_screenshot_size = (1400, 850)
                logger.info('🖼️  Auto-configured LLM screenshot size for Claude Sonnet: 1400x850')
        if page_extraction_llm is None:
            page_extraction_llm = llm
        if judge_llm is None:
            judge_llm = llm
        if available_file_paths is None:
            available_file_paths = []
        if llm_timeout is None:

            def _get_model_timeout(llm_model: BaseChatModel) -> int:
                model_name = getattr(llm_model, 'model', '').lower()
                if 'gemini' in model_name:
                    if '3-pro' in model_name:
                        return 90
                    return 75
                elif 'groq' in model_name:
                    return 30
                elif 'o3' in model_name or 'claude' in model_name or 'sonnet' in model_name or ('deepseek' in model_name):
                    return 90
                else:
                    return 75
            llm_timeout = _get_model_timeout(llm)
        self.id = task_id or uuid7str()
        self.task_id: str = self.id
        self.session_id: str = uuid7str()
        base_profile = browser_profile or DEFAULT_BROWSER_PROFILE
        if base_profile is DEFAULT_BROWSER_PROFILE:
            base_profile = base_profile.model_copy()
        browser_profile = base_profile
        if browser and browser_session:
            raise ValueError('Cannot specify both "browser" and "browser_session" parameters. Use "browser" for the cleaner API.')
        browser_session = browser or browser_session
        self.injected_browser_session = browser_session is not None
        if browser_session is not None:
            pass
        self.browser_session = browser_session or BrowserSession(browser_profile=browser_profile, id=uuid7str()[:-4] + self.id[-4:])
        self._demo_mode_enabled: bool = False
        self.available_file_paths = available_file_paths
        if tools is not None:
            self.tools = tools
        elif controller is not None:
            self.tools = controller
        else:
            exclude_actions = ['screenshot'] if use_vision != 'auto' else []
            self.tools = Tools(exclude_actions=exclude_actions, display_files_in_done_text=display_files_in_done_text)
        if use_vision != 'auto':
            self.tools.exclude_action('screenshot')
        model_name = getattr(llm, 'model', '').lower()
        supports_coordinate_clicking = any((pattern in model_name for pattern in ['claude-sonnet-4', 'claude-opus-4', 'gemini-3-pro', 'browser-use/']))
        if supports_coordinate_clicking:
            self.tools.set_coordinate_clicking(True)
        if skills and skill_ids:
            raise ValueError('Cannot specify both "skills" and "skill_ids" parameters. Use "skills" for the cleaner API.')
        skill_ids = skills or skill_ids
        self.skill_service = None
        self._skills_registered = False
        if skill_service is not None:
            self.skill_service = skill_service
        elif skill_ids:
            from system.skills import SkillService
            self.skill_service = SkillService(skill_ids=skill_ids)
        tools_output_model = self.tools.get_output_model()
        if output_model_schema is not None and tools_output_model is not None:
            if output_model_schema is not tools_output_model:
                logger.warning(f'output_model_schema ({output_model_schema.__name__}) differs from Tools output_model ({tools_output_model.__name__}). Using Agent output_model_schema.')
        elif output_model_schema is None and tools_output_model is not None:
            output_model_schema = cast(type[AgentStructuredOutput], tools_output_model)
        self.output_model_schema = output_model_schema
        if self.output_model_schema is not None:
            self.tools.use_structured_output_action(self.output_model_schema)
        self.extraction_schema = extraction_schema
        if self.extraction_schema is None and self.output_model_schema is not None:
            self.extraction_schema = self.output_model_schema.model_json_schema()
        self.task = self._enhance_task_with_schema(task, output_model_schema)
        self.llm = llm
        self.judge_llm = judge_llm
        self._fallback_llm: BaseChatModel | None = fallback_llm
        self._using_fallback_llm: bool = False
        self._original_llm: BaseChatModel = llm
        self.directly_open_url = directly_open_url
        self.include_recent_events = include_recent_events
        self._url_shortening_limit = _url_shortening_limit
        self.sensitive_data = sensitive_data
        self.sample_images = sample_images
        if isinstance(message_compaction, bool):
            message_compaction = MessageCompactionSettings(enabled=message_compaction)
        self.settings = AgentSettings(use_vision=use_vision, vision_detail_level=vision_detail_level, save_conversation_path=save_conversation_path, save_conversation_path_encoding=save_conversation_path_encoding, max_failures=max_failures, override_system_message=override_system_message, extend_system_message=extend_system_message, generate_gif=generate_gif, include_attributes=include_attributes, max_actions_per_step=max_actions_per_step, use_thinking=use_thinking, flash_mode=flash_mode, max_history_items=max_history_items, page_extraction_llm=page_extraction_llm, calculate_cost=calculate_cost, include_tool_call_examples=include_tool_call_examples, llm_timeout=llm_timeout, step_timeout=step_timeout, final_response_after_failure=final_response_after_failure, use_judge=use_judge, ground_truth=ground_truth, enable_planning=enable_planning, planning_replan_on_stall=planning_replan_on_stall, planning_exploration_limit=planning_exploration_limit, loop_detection_window=loop_detection_window, loop_detection_enabled=loop_detection_enabled, message_compaction=message_compaction, max_clickable_elements_length=max_clickable_elements_length)
        self.token_cost_service = TokenCost(include_cost=calculate_cost)
        self.token_cost_service.register_llm(llm)
        self.token_cost_service.register_llm(page_extraction_llm)
        self.token_cost_service.register_llm(judge_llm)
        if self.settings.message_compaction and self.settings.message_compaction.compaction_llm:
            self.token_cost_service.register_llm(self.settings.message_compaction.compaction_llm)
        self.state = injected_agent_state or AgentState()
        self.state.loop_detector.window_size = self.settings.loop_detection_window
        self.history = AgentHistoryList(history=[], usage=None)
        import time
        timestamp = int(time.time())
        base_tmp = Path(tempfile.gettempdir())
        self.agent_directory = base_tmp / f'system_agent_{self.id}_{timestamp}'
        self._set_file_system(file_system_path)
        self._set_screenshot_service()
        self.workflow_executor = WorkflowExecutor(workflow_template) if workflow_template else None
        self._frontend_event_callback = frontend_event_callback
        self._setup_action_models()
        self._set_system_version_and_source(source)
        initial_url = None
        if self.directly_open_url and (not self.state.follow_up_task) and (not initial_actions):
            initial_url = self._extract_start_url(self.task)
            if initial_url:
                self.logger.info(f'🔗 Found URL in task: {initial_url}, adding as initial action...')
                initial_actions = [{'navigate': {'url': initial_url, 'new_tab': False}}]
        self.initial_url = initial_url
        self.initial_actions = self._convert_initial_actions(initial_actions) if initial_actions else None
        self._verify_and_setup_llm()
        if 'deepseek' in self.llm.model.lower():
            self.logger.warning('⚠️ DeepSeek models do not support use_vision=True yet. Setting use_vision=False for now...')
            self.settings.use_vision = False
        model_lower = self.llm.model.lower()
        if 'grok-3' in model_lower or 'grok-code' in model_lower:
            self.logger.warning('⚠️ This XAI model does not support use_vision=True yet. Setting use_vision=False for now...')
            self.settings.use_vision = False
        logger.debug(f"{(' +vision' if self.settings.use_vision else '')} extraction_model={(self.settings.page_extraction_llm.model if self.settings.page_extraction_llm else 'Unknown')}{(' +file_system' if self.file_system else '')}")
        self.browser_session.llm_screenshot_size = llm_screenshot_size
        is_anthropic = False
        is_system_model = 'browser-use/' in self.llm.model.lower()
        self._message_manager = MessageManager(task=self.task, system_message=SystemPrompt(max_actions_per_step=self.settings.max_actions_per_step, override_system_message=override_system_message, extend_system_message=extend_system_message, use_thinking=self.settings.use_thinking, flash_mode=self.settings.flash_mode, is_anthropic=is_anthropic, is_system_model=is_system_model, model_name=self.llm.model).get_system_message(), file_system=self.file_system, state=self.state.message_manager_state, use_thinking=self.settings.use_thinking, include_attributes=self.settings.include_attributes, sensitive_data=sensitive_data, max_history_items=self.settings.max_history_items, vision_detail_level=self.settings.vision_detail_level, include_tool_call_examples=self.settings.include_tool_call_examples, include_recent_events=self.include_recent_events, sample_images=self.sample_images, llm_screenshot_size=llm_screenshot_size, max_clickable_elements_length=self.settings.max_clickable_elements_length)
        if self.sensitive_data:
            has_domain_specific_credentials = any((isinstance(v, dict) for v in self.sensitive_data.values()))
            if not self.browser_profile.allowed_domains:
                self.logger.warning('⚠️ Agent(sensitive_data=••••••••) was provided but Browser(allowed_domains=[...]) is not locked down! ⚠️\n          ☠️ If the agent visits a malicious website and encounters a prompt-injection attack, your sensitive_data may be exposed!\n\n   \n')
            elif has_domain_specific_credentials:
                domain_patterns = [k for k, v in self.sensitive_data.items() if isinstance(v, dict)]
                for domain_pattern in domain_patterns:
                    is_allowed = False
                    for allowed_domain in self.browser_profile.allowed_domains:
                        if domain_pattern == allowed_domain or allowed_domain == '*':
                            is_allowed = True
                            break
                        pattern_domain = domain_pattern.split('://')[-1] if '://' in domain_pattern else domain_pattern
                        allowed_domain_part = allowed_domain.split('://')[-1] if '://' in allowed_domain else allowed_domain
                        if pattern_domain == allowed_domain_part or (allowed_domain_part.startswith('*.') and (pattern_domain == allowed_domain_part[2:] or pattern_domain.endswith('.' + allowed_domain_part[2:]))):
                            is_allowed = True
                            break
                    if not is_allowed:
                        self.logger.warning(f'⚠️ Domain pattern "{domain_pattern}" in sensitive_data is not covered by any pattern in allowed_domains={self.browser_profile.allowed_domains}\n   This may be a security risk as credentials could be used on unintended domains.')
        self.register_new_step_callback = register_new_step_callback
        self.register_done_callback = register_done_callback
        self.register_should_stop_callback = register_should_stop_callback
        self.register_external_agent_status_raise_error_callback = register_external_agent_status_raise_error_callback
        self.telemetry = ProductTelemetry()
        self.eventbus = EventBus(name=f'Agent_{str(self.id)[-4:]}')
        if self.settings.save_conversation_path:
            self.settings.save_conversation_path = Path(self.settings.save_conversation_path).expanduser().resolve()
            self.logger.info(f'💬 Saving conversation to {_log_pretty_path(self.settings.save_conversation_path)}')
        assert self.browser_session is not None, 'BrowserSession is not set up'
        self.has_downloads_path = self.browser_session.browser_profile.downloads_path is not None
        if self.has_downloads_path:
            self._last_known_downloads: list[str] = []
            self.logger.debug('📁 Initialized download tracking for agent')
        self._external_pause_event = asyncio.Event()
        self._external_pause_event.set()

    def _enhance_task_with_schema(self, task: str, output_model_schema: type[AgentStructuredOutput] | None) -> str:
        if output_model_schema is None:
            return task
        try:
            schema = output_model_schema.model_json_schema()
            import json
            schema_json = json.dumps(schema, indent=2)
            enhancement = f'\nExpected output format: {output_model_schema.__name__}\n{schema_json}'
            return task + enhancement
        except Exception as e:
            self.logger.debug(f'Could not parse output schema: {e}')
        return task

    @property
    def logger(self) -> logging.Logger:
        _task_id = task_id[-4:] if (task_id := getattr(self, 'task_id', None)) else '----'
        _browser_session_id = browser_session.id[-4:] if (browser_session := getattr(self, 'browser_session', None)) else '----'
        _current_target_id = browser_session.agent_focus_target_id[-2:] if (browser_session := getattr(self, 'browser_session', None)) and browser_session.agent_focus_target_id else '--'
        return logging.getLogger(f'system.Agent🅰 {_task_id} ⇢ 🅑 {_browser_session_id} 🅣 {_current_target_id}')

    @property
    def browser_profile(self) -> BrowserProfile:
        assert self.browser_session is not None, 'BrowserSession is not set up'
        return self.browser_session.browser_profile

    @property
    def is_using_fallback_llm(self) -> bool:
        return self._using_fallback_llm

    @property
    def current_llm_model(self) -> str:
        return self.llm.model if hasattr(self.llm, 'model') else 'unknown'

    async def _check_and_update_downloads(self, context: str='') -> None:
        if not self.has_downloads_path:
            return
        assert self.browser_session is not None, 'BrowserSession is not set up'
        try:
            current_downloads = self.browser_session.downloaded_files
            if current_downloads != self._last_known_downloads:
                self._update_available_file_paths(current_downloads)
                self._last_known_downloads = current_downloads
                if context:
                    self.logger.debug(f'📁 {context}: Updated available files')
        except Exception as e:
            error_context = f' {context}' if context else ''
            self.logger.debug(f'📁 Failed to check for downloads{error_context}: {type(e).__name__}: {e}')

    def _update_available_file_paths(self, downloads: list[str]) -> None:
        if not self.has_downloads_path:
            return
        current_files = set(self.available_file_paths or [])
        new_files = set(downloads) - current_files
        if new_files:
            self.available_file_paths = list(current_files | new_files)
            self.logger.info(f'📁 Added {len(new_files)} downloaded files to available_file_paths (total: {len(self.available_file_paths)} files)')
            for file_path in new_files:
                self.logger.info(f'📄 New file available: {file_path}')
        else:
            self.logger.debug(f'📁 No new downloads detected (tracking {len(current_files)} files)')

    def _set_file_system(self, file_system_path: str | None=None) -> None:
        if self.state.file_system_state and file_system_path:
            raise ValueError('Cannot provide both file_system_state (from agent state) and file_system_path. Either restore from existing state or create new file system at specified path, not both.')
        if self.state.file_system_state:
            try:
                self.file_system = FileSystem.from_state(self.state.file_system_state)
                self.file_system_path = str(self.file_system.base_dir)
                self.logger.debug(f'💾 File system restored from state to: {self.file_system_path}')
                return
            except Exception as e:
                self.logger.error(f'💾 Failed to restore file system from state: {e}')
                raise e
        try:
            if file_system_path:
                self.file_system = FileSystem(file_system_path)
                self.file_system_path = file_system_path
            else:
                self.file_system = FileSystem(self.agent_directory)
                self.file_system_path = str(self.agent_directory)
        except Exception as e:
            self.logger.error(f'💾 Failed to initialize file system: {e}.')
            raise e
        self.state.file_system_state = self.file_system.get_state()
        self.logger.debug(f'💾 File system path: {self.file_system_path}')

    def _set_screenshot_service(self) -> None:
        try:
            from system.screenshots.service import ScreenshotService
            self.screenshot_service = ScreenshotService(self.agent_directory)
            self.logger.debug(f'📸 Screenshot service initialized in: {self.agent_directory}/screenshots')
        except Exception as e:
            self.logger.error(f'📸 Failed to initialize screenshot service: {e}.')
            raise e

    def save_file_system_state(self) -> None:
        if self.file_system:
            self.state.file_system_state = self.file_system.get_state()
        else:
            self.logger.error('💾 File system is not set up. Cannot save state.')
            raise ValueError('File system is not set up. Cannot save state.')

    def _set_system_version_and_source(self, source_override: str | None=None) -> None:
        version = get_browser_use_version()
        try:
            package_root = Path(__file__).parent.parent.parent
            repo_files = ['.git', 'README.md', 'docs', 'examples']
            if all((Path(package_root / file).exists() for file in repo_files)):
                source = 'git'
            else:
                source = 'pip'
        except Exception as e:
            self.logger.debug(f'Error determining source: {e}')
            source = 'unknown'
        if source_override is not None:
            source = source_override
        self.version = version
        self.source = source

    def _setup_action_models(self) -> None:
        self.ActionModel = self.tools.registry.create_action_model()
        if self.settings.flash_mode:
            self.AgentOutput = AgentOutput.type_with_custom_actions_flash_mode(self.ActionModel)
        elif self.settings.use_thinking:
            self.AgentOutput = AgentOutput.type_with_custom_actions(self.ActionModel)
        else:
            self.AgentOutput = AgentOutput.type_with_custom_actions_no_thinking(self.ActionModel)
        self.DoneActionModel = self.tools.registry.create_action_model(include_actions=['done'])
        if self.settings.flash_mode:
            self.DoneAgentOutput = AgentOutput.type_with_custom_actions_flash_mode(self.DoneActionModel)
        elif self.settings.use_thinking:
            self.DoneAgentOutput = AgentOutput.type_with_custom_actions(self.DoneActionModel)
        else:
            self.DoneAgentOutput = AgentOutput.type_with_custom_actions_no_thinking(self.DoneActionModel)

    def _get_skill_slug(self, skill: 'Skill', all_skills: list['Skill']) -> str:
        import re
        slug = re.sub('[^\\w\\s]', '', skill.title.lower())
        slug = re.sub('[\\s\\-]+', '_', slug)
        slug = slug.strip('_')
        same_slug_count = sum((1 for s in all_skills if re.sub('[\\s\\-]+', '_', re.sub('[^\\w\\s]', '', s.title.lower()).strip('_')) == slug))
        if same_slug_count > 1:
            return f'{slug}_{skill.id[:4]}'
        else:
            return slug

    async def _register_skills_as_actions(self) -> None:
        if not self.skill_service or self._skills_registered:
            return
        self.logger.info('🔧 Registering skill actions...')
        skills = await self.skill_service.get_all_skills()
        if not skills:
            self.logger.warning('No skills loaded from SkillService')
            return
        for skill in skills:
            slug = self._get_skill_slug(skill, skills)
            param_model = skill.parameters_pydantic(exclude_cookies=True)
            description = f'{skill.description} (Skill: "{skill.title}")'

            def make_skill_handler(skill_id: str):

                async def skill_handler(params: BaseModel) -> ActionResult:
                    assert self.skill_service is not None, 'SkillService not initialized'
                    if isinstance(params, BaseModel):
                        skill_params = params.model_dump()
                    elif isinstance(params, dict):
                        skill_params = params
                    else:
                        return ActionResult(extracted_content=None, error=f'Invalid parameters type: {type(params)}')
                    _cookies = await self.browser_session.cookies()
                    try:
                        result = await self.skill_service.execute_skill(skill_id=skill_id, parameters=skill_params, cookies=_cookies)
                        if result.success:
                            return ActionResult(extracted_content=str(result.result) if result.result else None, error=None)
                        else:
                            return ActionResult(extracted_content=None, error=result.error or 'Skill execution failed')
                    except Exception as e:
                        if type(e).__name__ == 'MissingCookieException':
                            cookie_name = getattr(e, 'cookie_name', 'unknown')
                            cookie_description = getattr(e, 'cookie_description', str(e))
                            error_msg = f'Missing cookies ({cookie_name}): {cookie_description}'
                            return ActionResult(extracted_content=None, error=error_msg)
                        return ActionResult(extracted_content=None, error=f'Skill execution error: {type(e).__name__}: {e}')
                return skill_handler
            handler = make_skill_handler(skill.id)
            handler.__name__ = slug
            self.tools.registry.action(description=description, param_model=param_model)(handler)
        self._skills_registered = True
        self._setup_action_models()
        if self.initial_actions:
            initial_actions_dict = []
            for action in self.initial_actions:
                action_dump = action.model_dump(exclude_unset=True)
                initial_actions_dict.append(action_dump)
            self.initial_actions = self._convert_initial_actions(initial_actions_dict)
        self.logger.info(f'✓ Registered {len(skills)} skill actions')

    async def _get_unavailable_skills_info(self) -> str:
        if not self.skill_service:
            return ''
        try:
            skills = await self.skill_service.get_all_skills()
            if not skills:
                return ''
            current_cookies = await self.browser_session.cookies()
            cookie_dict = {cookie['name']: cookie['value'] for cookie in current_cookies}
            unavailable_skills: list[dict[str, Any]] = []
            for skill in skills:
                cookie_params = [p for p in skill.parameters if p.type == 'cookie']
                if not cookie_params:
                    continue
                missing_cookies: list[dict[str, str]] = []
                for cookie_param in cookie_params:
                    is_required = cookie_param.required if cookie_param.required is not None else True
                    if is_required and cookie_param.name not in cookie_dict:
                        missing_cookies.append({'name': cookie_param.name, 'description': cookie_param.description or 'No description provided'})
                if missing_cookies:
                    unavailable_skills.append({'id': skill.id, 'title': skill.title, 'description': skill.description, 'missing_cookies': missing_cookies})
            if not unavailable_skills:
                return ''
            lines = ['Unavailable Skills (missing required cookies):']
            for skill_info in unavailable_skills:
                skill_obj = next((s for s in skills if s.id == skill_info['id']), None)
                slug = self._get_skill_slug(skill_obj, skills) if skill_obj else skill_info['title']
                title = skill_info['title']
                lines.append(f'\n  • {slug} ("{title}")')
                lines.append(f"    Description: {skill_info['description']}")
                lines.append('    Missing cookies:')
                for cookie in skill_info['missing_cookies']:
                    lines.append(f"      - {cookie['name']}: {cookie['description']}")
            return '\n'.join(lines)
        except Exception as e:
            self.logger.error(f'Error getting unavailable skills info: {type(e).__name__}: {e}')
            return ''

    def add_new_task(self, new_task: str) -> None:
        self.task = new_task
        self._message_manager.add_new_task(new_task)
        self.state.follow_up_task = True
        self.state.stopped = False
        self.state.paused = False
        agent_id_suffix = str(self.id)[-4:].replace('-', '_')
        if agent_id_suffix and agent_id_suffix[0].isdigit():
            agent_id_suffix = 'a' + agent_id_suffix
        self.eventbus = EventBus(name=f'Agent_{agent_id_suffix}')

    async def _check_stop_or_pause(self) -> None:
        if self.register_should_stop_callback:
            if await self.register_should_stop_callback():
                self.logger.info('External callback requested stop')
                self.state.stopped = True
                raise InterruptedError
        if self.register_external_agent_status_raise_error_callback:
            if await self.register_external_agent_status_raise_error_callback():
                raise InterruptedError
        if self.state.stopped:
            raise InterruptedError
        if self.state.paused:
            raise InterruptedError

    @observe(name='agent.step', ignore_output=True, ignore_input=True)
    @time_execution_async('--step')
    async def step(self, step_info: AgentStepInfo | None=None) -> None:
        self.step_start_time = time.time()
        browser_state_summary = None
        try:
            if self.browser_session:
                try:
                    captcha_wait = await self.browser_session.wait_if_captcha_solving()
                    if captcha_wait and captcha_wait.waited:
                        self.step_start_time = time.time()
                        duration_s = captcha_wait.duration_ms / 1000
                        outcome = captcha_wait.result
                        msg = f'Waited {duration_s:.1f}s for {captcha_wait.vendor} CAPTCHA to be solved. Result: {outcome}.'
                        self.logger.info(f'🔒 {msg}')
                        captcha_result = ActionResult(long_term_memory=msg)
                        if self.state.last_result:
                            self.state.last_result.append(captcha_result)
                        else:
                            self.state.last_result = [captcha_result]
                except Exception as e:
                    self.logger.warning(f'Phase 0 captcha wait failed (non-fatal): {e}')
            browser_state_summary = await self._prepare_context(step_info)
            await self._get_next_action(browser_state_summary)
            await self._execute_actions()
            await self._post_process()
        except Exception as e:
            await self._handle_step_error(e)
        finally:
            await self._finalize(browser_state_summary)

    async def _prepare_context(self, step_info: AgentStepInfo | None=None) -> BrowserStateSummary:
        assert self.browser_session is not None, 'BrowserSession is not set up'
        self.logger.debug(f'🌐 Step {self.state.n_steps}: Getting browser state...')
        self.logger.debug('📸 Requesting browser state with include_screenshot=True')
        browser_state_summary = await self.browser_session.get_browser_state_summary(include_screenshot=True, include_recent_events=self.include_recent_events)
        if browser_state_summary.screenshot:
            self.logger.debug(f'📸 Got browser state WITH screenshot, length: {len(browser_state_summary.screenshot)}')
        else:
            self.logger.debug('📸 Got browser state WITHOUT screenshot')
        await self._check_and_update_downloads(f'Step {self.state.n_steps}: after getting browser state')
        self._log_step_context(browser_state_summary)
        await self._check_stop_or_pause()
        self.logger.debug(f'📝 Step {self.state.n_steps}: Updating action models...')
        await self._update_action_models_for_page(browser_state_summary.url)
        page_filtered_actions = self.tools.registry.get_prompt_description(browser_state_summary.url)
        self.logger.debug(f'💬 Step {self.state.n_steps}: Creating state messages for context...')
        unavailable_skills_info = None
        if self.skill_service is not None:
            unavailable_skills_info = await self._get_unavailable_skills_info()
        plan_description = self._render_plan_description()
        self._message_manager.prepare_step_state(browser_state_summary=browser_state_summary, model_output=self.state.last_model_output, result=self.state.last_result, step_info=step_info, sensitive_data=self.sensitive_data)
        await self._maybe_compact_messages(step_info)
        self._message_manager.create_state_messages(browser_state_summary=browser_state_summary, model_output=self.state.last_model_output, result=self.state.last_result, step_info=step_info, use_vision=self.settings.use_vision, page_filtered_actions=page_filtered_actions if page_filtered_actions else None, sensitive_data=self.sensitive_data, available_file_paths=self.available_file_paths, unavailable_skills_info=unavailable_skills_info, plan_description=plan_description, skip_state_update=True)
        await self._inject_budget_warning(step_info)
        self._inject_replan_nudge()
        self._inject_exploration_nudge()
        self._update_loop_detector_page_state(browser_state_summary)
        self._inject_loop_detection_nudge()
        await self._force_done_after_last_step(step_info)
        await self._force_done_after_failure()
        return browser_state_summary

    async def _maybe_compact_messages(self, step_info: AgentStepInfo | None=None) -> None:
        settings = self.settings.message_compaction
        if not settings or not settings.enabled:
            return
        compaction_llm = settings.compaction_llm or self.settings.page_extraction_llm or self.llm
        await self._message_manager.maybe_compact_messages(llm=compaction_llm, settings=settings, step_info=step_info)

    @observe_debug(ignore_input=True, name='get_next_action')
    async def _get_next_action(self, browser_state_summary: BrowserStateSummary) -> None:
        input_messages = self._message_manager.get_messages()
        if self.workflow_executor and self.workflow_executor.mode == 'macro':
            try:
                self.logger.debug(f'🤖 Step {self.state.n_steps}: Executing macro deterministically via WorkflowExecutor...')
                macro_output = self.workflow_executor.get_next_action(browser_state_summary.dom_state, self.AgentOutput)
                if macro_output is not None:
                    self.state.last_model_output = macro_output
                    await self._handle_post_llm_processing(browser_state_summary, input_messages)
                    return
            except Exception as e:
                self.logger.warning(f'⚠️ [WorkflowExecutor] Engine crashed: {e}. Falling back to default LLM logic.')
        self.logger.debug(f'🤖 Step {self.state.n_steps}: Calling LLM with {len(input_messages)} messages (model: {self.llm.model})...')
        try:
            model_output = await asyncio.wait_for(self._get_model_output_with_retry(input_messages), timeout=self.settings.llm_timeout)
        except TimeoutError:

            @observe(name='_llm_call_timed_out_with_input')
            async def _log_model_input_to_lmnr(input_messages: list[BaseMessage]) -> None:
                pass
            await _log_model_input_to_lmnr(input_messages)
            raise TimeoutError(f'LLM call timed out after {self.settings.llm_timeout} seconds. Keep your thinking and output short.')
        self.state.last_model_output = model_output
        await self._check_stop_or_pause()
        await self._handle_post_llm_processing(browser_state_summary, input_messages)
        await self._check_stop_or_pause()

    async def _execute_actions(self) -> None:
        if self.state.last_model_output is None:
            raise ValueError('No model output to execute actions from')
        result = await self.multi_act(self.state.last_model_output.action)
        self.state.last_result = result

    async def _post_process(self) -> None:
        assert self.browser_session is not None, 'BrowserSession is not set up'
        await self._check_and_update_downloads('after executing actions')
        if self.state.last_model_output is not None:
            self._update_plan_from_model_output(self.state.last_model_output)
        self._update_loop_detector_actions()
        if self.state.last_result and len(self.state.last_result) == 1 and self.state.last_result[-1].error:
            self.state.consecutive_failures += 1
            self.logger.debug(f'🔄 Step {self.state.n_steps}: Consecutive failures: {self.state.consecutive_failures}')
            return
        if self.state.consecutive_failures > 0:
            self.state.consecutive_failures = 0
            self.logger.debug(f'🔄 Step {self.state.n_steps}: Consecutive failures reset to: {self.state.consecutive_failures}')
        if self.state.last_result and len(self.state.last_result) > 0 and self.state.last_result[-1].is_done:
            success = self.state.last_result[-1].success
            if success:
                self.logger.info(f'\n📄 \x1b[32m Final Result:\x1b[0m \n{self.state.last_result[-1].extracted_content}\n\n')
            else:
                self.logger.info(f'\n📄 \x1b[31m Final Result:\x1b[0m \n{self.state.last_result[-1].extracted_content}\n\n')
            if self.state.last_result[-1].attachments:
                total_attachments = len(self.state.last_result[-1].attachments)
                for i, file_path in enumerate(self.state.last_result[-1].attachments):
                    self.logger.info(f"👉 Attachment {(i + 1 if total_attachments > 1 else '')}: {file_path}")

    async def _handle_step_error(self, error: Exception) -> None:
        if isinstance(error, InterruptedError):
            error_msg = 'The agent was interrupted mid-step' + (f' - {str(error)}' if str(error) else '')
            self.logger.warning(f'{error_msg}')
            return
        if self._is_connection_like_error(error):
            if self.browser_session.is_reconnecting:
                wait_timeout = self.browser_session.RECONNECT_WAIT_TIMEOUT
                self.logger.warning(f'🔄 Connection error during reconnection, waiting up to {wait_timeout}s for reconnect: {error}')
                try:
                    await asyncio.wait_for(self.browser_session._reconnect_event.wait(), timeout=wait_timeout)
                except TimeoutError:
                    pass
                if self.browser_session.is_cdp_connected:
                    self.logger.info('🔄 Reconnection succeeded, retrying step...')
                    self.state.last_result = [ActionResult(error=f'Connection lost and recovered: {error}')]
                    return
            if self._is_browser_closed_error(error):
                self.logger.warning(f'🛑 Browser closed or disconnected: {error}')
                self.state.stopped = True
                self._external_pause_event.set()
                return
        include_trace = self.logger.isEnabledFor(logging.DEBUG)
        error_msg = AgentError.format_error(error, include_trace=include_trace)
        max_total_failures = self.settings.max_failures + int(self.settings.final_response_after_failure)
        prefix = f'❌ Result failed {self.state.consecutive_failures + 1}/{max_total_failures} times: '
        self.state.consecutive_failures += 1
        is_final_failure = self.state.consecutive_failures >= max_total_failures
        log_level = logging.ERROR if is_final_failure else logging.WARNING
        if 'Could not parse response' in error_msg or 'tool_use_failed' in error_msg:
            self.logger.log(log_level, f'Model: {self.llm.model} failed')
            self.logger.log(log_level, f'{prefix}{error_msg}')
        else:
            self.logger.log(log_level, f'{prefix}{error_msg}')
        await self._demo_mode_log(f'Step error: {error_msg}', 'error', {'step': self.state.n_steps})
        self.state.last_result = [ActionResult(error=error_msg)]
        return None

    def _is_connection_like_error(self, error: Exception) -> bool:
        error_str = str(error).lower()
        return isinstance(error, ConnectionError) or 'websocket connection closed' in error_str or 'connection closed' in error_str or ('browser has been closed' in error_str) or ('browser closed' in error_str) or ('no browser' in error_str)

    def _is_browser_closed_error(self, error: Exception) -> bool:
        if self.browser_session.is_reconnecting:
            return False
        error_str = str(error).lower()
        is_connection_error = isinstance(error, ConnectionError) or 'websocket connection closed' in error_str or 'connection closed' in error_str or ('browser has been closed' in error_str) or ('browser closed' in error_str) or ('no browser' in error_str)
        return is_connection_error and self.browser_session._cdp_client_root is None

    async def _finalize(self, browser_state_summary: BrowserStateSummary | None) -> None:
        step_end_time = time.time()
        if not self.state.last_result:
            return
        if browser_state_summary:
            step_interval = None
            if len(self.history.history) > 0:
                last_history_item = self.history.history[-1]
                if last_history_item.metadata:
                    previous_end_time = last_history_item.metadata.step_end_time
                    previous_start_time = last_history_item.metadata.step_start_time
                    step_interval = max(0, previous_end_time - previous_start_time)
            metadata = StepMetadata(step_number=self.state.n_steps, step_start_time=self.step_start_time, step_end_time=step_end_time, step_interval=step_interval)
            await self._make_history_item(self.state.last_model_output, browser_state_summary, self.state.last_result, metadata, state_message=self._message_manager.last_state_message_text)
        summary_message = self._log_step_completion_summary(self.step_start_time, self.state.last_result)
        if summary_message:
            await self._demo_mode_log(summary_message, 'info', {'step': self.state.n_steps})
        self.save_file_system_state()
        if browser_state_summary and self.state.last_model_output:
            actions_data = []
            if self.state.last_model_output.action:
                for action in self.state.last_model_output.action:
                    action_dict = action.model_dump() if hasattr(action, 'model_dump') else {}
                    actions_data.append(action_dict)
            step_event = CreateAgentStepEvent.from_agent_step(self, self.state.last_model_output, self.state.last_result, actions_data, browser_state_summary)
            self.eventbus.dispatch(step_event)
        try:
            current_usage = await self.token_cost_service.get_usage_summary()
            self._emit_mock_frontend_event({'event_type': 'telemetry_billing', 'model': self.llm.model if hasattr(self.llm, 'model') else 'unknown', 'tokens': {'prompt': current_usage.total_prompt_tokens, 'completion': current_usage.total_completion_tokens, 'cached': current_usage.total_prompt_cached_tokens}, 'step': self.state.n_steps, 'status': 'success'})
        except Exception as e:
            self.logger.debug(f'Failed to emit billing event: {e}')
        self.state.n_steps += 1

    def _emit_mock_frontend_event(self, data: dict) -> None:
        if getattr(self, '_frontend_event_callback', None):
            try:
                self._frontend_event_callback(data)
            except Exception as e:
                self.logger.warning(f'Frontend event callback failed: {e}')
            return
        stream_path_str = getattr(CONFIG, 'MIRA_MOCK_FRONTEND_STREAM', None) or os.getenv('MIRA_MOCK_FRONTEND_STREAM')
        if stream_path_str:
            stream_file = Path(stream_path_str).expanduser()
        else:
            stream_file = Path(tempfile.gettempdir()) / 'mock_frontend_stream.jsonl'
        stream_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(stream_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(data) + '\n')
        except Exception as e:
            self.logger.warning(f'Mock SSE Stream write failed: {e}')

    def _update_plan_from_model_output(self, model_output: AgentOutput) -> None:
        if not self.settings.enable_planning:
            return
        if model_output.plan_update is not None:
            self.state.plan = [PlanItem(text=step_text) for step_text in model_output.plan_update]
            self.state.current_plan_item_index = 0
            self.state.plan_generation_step = self.state.n_steps
            if self.state.plan:
                self.state.plan[0].status = 'current'
            self.logger.info(f"📋 Plan {('updated' if self.state.plan_generation_step else 'created')} with {len(self.state.plan)} steps")
            return
        if model_output.current_plan_item is not None and self.state.plan is not None:
            new_idx = model_output.current_plan_item
            new_idx = max(0, min(new_idx, len(self.state.plan) - 1))
            old_idx = self.state.current_plan_item_index
            for i in range(old_idx, new_idx):
                if i < len(self.state.plan) and self.state.plan[i].status in ('current', 'pending'):
                    self.state.plan[i].status = 'done'
            if new_idx < len(self.state.plan):
                self.state.plan[new_idx].status = 'current'
            self.state.current_plan_item_index = new_idx

    def _render_plan_description(self) -> str | None:
        if not self.settings.enable_planning or self.state.plan is None:
            return None
        markers = {'done': '[x]', 'current': '[>]', 'pending': '[ ]', 'skipped': '[-]'}
        lines = []
        for i, step in enumerate(self.state.plan):
            marker = markers.get(step.status, '[ ]')
            lines.append(f'{marker} {i}: {step.text}')
        return '\n'.join(lines)

    def _inject_replan_nudge(self) -> None:
        if not self.settings.enable_planning or self.state.plan is None:
            return
        if self.settings.planning_replan_on_stall <= 0:
            return
        if self.state.consecutive_failures >= self.settings.planning_replan_on_stall:
            msg = f'REPLAN SUGGESTED: You have failed {self.state.consecutive_failures} consecutive times. Your current plan may need revision. Output a new `plan_update` with revised steps to recover.'
            self.logger.info(f'📋 Replan nudge injected after {self.state.consecutive_failures} consecutive failures')
            self._message_manager._add_context_message(UserMessage(content=msg))

    def _inject_exploration_nudge(self) -> None:
        if not self.settings.enable_planning or self.state.plan is not None:
            return
        if self.settings.planning_exploration_limit <= 0:
            return
        if self.state.n_steps >= self.settings.planning_exploration_limit:
            msg = f'PLANNING NUDGE: You have taken {self.state.n_steps} steps without creating a plan. If the task is complex, output a `plan_update` with clear todo items now. If the task is already done or nearly done, call `done` instead.'
            self.logger.info(f'📋 Exploration nudge injected after {self.state.n_steps} steps without a plan')
            self._message_manager._add_context_message(UserMessage(content=msg))

    def _inject_loop_detection_nudge(self) -> None:
        if not self.settings.loop_detection_enabled:
            return
        nudge = self.state.loop_detector.get_nudge_message()
        if nudge:
            self.logger.info(f'🔁 Loop detection nudge injected (repetition={self.state.loop_detector.max_repetition_count}, stagnation={self.state.loop_detector.consecutive_stagnant_pages})')
            self._message_manager._add_context_message(UserMessage(content=nudge))

    def _update_loop_detector_actions(self) -> None:
        if not self.settings.loop_detection_enabled:
            return
        if self.state.last_model_output is None:
            return
        _LOOP_EXEMPT_ACTIONS = {'wait', 'done', 'go_back'}
        for action in self.state.last_model_output.action:
            action_data = action.model_dump(exclude_unset=True)
            action_name = next(iter(action_data.keys()), 'unknown')
            if action_name in _LOOP_EXEMPT_ACTIONS:
                continue
            params = action_data.get(action_name, {})
            if not isinstance(params, dict):
                params = {}
            self.state.loop_detector.record_action(action_name, params)

    def _update_loop_detector_page_state(self, browser_state_summary: BrowserStateSummary) -> None:
        if not self.settings.loop_detection_enabled:
            return
        url = browser_state_summary.url or ''
        element_count = len(browser_state_summary.dom_state.selector_map) if browser_state_summary.dom_state else 0
        dom_text = ''
        if browser_state_summary.dom_state:
            try:
                dom_text = browser_state_summary.dom_state.llm_representation()
            except Exception:
                dom_text = ''
        self.state.loop_detector.record_page_state(url, dom_text, element_count)

    async def _inject_budget_warning(self, step_info: AgentStepInfo | None=None) -> None:
        if step_info is None:
            return
        steps_used = step_info.step_number + 1
        budget_ratio = steps_used / step_info.max_steps
        if budget_ratio >= 0.75 and (not step_info.is_last_step()):
            steps_remaining = step_info.max_steps - steps_used
            pct = int(budget_ratio * 100)
            msg = f'BUDGET WARNING: You have used {steps_used}/{step_info.max_steps} steps ({pct}%). {steps_remaining} steps remaining. If the task cannot be completed in the remaining steps, prioritize: (1) consolidate your results (save to files if the file system is in use), (2) call done with what you have. Partial results are far more valuable than exhausting all steps with nothing saved.'
            self.logger.info(f'Step budget warning: {steps_used}/{step_info.max_steps} ({pct}%)')
            self._message_manager._add_context_message(UserMessage(content=msg))

    async def _force_done_after_last_step(self, step_info: AgentStepInfo | None=None) -> None:
        if step_info and step_info.is_last_step():
            msg = 'You reached max_steps - this is your last step. Your only tool available is the "done" tool. No other tool is available. All other tools which you see in history or examples are not available.'
            msg += '\nIf the task is not yet fully finished as requested by the user, set success in "done" to false! E.g. if not all steps are fully completed. Else success to true.'
            msg += '\nInclude everything you found out for the ultimate task in the done text.'
            self.logger.debug('Last step finishing up')
            self._message_manager._add_context_message(UserMessage(content=msg))
            self.AgentOutput = self.DoneAgentOutput

    async def _force_done_after_failure(self) -> None:
        if self.state.consecutive_failures >= self.settings.max_failures and self.settings.final_response_after_failure:
            msg = f'You failed {self.settings.max_failures} times. Therefore we terminate the agent.'
            msg += '\nYour only tool available is the "done" tool. No other tool is available. All other tools which you see in history or examples are not available.'
            msg += '\nIf the task is not yet fully finished as requested by the user, set success in "done" to false! E.g. if not all steps are fully completed. Else success to true.'
            msg += '\nInclude everything you found out for the ultimate task in the done text.'
            self.logger.debug('Force done action, because we reached max_failures.')
            self._message_manager._add_context_message(UserMessage(content=msg))
            self.AgentOutput = self.DoneAgentOutput

    @observe(ignore_input=True, ignore_output=False)
    async def _judge_trace(self) -> JudgementResult | None:
        task = self.task
        final_result = self.history.final_result() or ''
        agent_steps = self.history.agent_steps()
        screenshot_paths = [p for p in self.history.screenshot_paths() if p is not None]
        input_messages = construct_judge_messages(task=task, final_result=final_result, agent_steps=agent_steps, screenshot_paths=screenshot_paths, max_images=10, ground_truth=self.settings.ground_truth, use_vision=self.settings.use_vision)
        kwargs: dict = {'output_format': JudgementResult}
        if self.judge_llm.provider == 'browser-use':
            kwargs['request_type'] = 'judge'
        try:
            response = await self.judge_llm.ainvoke(input_messages, **kwargs)
            judgement: JudgementResult = response.completion
            return judgement
        except Exception as e:
            self.logger.error(f'Judge trace failed: {e}')
            return None

    async def _judge_and_log(self) -> None:
        judgement = await self._judge_trace()
        if self.history.history[-1].result[-1].is_done:
            last_result = self.history.history[-1].result[-1]
            last_result.judgement = judgement
            self_reported_success = last_result.success
            if judgement:
                if self_reported_success is True and judgement.verdict is True:
                    return
                judge_log = '\n'
                if self_reported_success is True and judgement.verdict is False:
                    judge_log += '⚠️  \x1b[33mAgent reported success but judge thinks task failed\x1b[0m\n'
                verdict_color = '\x1b[32m' if judgement.verdict else '\x1b[31m'
                verdict_text = '✅ PASS' if judgement.verdict else '❌ FAIL'
                judge_log += f'⚖️  {verdict_color}Judge Verdict: {verdict_text}\x1b[0m\n'
                if judgement.failure_reason:
                    judge_log += f'   Failure Reason: {judgement.failure_reason}\n'
                if judgement.reached_captcha:
                    judge_log += '   🤖 Captcha Detected: Agent encountered captcha challenges\n'
                    judge_log += '   👉 🥷 Use Browser Use Cloud for the most stealth browser infra: https://docs.browser-use.com/customize/browser/remote\n'
                judge_log += f'   {judgement.reasoning}\n'
                self.logger.info(judge_log)

    async def _get_model_output_with_retry(self, input_messages: list[BaseMessage]) -> AgentOutput:
        model_output = await self.get_model_output(input_messages)
        self.logger.debug(f'✅ Step {self.state.n_steps}: Got LLM response with {(len(model_output.action) if model_output.action else 0)} actions')
        if not model_output.action or not isinstance(model_output.action, list) or all((action.model_dump() == {} for action in model_output.action)):
            self.logger.warning('Model returned empty action. Retrying...')
            clarification_message = UserMessage(content='You forgot to return an action. Please respond with a valid JSON action according to the expected schema with your assessment and next actions.')
            retry_messages = input_messages + [clarification_message]
            model_output = await self.get_model_output(retry_messages)
            if not model_output.action or all((action.model_dump() == {} for action in model_output.action)):
                self.logger.warning('Model still returned empty after retry. Inserting safe noop action.')
                action_instance = self.ActionModel()
                setattr(action_instance, 'done', {'success': False, 'text': 'No next action returned by LLM!'})
                model_output.action = [action_instance]
        return model_output

    async def _handle_post_llm_processing(self, browser_state_summary: BrowserStateSummary, input_messages: list[BaseMessage]) -> None:
        if self.register_new_step_callback and self.state.last_model_output:
            if inspect.iscoroutinefunction(self.register_new_step_callback):
                await self.register_new_step_callback(browser_state_summary, self.state.last_model_output, self.state.n_steps)
            else:
                self.register_new_step_callback(browser_state_summary, self.state.last_model_output, self.state.n_steps)
        if self.settings.save_conversation_path and self.state.last_model_output:
            conversation_dir = Path(self.settings.save_conversation_path)
            conversation_filename = f'conversation_{self.id}_{self.state.n_steps}.txt'
            target = conversation_dir / conversation_filename
            await save_conversation(input_messages, self.state.last_model_output, target, self.settings.save_conversation_path_encoding)

    async def _make_history_item(self, model_output: AgentOutput | None, browser_state_summary: BrowserStateSummary, result: list[ActionResult], metadata: StepMetadata | None=None, state_message: str | None=None) -> None:
        if model_output:
            interacted_elements = AgentHistory.get_interacted_element(model_output, browser_state_summary.dom_state.selector_map)
        else:
            interacted_elements = [None]
        screenshot_path = None
        if browser_state_summary.screenshot:
            self.logger.debug(f'📸 Storing screenshot for step {self.state.n_steps}, screenshot length: {len(browser_state_summary.screenshot)}')
            screenshot_path = await self.screenshot_service.store_screenshot(browser_state_summary.screenshot, self.state.n_steps)
            self.logger.debug(f'📸 Screenshot stored at: {screenshot_path}')
        else:
            self.logger.debug(f'📸 No screenshot in browser_state_summary for step {self.state.n_steps}')
        state_history = BrowserStateHistory(url=browser_state_summary.url, title=browser_state_summary.title, tabs=browser_state_summary.tabs, interacted_element=interacted_elements, screenshot_path=screenshot_path)
        history_item = AgentHistory(model_output=model_output, result=result, state=state_history, metadata=metadata, state_message=state_message)
        self.history.add_item(history_item)

    def _remove_think_tags(self, text: str) -> str:
        THINK_TAGS = re.compile('<think>.*?</think>', re.DOTALL)
        STRAY_CLOSE_TAG = re.compile('.*?</think>', re.DOTALL)
        text = re.sub(THINK_TAGS, '', text)
        text = re.sub(STRAY_CLOSE_TAG, '', text)
        return text.strip()

    def _replace_urls_in_text(self, text: str) -> tuple[str, dict[str, str]]:
        replaced_urls: dict[str, str] = {}

        def replace_url(match: re.Match) -> str:
            import hashlib
            original_url = match.group(0)
            query_start = original_url.find('?')
            fragment_start = original_url.find('#')
            after_path_start = len(original_url)
            if query_start != -1:
                after_path_start = min(after_path_start, query_start)
            if fragment_start != -1:
                after_path_start = min(after_path_start, fragment_start)
            base_url = original_url[:after_path_start]
            after_path = original_url[after_path_start:]
            if len(after_path) <= self._url_shortening_limit:
                return original_url
            if after_path:
                truncated_after_path = after_path[:self._url_shortening_limit]
                hash_obj = hashlib.md5(after_path.encode('utf-8'))
                short_hash = hash_obj.hexdigest()[:7]
                shortened = f'{base_url}{truncated_after_path}...{short_hash}'
                if len(shortened) < len(original_url):
                    replaced_urls[shortened] = original_url
                    return shortened
            return original_url
        return (URL_PATTERN.sub(replace_url, text), replaced_urls)

    def _process_messsages_and_replace_long_urls_shorter_ones(self, input_messages: list[BaseMessage]) -> dict[str, str]:
        from system.llm.messages import AssistantMessage, UserMessage
        urls_replaced: dict[str, str] = {}
        for message in input_messages:
            if isinstance(message, (UserMessage, AssistantMessage)):
                if isinstance(message.content, str):
                    message.content, replaced_urls = self._replace_urls_in_text(message.content)
                    urls_replaced.update(replaced_urls)
                elif isinstance(message.content, list):
                    for part in message.content:
                        if isinstance(part, ContentPartTextParam):
                            part.text, replaced_urls = self._replace_urls_in_text(part.text)
                            urls_replaced.update(replaced_urls)
        return urls_replaced

    @staticmethod
    def _recursive_process_all_strings_inside_pydantic_model(model: BaseModel, url_replacements: dict[str, str]) -> None:
        for field_name, field_value in model.__dict__.items():
            if isinstance(field_value, str):
                processed_string = Agent._replace_shortened_urls_in_string(field_value, url_replacements)
                setattr(model, field_name, processed_string)
            elif isinstance(field_value, BaseModel):
                Agent._recursive_process_all_strings_inside_pydantic_model(field_value, url_replacements)
            elif isinstance(field_value, dict):
                Agent._recursive_process_dict(field_value, url_replacements)
            elif isinstance(field_value, (list, tuple)):
                processed_value = Agent._recursive_process_list_or_tuple(field_value, url_replacements)
                setattr(model, field_name, processed_value)

    @staticmethod
    def _recursive_process_dict(dictionary: dict, url_replacements: dict[str, str]) -> None:
        for k, v in dictionary.items():
            if isinstance(v, str):
                dictionary[k] = Agent._replace_shortened_urls_in_string(v, url_replacements)
            elif isinstance(v, BaseModel):
                Agent._recursive_process_all_strings_inside_pydantic_model(v, url_replacements)
            elif isinstance(v, dict):
                Agent._recursive_process_dict(v, url_replacements)
            elif isinstance(v, (list, tuple)):
                dictionary[k] = Agent._recursive_process_list_or_tuple(v, url_replacements)

    @staticmethod
    def _recursive_process_list_or_tuple(container: list | tuple, url_replacements: dict[str, str]) -> list | tuple:
        if isinstance(container, tuple):
            processed_items = []
            for item in container:
                if isinstance(item, str):
                    processed_items.append(Agent._replace_shortened_urls_in_string(item, url_replacements))
                elif isinstance(item, BaseModel):
                    Agent._recursive_process_all_strings_inside_pydantic_model(item, url_replacements)
                    processed_items.append(item)
                elif isinstance(item, dict):
                    Agent._recursive_process_dict(item, url_replacements)
                    processed_items.append(item)
                elif isinstance(item, (list, tuple)):
                    processed_items.append(Agent._recursive_process_list_or_tuple(item, url_replacements))
                else:
                    processed_items.append(item)
            return tuple(processed_items)
        else:
            for i, item in enumerate(container):
                if isinstance(item, str):
                    container[i] = Agent._replace_shortened_urls_in_string(item, url_replacements)
                elif isinstance(item, BaseModel):
                    Agent._recursive_process_all_strings_inside_pydantic_model(item, url_replacements)
                elif isinstance(item, dict):
                    Agent._recursive_process_dict(item, url_replacements)
                elif isinstance(item, (list, tuple)):
                    container[i] = Agent._recursive_process_list_or_tuple(item, url_replacements)
            return container

    @staticmethod
    def _replace_shortened_urls_in_string(text: str, url_replacements: dict[str, str]) -> str:
        result = text
        for shortened_url, original_url in url_replacements.items():
            result = result.replace(shortened_url, original_url)
        return result

    @time_execution_async('--get_next_action')
    @observe_debug(ignore_input=True, ignore_output=True, name='get_model_output')
    async def get_model_output(self, input_messages: list[BaseMessage]) -> AgentOutput:
        urls_replaced = self._process_messsages_and_replace_long_urls_shorter_ones(input_messages)
        kwargs: dict = {'output_format': self.AgentOutput, 'session_id': self.session_id}
        try:
            response = await self.llm.ainvoke(input_messages, **kwargs)
            parsed: AgentOutput = response.completion
            if urls_replaced:
                self._recursive_process_all_strings_inside_pydantic_model(parsed, urls_replaced)
            if len(parsed.action) > self.settings.max_actions_per_step:
                parsed.action = parsed.action[:self.settings.max_actions_per_step]
            if not (hasattr(self.state, 'paused') and (self.state.paused or self.state.stopped)):
                log_response(parsed, self.tools.registry.registry, self.logger)
                await self._broadcast_model_state(parsed)
                if parsed.current_state and parsed.current_state.thinking:
                    self._emit_mock_frontend_event({'event_type': 'agent_thought', 'text': parsed.current_state.thinking, 'step': self.state.n_steps})
            self._log_next_action_summary(parsed)
            return parsed
        except ValidationError:
            raise
        except (ModelRateLimitError, ModelProviderError) as e:
            if not self._try_switch_to_fallback_llm(e):
                raise
            return await self.get_model_output(input_messages)

    def _try_switch_to_fallback_llm(self, error: ModelRateLimitError | ModelProviderError) -> bool:
        if self._using_fallback_llm:
            self.logger.warning(f'⚠️ Fallback LLM also failed ({type(error).__name__}: {error.message}), no more fallbacks available')
            return False
        retryable_status_codes = {401, 402, 429, 500, 502, 503, 504}
        is_retryable = isinstance(error, ModelRateLimitError) or (hasattr(error, 'status_code') and error.status_code in retryable_status_codes)
        if not is_retryable:
            return False
        if self._fallback_llm is None:
            self.logger.warning(f'⚠️ LLM error ({type(error).__name__}: {error.message}) but no fallback_llm configured')
            return False
        self._log_fallback_switch(error, self._fallback_llm)
        self.llm = self._fallback_llm
        self._using_fallback_llm = True
        self.token_cost_service.register_llm(self._fallback_llm)
        return True

    def _log_fallback_switch(self, error: ModelRateLimitError | ModelProviderError, fallback: BaseChatModel) -> None:
        original_model = self._original_llm.model if hasattr(self._original_llm, 'model') else 'unknown'
        fallback_model = fallback.model if hasattr(fallback, 'model') else 'unknown'
        error_type = type(error).__name__
        status_code = getattr(error, 'status_code', 'N/A')
        self.logger.warning(f'⚠️ Primary LLM ({original_model}) failed with {error_type} (status={status_code}), switching to fallback LLM ({fallback_model})')

    async def _log_agent_run(self) -> None:
        self.logger.info(f'\x1b[34m🎯 Task: {self.task}\x1b[0m')
        self.logger.debug(f'🤖 Browser-Use Library Version {self.version} ({self.source})')
        if CONFIG.BROWSER_USE_VERSION_CHECK:
            latest_version = await check_latest_browser_use_version()
            if latest_version and latest_version != self.version:
                self.logger.info(f'📦 Newer version available: {latest_version} (current: {self.version}). Upgrade with: uv add browser-use=={latest_version}')

    def _log_first_step_startup(self) -> None:
        if len(self.history.history) == 0:
            self.logger.info(f'Starting a browser-use agent with version {self.version}, with provider={self.llm.provider} and model={self.llm.model}')

    def _log_step_context(self, browser_state_summary: BrowserStateSummary) -> None:
        url = browser_state_summary.url if browser_state_summary else ''
        url_short = url[:50] + '...' if len(url) > 50 else url
        interactive_count = len(browser_state_summary.dom_state.selector_map) if browser_state_summary else 0
        self.logger.info('\n')
        self.logger.info(f'📍 Step {self.state.n_steps}:')
        self.logger.debug(f'Evaluating page with {interactive_count} interactive elements on: {url_short}')

    def _log_next_action_summary(self, parsed: 'AgentOutput') -> None:
        if not (self.logger.isEnabledFor(logging.DEBUG) and parsed.action):
            return
        action_count = len(parsed.action)
        action_details = []
        for i, action in enumerate(parsed.action):
            action_data = action.model_dump(exclude_unset=True)
            action_name = next(iter(action_data.keys())) if action_data else 'unknown'
            action_params = action_data.get(action_name, {}) if action_data else {}
            param_summary = []
            if isinstance(action_params, dict):
                for key, value in action_params.items():
                    if key == 'index':
                        param_summary.append(f'#{value}')
                    elif key == 'text' and isinstance(value, str):
                        text_preview = value[:30] + '...' if len(value) > 30 else value
                        param_summary.append(f'text="{text_preview}"')
                    elif key == 'url':
                        param_summary.append(f'url="{value}"')
                    elif key == 'success':
                        param_summary.append(f'success={value}')
                    elif isinstance(value, (str, int, bool)):
                        val_str = str(value)[:30] + '...' if len(str(value)) > 30 else str(value)
                        param_summary.append(f'{key}={val_str}')
            param_str = f"({', '.join(param_summary)})" if param_summary else ''
            action_details.append(f'{action_name}{param_str}')

    def _prepare_demo_message(self, message: str, limit: int=600) -> str:
        return message.strip()

    async def _demo_mode_log(self, message: str, level: str='info', metadata: dict[str, Any] | None=None) -> None:
        return

    async def _broadcast_model_state(self, parsed: 'AgentOutput') -> None:
        if not self._demo_mode_enabled:
            return
        state = parsed.current_state
        step_meta = {'step': self.state.n_steps}
        if state.thinking:
            await self._demo_mode_log(state.thinking, 'thought', step_meta)
        if state.evaluation_previous_goal:
            eval_text = state.evaluation_previous_goal
            level = 'success' if 'success' in eval_text.lower() else 'warning' if 'failure' in eval_text.lower() else 'info'
            await self._demo_mode_log(eval_text, level, step_meta)
        if state.memory:
            await self._demo_mode_log(f'Memory: {state.memory}', 'info', step_meta)
        if state.next_goal:
            await self._demo_mode_log(f'Next goal: {state.next_goal}', 'info', step_meta)

    def _log_step_completion_summary(self, step_start_time: float, result: list[ActionResult]) -> str | None:
        if not result:
            return None
        step_duration = time.time() - step_start_time
        action_count = len(result)
        success_count = sum((1 for r in result if not r.error))
        failure_count = action_count - success_count
        success_indicator = f'✅ {success_count}' if success_count > 0 else ''
        failure_indicator = f'❌ {failure_count}' if failure_count > 0 else ''
        status_parts = [part for part in [success_indicator, failure_indicator] if part]
        status_str = ' | '.join(status_parts) if status_parts else '✅ 0'
        message = f"📍 Step {self.state.n_steps}: Ran {action_count} action{('' if action_count == 1 else 's')} in {step_duration:.2f}s: {status_str}"
        self.logger.debug(message)
        return message

    def _log_final_outcome_messages(self) -> None:
        is_successful = self.history.is_successful()
        if is_successful is False or is_successful is None:
            final_result = self.history.final_result()
            final_result_str = str(final_result).lower() if final_result else ''
            captcha_keywords = ['captcha', 'cloudflare', 'recaptcha', 'challenge', 'bot detection', 'access denied']
            has_captcha_issue = any((keyword in final_result_str for keyword in captcha_keywords))
            if has_captcha_issue:
                task_preview = self.task[:10] if len(self.task) > 10 else self.task
                self.logger.info('')
                self.logger.info('Failed because of CAPTCHA? For better browser stealth, try:')
                self.logger.info(f'   agent = Agent(task="{task_preview}...", browser=Browser(use_cloud=True))')
            self.logger.info('')
            self.logger.info('Did the Agent not work as expected? Let us fix this!')
            self.logger.info('   Open a short issue on GitHub: https://github.com/browser-use/browser-use/issues')

    def _log_agent_event(self, max_steps: int, agent_run_error: str | None=None) -> None:
        token_summary = self.token_cost_service.get_usage_tokens_for_model(self.llm.model)
        action_history_data = []
        for item in self.history.history:
            if item.model_output and item.model_output.action:
                step_actions = [action.model_dump(exclude_unset=True) for action in item.model_output.action if action]
                action_history_data.append(step_actions)
            else:
                action_history_data.append(None)
        final_res = self.history.final_result()
        final_result_str = json.dumps(final_res) if final_res is not None else None
        judgement_data = self.history.judgement()
        judge_verdict = judgement_data.get('verdict') if judgement_data else None
        judge_reasoning = judgement_data.get('reasoning') if judgement_data else None
        judge_failure_reason = judgement_data.get('failure_reason') if judgement_data else None
        judge_reached_captcha = judgement_data.get('reached_captcha') if judgement_data else None
        judge_impossible_task = judgement_data.get('impossible_task') if judgement_data else None
        self.telemetry.capture(AgentTelemetryEvent(task=self.task, model=self.llm.model, model_provider=self.llm.provider, max_steps=max_steps, max_actions_per_step=self.settings.max_actions_per_step, use_vision=self.settings.use_vision, version=self.version, source=self.source, cdp_url=urlparse(self.browser_session.cdp_url).hostname if self.browser_session and self.browser_session.cdp_url else None, agent_type=None, action_errors=self.history.errors(), action_history=action_history_data, urls_visited=self.history.urls(), steps=self.state.n_steps, total_input_tokens=token_summary.prompt_tokens, total_output_tokens=token_summary.completion_tokens, prompt_cached_tokens=token_summary.prompt_cached_tokens, total_tokens=token_summary.total_tokens, total_duration_seconds=self.history.total_duration_seconds(), success=self.history.is_successful(), final_result_response=final_result_str, error_message=agent_run_error, judge_verdict=judge_verdict, judge_reasoning=judge_reasoning, judge_failure_reason=judge_failure_reason, judge_reached_captcha=judge_reached_captcha, judge_impossible_task=judge_impossible_task))

    async def take_step(self, step_info: AgentStepInfo | None=None) -> tuple[bool, bool]:
        if step_info is not None and step_info.step_number == 0:
            self._log_first_step_startup()
            try:
                await self._execute_initial_actions()
            except InterruptedError:
                pass
            except Exception as e:
                raise e
        await self.step(step_info)
        if self.history.is_done():
            await self.log_completion()
            if self.settings.use_judge:
                await self._judge_and_log()
            if self.register_done_callback:
                if inspect.iscoroutinefunction(self.register_done_callback):
                    await self.register_done_callback(self.history)
                else:
                    self.register_done_callback(self.history)
            return (True, True)
        return (False, False)

    def _extract_start_url(self, task: str) -> str | None:
        import re
        task_without_emails = re.sub('\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b', '', task)
        patterns = ['https?://[^\\s<>"\\\']+', '(?:www\\.)?[a-zA-Z0-9-]+(?:\\.[a-zA-Z0-9-]+)*\\.[a-zA-Z]{2,}(?:/[^\\s<>"\\\']*)?']
        excluded_extensions = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'odp', 'txt', 'md', 'csv', 'json', 'xml', 'yaml', 'yml', 'zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp', 'ico', 'mp3', 'mp4', 'avi', 'mkv', 'mov', 'wav', 'flac', 'ogg', 'py', 'js', 'css', 'java', 'cpp', 'bib', 'bibtex', 'tex', 'latex', 'cls', 'sty', 'exe', 'msi', 'dmg', 'pkg', 'deb', 'rpm', 'iso', 'polynomial'}
        excluded_words = {'never', 'dont', 'not', "don't"}
        found_urls = []
        for pattern in patterns:
            matches = re.finditer(pattern, task_without_emails)
            for match in matches:
                url = match.group(0)
                original_position = match.start()
                url = re.sub('[.,;:!?()\\[\\]]+$', '', url)
                url_lower = url.lower()
                should_exclude = False
                for ext in excluded_extensions:
                    if f'.{ext}' in url_lower:
                        should_exclude = True
                        break
                if should_exclude:
                    self.logger.debug(f'Excluding URL with file extension from auto-navigation: {url}')
                    continue
                context_start = max(0, original_position - 20)
                context_text = task_without_emails[context_start:original_position]
                if any((word.lower() in context_text.lower() for word in excluded_words)):
                    self.logger.debug(f'Excluding URL with word in excluded words from auto-navigation: {url} (context: "{context_text.strip()}")')
                    continue
                if not url.startswith(('http://', 'https://')):
                    url = 'https://' + url
                found_urls.append(url)
        unique_urls = list(set(found_urls))
        if len(unique_urls) > 1:
            self.logger.debug(f'Multiple URLs found ({len(found_urls)}), skipping directly_open_url to avoid ambiguity')
            return None
        if len(unique_urls) == 1:
            return unique_urls[0]
        return None

    async def _execute_step(self, step: int, max_steps: int, step_info: AgentStepInfo, on_step_start: AgentHookFunc | None=None, on_step_end: AgentHookFunc | None=None) -> bool:
        if on_step_start is not None:
            await on_step_start(self)
        await self._demo_mode_log(f'Starting step {step + 1}/{max_steps}', 'info', {'step': step + 1, 'total_steps': max_steps})
        self.logger.debug(f'🚶 Starting step {step + 1}/{max_steps}...')
        try:
            await asyncio.wait_for(self.step(step_info), timeout=self.settings.step_timeout)
            self.logger.debug(f'✅ Completed step {step + 1}/{max_steps}')
        except TimeoutError:
            error_msg = f'Step {step + 1} timed out after {self.settings.step_timeout} seconds'
            self.logger.error(f'⏰ {error_msg}')
            await self._demo_mode_log(error_msg, 'error', {'step': step + 1})
            self.state.consecutive_failures += 1
            self.state.last_result = [ActionResult(error=error_msg)]
        if on_step_end is not None:
            await on_step_end(self)
        if self.history.is_done():
            await self.log_completion()
            if self.settings.use_judge:
                await self._judge_and_log()
            if self.register_done_callback:
                if inspect.iscoroutinefunction(self.register_done_callback):
                    await self.register_done_callback(self.history)
                else:
                    self.register_done_callback(self.history)
            return True
        return False

    @observe(name='agent.run', ignore_input=True, ignore_output=True)
    @time_execution_async('--run')
    async def run(self, max_steps: int=500, on_step_start: AgentHookFunc | None=None, on_step_end: AgentHookFunc | None=None) -> AgentHistoryList[AgentStructuredOutput]:
        loop = asyncio.get_event_loop()
        agent_run_error: str | None = None
        self._force_exit_telemetry_logged = False
        should_delay_close = False
        from system.utils import SignalHandler

        def on_force_exit_log_telemetry():
            self._log_agent_event(max_steps=max_steps, agent_run_error='SIGINT: Cancelled by user')
            if hasattr(self, 'telemetry') and self.telemetry:
                self.telemetry.flush()
            self._force_exit_telemetry_logged = True
        signal_handler = SignalHandler(loop=loop, pause_callback=self.pause, resume_callback=self.resume, custom_exit_callback=on_force_exit_log_telemetry, exit_on_second_int=True)
        signal_handler.register()
        try:
            await self._log_agent_run()
            self.logger.debug(f"🔧 Agent setup: Agent Session ID {self.session_id[-4:]}, Task ID {self.task_id[-4:]}, Browser Session ID {(self.browser_session.id[-4:] if self.browser_session else 'None')} {('(connecting via CDP)' if self.browser_session and self.browser_session.cdp_url else '(launching local browser)')}")
            self._session_start_time = time.time()
            self._task_start_time = self._session_start_time
            if not self.state.session_initialized:
                self.logger.debug('📡 Dispatching CreateAgentSessionEvent...')
                self.eventbus.dispatch(CreateAgentSessionEvent.from_agent(self))
                self.state.session_initialized = True
            self.logger.debug('📡 Dispatching CreateAgentTaskEvent...')
            self.eventbus.dispatch(CreateAgentTaskEvent.from_agent(self))
            self._log_first_step_startup()
            await self.browser_session.start()
            if self._demo_mode_enabled:
                await self._demo_mode_log(f'Started task: {self.task}', 'info', {'tag': 'task'})
                await self._demo_mode_log('Demo mode active - follow the side panel for live thoughts and actions.', 'info', {'tag': 'status'})
            await self._register_skills_as_actions()
            try:
                await self._execute_initial_actions()
            except InterruptedError:
                pass
            except Exception as e:
                raise e
            self.logger.debug(f'🔄 Starting main execution loop with max {max_steps} steps (currently at step {self.state.n_steps})...')
            while self.state.n_steps <= max_steps:
                current_step = self.state.n_steps - 1
                if self.state.paused:
                    self.logger.debug(f'⏸️ Step {self.state.n_steps}: Agent paused, waiting to resume...')
                    await self._external_pause_event.wait()
                    signal_handler.reset()
                if self.state.consecutive_failures >= self.settings.max_failures + int(self.settings.final_response_after_failure):
                    self.logger.error(f'❌ Stopping due to {self.settings.max_failures} consecutive failures')
                    agent_run_error = f'Stopped due to {self.settings.max_failures} consecutive failures'
                    break
                if self.state.stopped:
                    self.logger.info('🛑 Agent stopped')
                    agent_run_error = 'Agent stopped programmatically'
                    break
                step_info = AgentStepInfo(step_number=current_step, max_steps=max_steps)
                is_done = await self._execute_step(current_step, max_steps, step_info, on_step_start, on_step_end)
                if is_done:
                    if self._demo_mode_enabled and self.history.history:
                        final_result_text = self.history.final_result() or 'Task completed'
                        await self._demo_mode_log(f'Final Result: {final_result_text}', 'success', {'tag': 'task'})
                    should_delay_close = True
                    break
            else:
                agent_run_error = 'Failed to complete task in maximum steps'
                self.history.add_item(AgentHistory(model_output=None, result=[ActionResult(error=agent_run_error, include_in_memory=True)], state=BrowserStateHistory(url='', title='', tabs=[], interacted_element=[], screenshot_path=None), metadata=None))
                self.logger.info(f'❌ {agent_run_error}')
            self.history.usage = await self.token_cost_service.get_usage_summary()
            if agent_run_error is None and self.history.is_successful() and (not self.workflow_executor):
                try:
                    from system.agent.workflow import WorkflowRecorder
                    workflow_template = WorkflowRecorder.extract_workflow(self.history, original_task=self.task)
                    workflow_json = workflow_template.to_json()
                    workflow_dir_raw = getattr(CONFIG, 'MIRA_WORKFLOW_OUTPUT_DIR', None) or os.getenv('MIRA_WORKFLOW_OUTPUT_DIR')
                    if workflow_dir_raw:
                        output_dir = Path(workflow_dir_raw).expanduser().resolve()
                    else:
                        output_dir = Path(tempfile.gettempdir()) / 'mira_workflows'
                    output_dir.mkdir(parents=True, exist_ok=True)
                    workflow_path = output_dir / f'macro_{workflow_template.workflow_id}.json'
                    with open(workflow_path, 'w', encoding='utf-8') as f:
                        f.write(workflow_json)
                    self.logger.info(f'📁 Workflow macro successfully recorded to {workflow_path}')
                    self._emit_mock_frontend_event({'event_type': 'workflow_macro_recorded', 'workflow_id': workflow_template.workflow_id, 'target_url': workflow_template.target_url, 'step_count': len(workflow_template.steps), 'workflow_json': workflow_json})
                except Exception as macro_err:
                    self.logger.warning(f'⚠️ Failed to compile workflow macro: {macro_err}')
            if self.history._output_model_schema is None and self.output_model_schema is not None:
                self.history._output_model_schema = self.output_model_schema
            return self.history
        except KeyboardInterrupt:
            self.logger.debug('Got KeyboardInterrupt during execution, returning current history')
            agent_run_error = 'KeyboardInterrupt'
            self.history.usage = await self.token_cost_service.get_usage_summary()
            return self.history
        except Exception as e:
            self.logger.error(f'Agent run failed with exception: {e}', exc_info=True)
            agent_run_error = str(e)
            raise e
        finally:
            if should_delay_close and self._demo_mode_enabled and (agent_run_error is None):
                await asyncio.sleep(30)
            if agent_run_error:
                await self._demo_mode_log(f'Agent stopped: {agent_run_error}', 'error', {'tag': 'run'})
            await self.token_cost_service.log_usage_summary()
            signal_handler.unregister()
            if not self._force_exit_telemetry_logged:
                try:
                    self._log_agent_event(max_steps=max_steps, agent_run_error=agent_run_error)
                except Exception as log_e:
                    self.logger.error(f'Failed to log telemetry event: {log_e}', exc_info=True)
            else:
                self.logger.debug('Telemetry for force exit (SIGINT) was logged by custom exit callback.')
            self.eventbus.dispatch(UpdateAgentTaskEvent.from_agent(self))
            if self.settings.generate_gif:
                output_path: str = 'agent_history.gif'
                if isinstance(self.settings.generate_gif, str):
                    output_path = self.settings.generate_gif
                from system.agent.gif import create_history_gif
                create_history_gif(task=self.task, history=self.history, output_path=output_path)
                if Path(output_path).exists():
                    output_event = await CreateAgentOutputFileEvent.from_agent_and_file(self, output_path)
                    self.eventbus.dispatch(output_event)
            self._log_final_outcome_messages()
            await self.eventbus.stop(clear=True, timeout=_get_timeout('TIMEOUT_AgentEventBusStop', 3.0))
            await self.close()

    @observe_debug(ignore_input=True, ignore_output=True)
    @time_execution_async('--multi_act')
    async def multi_act(self, actions: list[ActionModel]) -> list[ActionResult]:
        results: list[ActionResult] = []
        time_elapsed = 0
        total_actions = len(actions)
        assert self.browser_session is not None, 'BrowserSession is not set up'
        try:
            if self.browser_session._cached_browser_state_summary is not None and self.browser_session._cached_browser_state_summary.dom_state is not None:
                cached_selector_map = dict(self.browser_session._cached_browser_state_summary.dom_state.selector_map)
                cached_element_hashes = {e.parent_branch_hash() for e in cached_selector_map.values()}
            else:
                cached_selector_map = {}
                cached_element_hashes = set()
        except Exception as e:
            self.logger.error(f'Error getting cached selector map: {e}')
            cached_selector_map = {}
            cached_element_hashes = set()
        for i, action in enumerate(actions):
            action_data = action.model_dump(exclude_unset=True)
            action_name = next(iter(action_data.keys())) if action_data else 'unknown'
            if i > 0:
                if action_data.get('done') is not None:
                    msg = f'Done action is allowed only as a single action - stopped after action {i} / {total_actions}.'
                    self.logger.debug(msg)
                    break
            if i > 0:
                self.logger.debug(f'Waiting {self.browser_profile.wait_between_actions} seconds between actions')
                await asyncio.sleep(self.browser_profile.wait_between_actions)
            try:
                await self._check_stop_or_pause()
                await self._log_action(action, action_name, i + 1, total_actions)
                pre_action_url = await self.browser_session.get_current_page_url()
                pre_action_focus = self.browser_session.agent_focus_target_id
                time_start = time.time()
                result = await self.tools.act(action=action, browser_session=self.browser_session, file_system=self.file_system, page_extraction_llm=self.settings.page_extraction_llm, sensitive_data=self.sensitive_data, available_file_paths=self.available_file_paths, extraction_schema=self.extraction_schema)
                time_end = time.time()
                time_elapsed = time_end - time_start
                if result.error:
                    await self._demo_mode_log(f'Action "{action_name}" failed: {result.error}', 'error', {'action': action_name, 'step': self.state.n_steps})
                elif result.is_done:
                    completion_text = result.long_term_memory or result.extracted_content or 'Task marked as done.'
                    level = 'success' if result.success is not False else 'warning'
                    await self._demo_mode_log(completion_text, level, {'action': action_name, 'step': self.state.n_steps})
                results.append(result)
                if results[-1].is_done or results[-1].error or i == total_actions - 1:
                    break
                registered_action = self.tools.registry.registry.actions.get(action_name)
                if registered_action and registered_action.terminates_sequence:
                    self.logger.info(f'Action "{action_name}" terminates sequence — skipping {total_actions - i - 1} remaining action(s)')
                    break
                post_action_url = await self.browser_session.get_current_page_url()
                post_action_focus = self.browser_session.agent_focus_target_id
                if post_action_url != pre_action_url or post_action_focus != pre_action_focus:
                    self.logger.info(f'Page changed after "{action_name}" — skipping {total_actions - i - 1} remaining action(s)')
                    break
            except Exception as e:
                self.logger.error(f'❌ Executing action {i + 1} failed -> {type(e).__name__}: {e}')
                await self._demo_mode_log(f'Action "{action_name}" raised {type(e).__name__}: {e}', 'error', {'action': action_name, 'step': self.state.n_steps})
                raise e
        return results

    async def _log_action(self, action, action_name: str, action_num: int, total_actions: int) -> None:
        blue = '\x1b[34m'
        magenta = '\x1b[35m'
        reset = '\x1b[0m'
        if total_actions > 1:
            action_header = f'▶️  [{action_num}/{total_actions}] {blue}{action_name}{reset}:'
            plain_header = f'▶️  [{action_num}/{total_actions}] {action_name}:'
        else:
            action_header = f'▶️   {blue}{action_name}{reset}:'
            plain_header = f'▶️  {action_name}:'
        action_data = action.model_dump(exclude_unset=True)
        params = action_data.get(action_name, {})
        param_parts = []
        plain_param_parts = []
        if params and isinstance(params, dict):
            for param_name, value in params.items():
                if isinstance(value, str) and len(value) > 150:
                    display_value = value[:150] + '...'
                elif isinstance(value, list) and len(str(value)) > 200:
                    display_value = str(value)[:200] + '...'
                else:
                    display_value = value
                param_parts.append(f'{magenta}{param_name}{reset}: {display_value}')
                plain_param_parts.append(f'{param_name}: {display_value}')
        if param_parts:
            params_string = ', '.join(param_parts)
            self.logger.info(f'  {action_header} {params_string}')
        else:
            self.logger.info(f'  {action_header}')
        if self._demo_mode_enabled:
            panel_message = plain_header
            if plain_param_parts:
                panel_message = f"{panel_message} {', '.join(plain_param_parts)}"
            await self._demo_mode_log(panel_message.strip(), 'action', {'action': action_name, 'step': self.state.n_steps})

    async def log_completion(self) -> None:
        if self.history.is_successful():
            self.logger.info('✅ Task completed successfully')
            await self._demo_mode_log('Task completed successfully', 'success', {'tag': 'task'})

    async def _generate_rerun_summary(self, original_task: str, results: list[ActionResult], summary_llm: BaseChatModel | None=None) -> ActionResult:
        from system.agent.views import RerunSummaryAction
        screenshot_b64 = None
        try:
            screenshot = await self.browser_session.take_screenshot(full_page=False)
            if screenshot:
                import base64
                screenshot_b64 = base64.b64encode(screenshot).decode('utf-8')
        except Exception as e:
            self.logger.warning(f'Failed to capture screenshot for rerun summary: {e}')
        error_count = sum((1 for r in results if r.error))
        success_count = len(results) - error_count
        from system.agent.prompts import get_rerun_summary_message, get_rerun_summary_prompt
        prompt = get_rerun_summary_prompt(original_task=original_task, total_steps=len(results), success_count=success_count, error_count=error_count)
        try:
            if summary_llm is None:
                summary_llm = self.llm
                self.logger.debug('Using agent LLM for rerun summary')
            else:
                self.logger.debug(f'Using provided LLM for rerun summary: {summary_llm.model}')
            from system.llm.messages import BaseMessage
            message = get_rerun_summary_message(prompt, screenshot_b64)
            messages: list[BaseMessage] = [message]
            self.logger.debug(f'Calling LLM for rerun summary with {len(messages)} message(s)')
            try:
                kwargs: dict = {'output_format': RerunSummaryAction}
                response = await summary_llm.ainvoke(messages, **kwargs)
                summary: RerunSummaryAction = response.completion
                self.logger.debug(f'LLM response type: {type(summary)}')
                self.logger.debug(f'LLM response: {summary}')
            except Exception as structured_error:
                self.logger.debug(f'Structured output failed: {structured_error}, falling back to text response')
                response = await summary_llm.ainvoke(messages, None)
                response_text = response.completion
                self.logger.debug(f'LLM text response: {response_text}')
                summary = RerunSummaryAction(summary=response_text if isinstance(response_text, str) else str(response_text), success=error_count == 0, completion_status='complete' if error_count == 0 else 'partial' if success_count > 0 else 'failed')
            self.logger.info(f'📊 Rerun Summary: {summary.summary}')
            self.logger.info(f'📊 Status: {summary.completion_status} (success={summary.success})')
            return ActionResult(is_done=True, success=summary.success, extracted_content=summary.summary, long_term_memory=f'Rerun completed with status: {summary.completion_status}. {summary.summary[:100]}')
        except Exception as e:
            self.logger.warning(f'Failed to generate AI summary: {e.__class__.__name__}: {e}')
            self.logger.debug('Full error traceback:', exc_info=True)
            return ActionResult(is_done=True, success=error_count == 0, extracted_content=f'Rerun completed: {success_count}/{len(results)} steps succeeded', long_term_memory=f'Rerun completed: {success_count} steps succeeded, {error_count} errors')

    async def _execute_ai_step(self, query: str, include_screenshot: bool=False, extract_links: bool=False, ai_step_llm: BaseChatModel | None=None) -> ActionResult:
        from system.agent.prompts import get_ai_step_system_prompt, get_ai_step_user_prompt, get_rerun_summary_message
        from system.llm.messages import SystemMessage, UserMessage
        from system.utils import sanitize_surrogates
        llm = ai_step_llm or self.llm
        self.logger.debug(f'Using LLM for AI step: {llm.model}')
        try:
            from system.dom.markdown_extractor import extract_clean_markdown
            content, content_stats = await extract_clean_markdown(browser_session=self.browser_session, extract_links=extract_links)
        except Exception as e:
            return ActionResult(error=f'Could not extract clean markdown: {type(e).__name__}: {e}')
        screenshot_b64 = None
        if include_screenshot:
            try:
                screenshot = await self.browser_session.take_screenshot(full_page=False)
                if screenshot:
                    import base64
                    screenshot_b64 = base64.b64encode(screenshot).decode('utf-8')
            except Exception as e:
                self.logger.warning(f'Failed to capture screenshot for ai_step: {e}')
        original_html_length = content_stats['original_html_chars']
        initial_markdown_length = content_stats['initial_markdown_chars']
        final_filtered_length = content_stats['final_filtered_chars']
        chars_filtered = content_stats['filtered_chars_removed']
        stats_summary = f'Content processed: {original_html_length:,} HTML chars → {initial_markdown_length:,} initial markdown → {final_filtered_length:,} filtered markdown'
        if chars_filtered > 0:
            stats_summary += f' (filtered {chars_filtered:,} chars of noise)'
        content = sanitize_surrogates(content)
        query = sanitize_surrogates(query)
        system_prompt = get_ai_step_system_prompt()
        prompt_text = get_ai_step_user_prompt(query, stats_summary, content)
        if screenshot_b64:
            user_message = get_rerun_summary_message(prompt_text, screenshot_b64)
        else:
            user_message = UserMessage(content=prompt_text)
        try:
            import asyncio
            response = await asyncio.wait_for(llm.ainvoke([SystemMessage(content=system_prompt), user_message]), timeout=120.0)
            current_url = await self.browser_session.get_current_page_url()
            extracted_content = f'<url>\n{current_url}\n</url>\n<query>\n{query}\n</query>\n<result>\n{response.completion}\n</result>'
            MAX_MEMORY_LENGTH = 1000
            if len(extracted_content) < MAX_MEMORY_LENGTH:
                memory = extracted_content
                include_extracted_content_only_once = False
            else:
                file_name = await self.file_system.save_extracted_content(extracted_content)
                memory = f'Query: {query}\nContent in {file_name} and once in <read_state>.'
                include_extracted_content_only_once = True
            self.logger.info(f'🤖 AI Step: {memory}')
            return ActionResult(extracted_content=extracted_content, include_extracted_content_only_once=include_extracted_content_only_once, long_term_memory=memory)
        except Exception as e:
            self.logger.warning(f'Failed to execute AI step: {e.__class__.__name__}: {e}')
            self.logger.debug('Full error traceback:', exc_info=True)
            return ActionResult(error=f'AI step failed: {e}')

    async def rerun_history(self, history: AgentHistoryList, max_retries: int=3, skip_failures: bool=False, delay_between_actions: float=2.0, max_step_interval: float=45.0, summary_llm: BaseChatModel | None=None, ai_step_llm: BaseChatModel | None=None, wait_for_elements: bool=False) -> list[ActionResult]:
        self.state.session_initialized = True
        await self.browser_session.start()
        results = []
        previous_item: AgentHistory | None = None
        previous_step_succeeded: bool = False
        try:
            for i, history_item in enumerate(history.history):
                goal = history_item.model_output.current_state.next_goal if history_item.model_output else ''
                step_num = history_item.metadata.step_number if history_item.metadata else i
                step_name = 'Initial actions' if step_num == 0 else f'Step {step_num}'
                if history_item.metadata and history_item.metadata.step_interval is not None:
                    step_delay = min(history_item.metadata.step_interval, max_step_interval)
                    if step_delay < 1.0:
                        delay_str = f'{step_delay * 1000:.0f}ms'
                    else:
                        delay_str = f'{step_delay:.1f}s'
                    if history_item.metadata.step_interval > max_step_interval:
                        delay_source = f'capped to {delay_str} (saved was {history_item.metadata.step_interval:.1f}s)'
                    else:
                        delay_source = f'using saved step_interval={delay_str}'
                else:
                    step_delay = delay_between_actions
                    if step_delay < 1.0:
                        delay_str = f'{step_delay * 1000:.0f}ms'
                    else:
                        delay_str = f'{step_delay:.1f}s'
                    delay_source = f'using default delay={delay_str}'
                self.logger.info(f'Replaying {step_name} ({i + 1}/{len(history.history)}) [{delay_source}]: {goal}')
                if not history_item.model_output or not history_item.model_output.action or history_item.model_output.action == [None]:
                    self.logger.warning(f'{step_name}: No action to replay, skipping')
                    results.append(ActionResult(error='No action to replay'))
                    continue
                original_had_error = any((r.error for r in history_item.result if r.error))
                if original_had_error and skip_failures:
                    error_msgs = [r.error for r in history_item.result if r.error]
                    self.logger.warning(f"{step_name}: Original step had error(s), skipping (skip_failures=True): {(error_msgs[0][:100] if error_msgs else 'unknown')}")
                    results.append(ActionResult(error=f"Skipped - original step had error: {(error_msgs[0][:100] if error_msgs else 'unknown')}"))
                    continue
                if self._is_redundant_retry_step(history_item, previous_item, previous_step_succeeded):
                    self.logger.info(f'{step_name}: Skipping redundant retry (previous step already succeeded with same element)')
                    results.append(ActionResult(extracted_content='Skipped - redundant retry of previous step', include_in_memory=False))
                    continue
                retry_count = 0
                step_succeeded = False
                menu_reopened = False
                base_retry_delay = 5.0
                max_retry_delay = 30.0
                while retry_count < max_retries:
                    try:
                        result = await self._execute_history_step(history_item, step_delay, ai_step_llm, wait_for_elements)
                        results.extend(result)
                        step_succeeded = True
                        break
                    except Exception as e:
                        error_str = str(e)
                        retry_count += 1
                        if not menu_reopened and 'Could not find matching element' in error_str and (previous_item is not None) and self._is_menu_opener_step(previous_item):
                            curr_elements = history_item.state.interacted_element if history_item.state else []
                            curr_elem = curr_elements[0] if curr_elements else None
                            if self._is_menu_item_element(curr_elem):
                                self.logger.info('🔄 Dropdown may have closed. Attempting to re-open by re-executing previous step...')
                                reopened = await self._reexecute_menu_opener(previous_item, ai_step_llm)
                                if reopened:
                                    menu_reopened = True
                                    retry_count -= 1
                                    step_delay = 0.5
                                    self.logger.info('🔄 Dropdown re-opened, retrying element match...')
                                    continue
                        if retry_count == max_retries:
                            error_msg = f'{step_name} failed after {max_retries} attempts: {error_str}'
                            self.logger.error(error_msg)
                            results.append(ActionResult(error=error_msg))
                            if not skip_failures:
                                raise RuntimeError(error_msg)
                        else:
                            retry_delay = min(base_retry_delay * 2 ** (retry_count - 1), max_retry_delay)
                            self.logger.warning(f'{step_name} failed (attempt {retry_count}/{max_retries}), retrying in {retry_delay}s...')
                            await asyncio.sleep(retry_delay)
                previous_item = history_item
                previous_step_succeeded = step_succeeded
            self.logger.info('🤖 Generating AI summary of rerun completion...')
            summary_result = await self._generate_rerun_summary(self.task, results, summary_llm)
            results.append(summary_result)
            return results
        finally:
            await self.close()

    async def _execute_initial_actions(self) -> None:
        if self.initial_actions and (not self.state.follow_up_task):
            self.logger.debug(f'⚡ Executing {len(self.initial_actions)} initial actions...')
            result = await self.multi_act(self.initial_actions)
            if result and self.initial_url and result[0].long_term_memory:
                result[0].long_term_memory = f'Found initial url and automatically loaded it. {result[0].long_term_memory}'
            self.state.last_result = result
            if self.settings.flash_mode:
                model_output = self.AgentOutput(evaluation_previous_goal=None, memory='Initial navigation', next_goal=None, action=self.initial_actions)
            else:
                model_output = self.AgentOutput(evaluation_previous_goal='Start', memory=None, next_goal='Initial navigation', action=self.initial_actions)
            metadata = StepMetadata(step_number=0, step_start_time=time.time(), step_end_time=time.time(), step_interval=None)
            state_history = BrowserStateHistory(url=self.initial_url or '', title='Initial Actions', tabs=[], interacted_element=[None] * len(self.initial_actions), screenshot_path=None)
            history_item = AgentHistory(model_output=model_output, result=result, state=state_history, metadata=metadata)
            self.history.add_item(history_item)
            self.logger.debug('📝 Saved initial actions to history as step 0')
            self.logger.debug('Initial actions completed')

    async def _wait_for_minimum_elements(self, min_elements: int, timeout: float=30.0, poll_interval: float=1.0) -> BrowserStateSummary | None:
        assert self.browser_session is not None, 'BrowserSession is not set up'
        start_time = time.time()
        last_count = 0
        while time.time() - start_time < timeout:
            state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
            if state and state.dom_state.selector_map:
                current_count = len(state.dom_state.selector_map)
                if current_count >= min_elements:
                    self.logger.debug(f'✅ Page has {current_count} elements (needed {min_elements}), proceeding with action')
                    return state
                if current_count != last_count:
                    self.logger.debug(f'⏳ Waiting for elements: {current_count}/{min_elements} (timeout in {timeout - (time.time() - start_time):.1f}s)')
                    last_count = current_count
            await asyncio.sleep(poll_interval)
        self.logger.warning(f'⚠️ Timeout waiting for {min_elements} elements, proceeding with {last_count} elements')
        return await self.browser_session.get_browser_state_summary(include_screenshot=False)

    def _count_expected_elements_from_history(self, history_item: AgentHistory) -> int:
        if not history_item.model_output or not history_item.model_output.action:
            return 0
        max_index = -1
        for action in history_item.model_output.action:
            index = action.get_index()
            if index is not None:
                max_index = max(max_index, index)
        return min(max_index + 1, 50) if max_index >= 0 else 0

    async def _execute_history_step(self, history_item: AgentHistory, delay: float, ai_step_llm: BaseChatModel | None=None, wait_for_elements: bool=False) -> list[ActionResult]:
        assert self.browser_session is not None, 'BrowserSession is not set up'
        await asyncio.sleep(delay)
        if wait_for_elements:
            needs_element_matching = False
            if history_item.model_output:
                for i, action in enumerate(history_item.model_output.action):
                    action_data = action.model_dump(exclude_unset=True)
                    action_name = next(iter(action_data.keys()), None)
                    if action_name in ('click', 'input', 'hover', 'select_option', 'drag_and_drop'):
                        historical_elem = history_item.state.interacted_element[i] if i < len(history_item.state.interacted_element) else None
                        if historical_elem is not None:
                            needs_element_matching = True
                            break
            if needs_element_matching:
                min_elements = self._count_expected_elements_from_history(history_item)
                if min_elements > 0:
                    state = await self._wait_for_minimum_elements(min_elements, timeout=15.0, poll_interval=1.0)
                else:
                    state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
            else:
                state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
        else:
            state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
        if not state or not history_item.model_output:
            raise ValueError('Invalid state or model output')
        results = []
        pending_actions = []
        for i, action in enumerate(history_item.model_output.action):
            action_data = action.model_dump(exclude_unset=True)
            action_name = next(iter(action_data.keys()), None)
            if action_name == 'extract':
                if pending_actions:
                    batch_results = await self.multi_act(pending_actions)
                    results.extend(batch_results)
                    pending_actions = []
                extract_params = action_data['extract']
                query = extract_params.get('query', '')
                extract_links = extract_params.get('extract_links', False)
                self.logger.info(f'🤖 Using AI step for extract action: {query[:50]}...')
                ai_result = await self._execute_ai_step(query=query, include_screenshot=False, extract_links=extract_links, ai_step_llm=ai_step_llm)
                results.append(ai_result)
            else:
                historical_elem = history_item.state.interacted_element[i]
                updated_action = await self._update_action_indices(historical_elem, action, state)
                if updated_action is None:
                    elem_info = self._format_element_for_error(historical_elem)
                    selector_map = state.dom_state.selector_map or {}
                    selector_count = len(selector_map)
                    hist_node = historical_elem.node_name.lower() if historical_elem else ''
                    similar_elements = []
                    if historical_elem and historical_elem.attributes:
                        hist_aria = historical_elem.attributes.get('aria-label', '')
                        for idx, elem in selector_map.items():
                            if elem.node_name.lower() == hist_node and elem.attributes:
                                elem_aria = elem.attributes.get('aria-label', '')
                                if elem_aria:
                                    similar_elements.append(f'{idx}:{elem_aria[:30]}')
                                    if len(similar_elements) >= 5:
                                        break
                    diagnostic = ''
                    if similar_elements:
                        diagnostic = f'\n  Available <{hist_node.upper()}> with aria-label: {similar_elements}'
                    elif hist_node:
                        same_node_count = sum((1 for e in selector_map.values() if e.node_name.lower() == hist_node))
                        diagnostic = f'\n  Found {same_node_count} <{hist_node.upper()}> elements (none with matching identifiers)'
                    raise ValueError(f'Could not find matching element for action {i} in current page.\n  Looking for: {elem_info}\n  Page has {selector_count} interactive elements.{diagnostic}\n  Tried: EXACT hash → STABLE hash → XPATH → AX_NAME → ATTRIBUTE matching')
                pending_actions.append(updated_action)
        if pending_actions:
            batch_results = await self.multi_act(pending_actions)
            results.extend(batch_results)
        return results

    async def _update_action_indices(self, historical_element: DOMInteractedElement | None, action: ActionModel, browser_state_summary: BrowserStateSummary) -> ActionModel | None:
        if not historical_element or not browser_state_summary.dom_state.selector_map:
            return action
        selector_map = browser_state_summary.dom_state.selector_map
        highlight_index: int | None = None
        match_level: MatchLevel | None = None
        self.logger.info(f'🔍 Searching for element: <{historical_element.node_name}> hash={historical_element.element_hash} stable_hash={historical_element.stable_hash}')
        if historical_element.node_name:
            hist_name = historical_element.node_name.lower()
            matching_nodes = [(idx, elem.node_name, elem.attributes.get('name') if elem.attributes else None) for idx, elem in selector_map.items() if elem.node_name.lower() == hist_name]
            self.logger.info(f'🔍 Selector map has {len(selector_map)} elements, {len(matching_nodes)} are <{hist_name.upper()}>: {matching_nodes}')
        for idx, elem in selector_map.items():
            if elem.element_hash == historical_element.element_hash:
                highlight_index = idx
                match_level = MatchLevel.EXACT
                break
        if highlight_index is None:
            self.logger.debug(f'EXACT hash match failed (checked {len(selector_map)} elements)')
        if highlight_index is None and historical_element.stable_hash is not None:
            for idx, elem in selector_map.items():
                if elem.compute_stable_hash() == historical_element.stable_hash:
                    highlight_index = idx
                    match_level = MatchLevel.STABLE
                    self.logger.info('Element matched at STABLE level (dynamic classes filtered)')
                    break
            if highlight_index is None:
                self.logger.debug('STABLE hash match failed')
        elif highlight_index is None:
            self.logger.debug('STABLE hash match skipped (no stable_hash in history)')
        if highlight_index is None and historical_element.x_path:
            for idx, elem in selector_map.items():
                if elem.xpath == historical_element.x_path:
                    highlight_index = idx
                    match_level = MatchLevel.XPATH
                    self.logger.info(f'Element matched at XPATH level: {historical_element.x_path}')
                    break
            if highlight_index is None:
                self.logger.debug(f'XPATH match failed for: {historical_element.x_path[-60:]}')
        if highlight_index is None and historical_element.ax_name:
            hist_name = historical_element.node_name.lower()
            hist_ax_name = historical_element.ax_name
            for idx, elem in selector_map.items():
                elem_ax_name = elem.ax_node.name if elem.ax_node else None
                if elem.node_name.lower() == hist_name and elem_ax_name == hist_ax_name:
                    highlight_index = idx
                    match_level = MatchLevel.AX_NAME
                    self.logger.info(f'Element matched at AX_NAME level: "{hist_ax_name}"')
                    break
            if highlight_index is None:
                same_type_ax_names = [(idx, elem.ax_node.name if elem.ax_node else None) for idx, elem in selector_map.items() if elem.node_name.lower() == hist_name and elem.ax_node and elem.ax_node.name]
                self.logger.debug(f'''AX_NAME match failed for <{hist_name.upper()}> ax_name="{hist_ax_name}". Page has {len(same_type_ax_names)} <{hist_name.upper()}> with ax_names: {same_type_ax_names[:5]}{('...' if len(same_type_ax_names) > 5 else '')}''')
        if highlight_index is None and historical_element.attributes:
            hist_attrs = historical_element.attributes
            hist_name = historical_element.node_name.lower()
            for attr_key in ['name', 'id', 'aria-label']:
                if attr_key in hist_attrs and hist_attrs[attr_key]:
                    for idx, elem in selector_map.items():
                        if elem.node_name.lower() == hist_name and elem.attributes and (elem.attributes.get(attr_key) == hist_attrs[attr_key]):
                            highlight_index = idx
                            match_level = MatchLevel.ATTRIBUTE
                            self.logger.info(f'Element matched via {attr_key} attribute: {hist_attrs[attr_key]}')
                            break
                    if highlight_index is not None:
                        break
            if highlight_index is None:
                tried_attrs = [k for k in ['name', 'id', 'aria-label'] if k in hist_attrs and hist_attrs[k]]
                same_node_elements = [(idx, elem.attributes.get('aria-label') or elem.attributes.get('id') or elem.attributes.get('name')) for idx, elem in selector_map.items() if elem.node_name.lower() == hist_name and elem.attributes]
                self.logger.info(f"🔍 ATTRIBUTE match failed for <{hist_name.upper()}> (tried: {tried_attrs}, looking for: {[hist_attrs.get(k) for k in tried_attrs]}). Page has {len(same_node_elements)} <{hist_name.upper()}> elements with identifiers: {same_node_elements[:5]}{('...' if len(same_node_elements) > 5 else '')}")
        if highlight_index is None:
            return None
        old_index = action.get_index()
        if old_index != highlight_index:
            action.set_index(highlight_index)
            level_name = match_level.name if match_level else 'UNKNOWN'
            self.logger.info(f'Element index updated {old_index} → {highlight_index} (matched at {level_name} level)')
        return action

    def _format_element_for_error(self, elem: DOMInteractedElement | None) -> str:
        if elem is None:
            return '<no element recorded>'
        parts = [f'<{elem.node_name}>']
        if elem.attributes:
            for key in ['name', 'id', 'aria-label', 'type']:
                if key in elem.attributes and elem.attributes[key]:
                    parts.append(f'{key}="{elem.attributes[key]}"')
        parts.append(f'hash={elem.element_hash}')
        if elem.stable_hash:
            parts.append(f'stable_hash={elem.stable_hash}')
        if elem.x_path:
            xpath_short = elem.x_path if len(elem.x_path) <= 60 else f'...{elem.x_path[-57:]}'
            parts.append(f'xpath="{xpath_short}"')
        return ' '.join(parts)

    def _is_redundant_retry_step(self, current_item: AgentHistory, previous_item: AgentHistory | None, previous_step_succeeded: bool) -> bool:
        if not previous_item or not previous_step_succeeded:
            return False
        curr_elements = current_item.state.interacted_element
        prev_elements = previous_item.state.interacted_element
        if not curr_elements or not prev_elements:
            return False
        curr_elem = curr_elements[0] if curr_elements else None
        prev_elem = prev_elements[0] if prev_elements else None
        if not curr_elem or not prev_elem:
            return False
        same_by_hash = curr_elem.element_hash == prev_elem.element_hash
        same_by_stable_hash = curr_elem.stable_hash is not None and prev_elem.stable_hash is not None and (curr_elem.stable_hash == prev_elem.stable_hash)
        same_by_xpath = curr_elem.x_path == prev_elem.x_path
        if not (same_by_hash or same_by_stable_hash or same_by_xpath):
            return False
        curr_actions = current_item.model_output.action if current_item.model_output else []
        prev_actions = previous_item.model_output.action if previous_item.model_output else []
        if not curr_actions or not prev_actions:
            return False
        curr_action_data = curr_actions[0].model_dump(exclude_unset=True)
        prev_action_data = prev_actions[0].model_dump(exclude_unset=True)
        curr_action_type = next(iter(curr_action_data.keys()), None)
        prev_action_type = next(iter(prev_action_data.keys()), None)
        if curr_action_type != prev_action_type:
            return False
        self.logger.debug(f'🔄 Detected redundant retry: both steps target same element <{curr_elem.node_name}> with action "{curr_action_type}"')
        return True

    def _is_menu_opener_step(self, history_item: AgentHistory | None) -> bool:
        if not history_item or not history_item.state or (not history_item.state.interacted_element):
            return False
        elem = history_item.state.interacted_element[0] if history_item.state.interacted_element else None
        if not elem:
            return False
        attrs = elem.attributes or {}
        if attrs.get('aria-haspopup') in ('true', 'menu', 'listbox'):
            return True
        if attrs.get('data-gw-click') == 'toggleSubMenu':
            return True
        if 'expand-button' in attrs.get('class', ''):
            return True
        if attrs.get('role') == 'menuitem' and attrs.get('aria-expanded') in ('false', 'true'):
            return True
        if attrs.get('role') == 'button' and attrs.get('aria-expanded') in ('false', 'true'):
            return True
        return False

    def _is_menu_item_element(self, elem: 'DOMInteractedElement | None') -> bool:
        if not elem:
            return False
        attrs = elem.attributes or {}
        role = attrs.get('role', '')
        if role in ('menuitem', 'option', 'menuitemcheckbox', 'menuitemradio', 'treeitem'):
            return True
        if 'gw-action--inner' in attrs.get('class', ''):
            return True
        if 'menuitem' in attrs.get('class', '').lower():
            return True
        if elem.ax_name and elem.ax_name not in ('', None):
            elem_class = attrs.get('class', '').lower()
            if any((x in elem_class for x in ['dropdown', 'popup', 'menu', 'submenu', 'action'])):
                return True
        return False

    async def _reexecute_menu_opener(self, opener_item: AgentHistory, ai_step_llm: 'BaseChatModel | None'=None) -> bool:
        try:
            self.logger.info('🔄 Re-opening dropdown/menu by re-executing previous step...')
            await self._execute_history_step(opener_item, delay=0.5, ai_step_llm=ai_step_llm, wait_for_elements=False)
            await asyncio.sleep(0.3)
            return True
        except Exception as e:
            self.logger.warning(f'Failed to re-open dropdown: {e}')
            return False

    async def load_and_rerun(self, history_file: str | Path | None=None, variables: dict[str, str] | None=None, **kwargs) -> list[ActionResult]:
        if not history_file:
            history_file = 'AgentHistory.json'
        history = AgentHistoryList.load_from_file(history_file, self.AgentOutput)
        if variables:
            history = self._substitute_variables_in_history(history, variables)
        return await self.rerun_history(history, **kwargs)

    def save_history(self, file_path: str | Path | None=None) -> None:
        if not file_path:
            file_path = 'AgentHistory.json'
        self.history.save_to_file(file_path, sensitive_data=self.sensitive_data)

    def pause(self) -> None:
        print('\n\n⏸️ Paused the agent and left the browser open.\n\tPress [Enter] to resume or [Ctrl+C] again to quit.')
        self.state.paused = True
        self._external_pause_event.clear()

    def resume(self) -> None:
        print('----------------------------------------------------------------------')
        print('▶️  Resuming agent execution where it left off...\n')
        self.state.paused = False
        self._external_pause_event.set()

    def stop(self) -> None:
        self.logger.info('⏹️ Agent stopping')
        self.state.stopped = True
        self._external_pause_event.set()

    def _convert_initial_actions(self, actions: list[dict[str, dict[str, Any]]]) -> list[ActionModel]:
        converted_actions = []
        action_model = self.ActionModel
        for action_dict in actions:
            action_name = next(iter(action_dict))
            params = action_dict[action_name]
            action_info = self.tools.registry.registry.actions[action_name]
            param_model = action_info.param_model
            validated_params = param_model(**params)
            action_model = self.ActionModel(**{action_name: validated_params})
            converted_actions.append(action_model)
        return converted_actions

    def _verify_and_setup_llm(self):
        if getattr(self.llm, '_verified_api_keys', None) is True or CONFIG.SKIP_LLM_API_KEY_VERIFICATION:
            setattr(self.llm, '_verified_api_keys', True)
            return True

    @property
    def message_manager(self) -> MessageManager:
        return self._message_manager

    async def close(self):
        try:
            if self.browser_session is not None and (not self.injected_browser_session):
                if not self.browser_session.browser_profile.keep_alive:
                    await self.browser_session.kill()
                else:
                    await self.browser_session.event_bus.stop(clear=False, timeout=_get_timeout('TIMEOUT_BrowserSessionEventBusStopOnAgentClose', 1.0))
                    try:
                        self.browser_session.event_bus.event_queue = None
                        self.browser_session.event_bus._on_idle = None
                    except Exception:
                        pass
            if self.skill_service is not None:
                await self.skill_service.close()
            gc.collect()
            import threading
            threads = threading.enumerate()
            self.logger.debug(f'🧵 Remaining threads ({len(threads)}): {[t.name for t in threads]}')
            tasks = asyncio.all_tasks(asyncio.get_event_loop())
            other_tasks = [t for t in tasks if t != asyncio.current_task()]
            if other_tasks:
                self.logger.debug(f'⚡ Remaining asyncio tasks ({len(other_tasks)}):')
                for task in other_tasks[:10]:
                    self.logger.debug(f'  - {task.get_name()}: {task}')
        except Exception as e:
            self.logger.error(f'Error during cleanup: {e}')

    async def _update_action_models_for_page(self, page_url: str) -> None:
        self.ActionModel = self.tools.registry.create_action_model(page_url=page_url)
        if self.settings.flash_mode:
            self.AgentOutput = AgentOutput.type_with_custom_actions_flash_mode(self.ActionModel)
        elif self.settings.use_thinking:
            self.AgentOutput = AgentOutput.type_with_custom_actions(self.ActionModel)
        else:
            self.AgentOutput = AgentOutput.type_with_custom_actions_no_thinking(self.ActionModel)
        self.DoneActionModel = self.tools.registry.create_action_model(include_actions=['done'], page_url=page_url)
        if self.settings.flash_mode:
            self.DoneAgentOutput = AgentOutput.type_with_custom_actions_flash_mode(self.DoneActionModel)
        elif self.settings.use_thinking:
            self.DoneAgentOutput = AgentOutput.type_with_custom_actions(self.DoneActionModel)
        else:
            self.DoneAgentOutput = AgentOutput.type_with_custom_actions_no_thinking(self.DoneActionModel)

    async def authenticate_cloud_sync(self, show_instructions: bool=True) -> bool:
        self.logger.warning('Cloud sync has been removed and is no longer available')
        return False

    def run_sync(self, max_steps: int=500, on_step_start: AgentHookFunc | None=None, on_step_end: AgentHookFunc | None=None) -> AgentHistoryList[AgentStructuredOutput]:
        import asyncio
        return asyncio.run(self.run(max_steps=max_steps, on_step_start=on_step_start, on_step_end=on_step_end))

    def detect_variables(self) -> dict[str, DetectedVariable]:
        from system.agent.variable_detector import detect_variables_in_history
        return detect_variables_in_history(self.history)

    def _substitute_variables_in_history(self, history: AgentHistoryList, variables: dict[str, str]) -> AgentHistoryList:
        from system.agent.variable_detector import detect_variables_in_history
        detected_vars = detect_variables_in_history(history)
        value_replacements: dict[str, str] = {}
        for var_name, new_value in variables.items():
            if var_name in detected_vars:
                old_value = detected_vars[var_name].original_value
                value_replacements[old_value] = new_value
            else:
                self.logger.warning(f'Variable "{var_name}" not found in history, skipping substitution')
        if not value_replacements:
            self.logger.info('No variables to substitute')
            return history
        import copy
        modified_history = copy.deepcopy(history)
        substitution_count = 0
        for history_item in modified_history.history:
            if not history_item.model_output or not history_item.model_output.action:
                continue
            for action in history_item.model_output.action:
                if hasattr(action, 'model_dump'):
                    action_dict = action.model_dump()
                elif isinstance(action, dict):
                    action_dict = action
                else:
                    action_dict = vars(action) if hasattr(action, '__dict__') else {}
                substitution_count += self._substitute_in_dict(action_dict, value_replacements)
                if hasattr(action, 'model_dump'):
                    if hasattr(action, 'root'):
                        new_action = type(action).model_validate(action_dict)
                        object.__setattr__(action, 'root', getattr(new_action, 'root'))
                    else:
                        for key, val in action_dict.items():
                            if hasattr(action, key):
                                setattr(action, key, val)
                elif isinstance(action, dict):
                    action.update(action_dict)
        self.logger.info(f'Substituted {substitution_count} value(s) in {len(value_replacements)} variable type(s) in history')
        return modified_history

    def _substitute_in_dict(self, data: dict, replacements: dict[str, str]) -> int:
        count = 0
        for key, value in data.items():
            if isinstance(value, str):
                if value in replacements:
                    data[key] = replacements[value]
                    count += 1
            elif isinstance(value, dict):
                count += self._substitute_in_dict(value, replacements)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, str) and item in replacements:
                        value[i] = replacements[item]
                        count += 1
                    elif isinstance(item, dict):
                        count += self._substitute_in_dict(item, replacements)
        return count