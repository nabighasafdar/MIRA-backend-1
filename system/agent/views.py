from __future__ import annotations
import hashlib
import json
import logging
import re
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, model_validator
from typing_extensions import TypeVar
from uuid_extensions import uuid7str
from system.agent.message_manager.views import MessageManagerState
from system.browser.views import BrowserStateHistory
from system.dom.views import DEFAULT_INCLUDE_ATTRIBUTES, DOMInteractedElement, DOMSelectorMap
from system.filesystem.file_system import FileSystemState
from system.llm.base import BaseChatModel
from system.tokens.views import UsageSummary
from system.tools.registry.views import ActionModel
logger = logging.getLogger(__name__)

class MessageCompactionSettings(BaseModel):
    enabled: bool = True
    compact_every_n_steps: int = 15
    trigger_char_count: int | None = None
    trigger_token_count: int | None = None
    chars_per_token: float = 4.0
    keep_last_items: int = 6
    summary_max_chars: int = 6000
    include_read_state: bool = False
    compaction_llm: BaseChatModel | None = None

    @model_validator(mode='after')
    def _resolve_trigger_threshold(self) -> MessageCompactionSettings:
        if self.trigger_char_count is not None and self.trigger_token_count is not None:
            raise ValueError('Set trigger_char_count or trigger_token_count, not both.')
        if self.trigger_token_count is not None:
            self.trigger_char_count = int(self.trigger_token_count * self.chars_per_token)
        elif self.trigger_char_count is None:
            self.trigger_char_count = 40000
        return self

class AgentSettings(BaseModel):
    use_vision: bool | Literal['auto'] = True
    vision_detail_level: Literal['auto', 'low', 'high'] = 'auto'
    save_conversation_path: str | Path | None = None
    save_conversation_path_encoding: str | None = 'utf-8'
    max_failures: int = 5
    override_system_message: str | None = None
    extend_system_message: str | None = None
    include_attributes: list[str] | None = DEFAULT_INCLUDE_ATTRIBUTES
    max_actions_per_step: int = 5
    use_thinking: bool = True
    flash_mode: bool = False
    use_judge: bool = True
    ground_truth: str | None = None
    max_history_items: int | None = None
    message_compaction: MessageCompactionSettings | None = None
    enable_planning: bool = True
    planning_replan_on_stall: int = 3
    planning_exploration_limit: int = 5
    page_extraction_llm: BaseChatModel | None = None
    calculate_cost: bool = False
    include_tool_call_examples: bool = False
    llm_timeout: int = 60
    step_timeout: int = 180
    final_response_after_failure: bool = True
    loop_detection_window: int = 20
    loop_detection_enabled: bool = True
    max_clickable_elements_length: int = 40000

class PageFingerprint(BaseModel):
    model_config = ConfigDict(frozen=True)
    url: str
    element_count: int
    text_hash: str

    @staticmethod
    def from_browser_state(url: str, dom_text: str, element_count: int) -> PageFingerprint:
        text_hash = hashlib.sha256(dom_text.encode('utf-8', errors='replace')).hexdigest()[:16]
        return PageFingerprint(url=url, element_count=element_count, text_hash=text_hash)

def _normalize_action_for_hash(action_name: str, params: dict[str, Any]) -> str:
    if action_name == 'search':
        query = str(params.get('query', ''))
        tokens = sorted(set(re.sub('[^\\w\\s]', ' ', query.lower()).split()))
        engine = params.get('engine', 'google')
        return f"search|{engine}|{'|'.join(tokens)}"
    if action_name in ('click', 'input'):
        index = params.get('index')
        if action_name == 'input':
            text = str(params.get('text', ''))
            return f'input|{index}|{text.strip().lower()}'
        return f'click|{index}'
    if action_name == 'navigate':
        url = str(params.get('url', ''))
        return f'navigate|{url}'
    if action_name == 'scroll':
        direction = 'down' if params.get('down', True) else 'up'
        index = params.get('index')
        return f'scroll|{direction}|{index}'
    filtered = {k: v for k, v in sorted(params.items()) if v is not None}
    return f'{action_name}|{json.dumps(filtered, sort_keys=True, default=str)}'

def compute_action_hash(action_name: str, params: dict[str, Any]) -> str:
    normalized = _normalize_action_for_hash(action_name, params)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]

class ActionLoopDetector(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    window_size: int = 20
    recent_action_hashes: list[str] = Field(default_factory=list)
    recent_page_fingerprints: list[PageFingerprint] = Field(default_factory=list)
    max_repetition_count: int = 0
    most_repeated_hash: str | None = None
    consecutive_stagnant_pages: int = 0

    def record_action(self, action_name: str, params: dict[str, Any]) -> None:
        h = compute_action_hash(action_name, params)
        self.recent_action_hashes.append(h)
        if len(self.recent_action_hashes) > self.window_size:
            self.recent_action_hashes = self.recent_action_hashes[-self.window_size:]
        self._update_repetition_stats()

    def record_page_state(self, url: str, dom_text: str, element_count: int) -> None:
        fp = PageFingerprint.from_browser_state(url, dom_text, element_count)
        if self.recent_page_fingerprints and self.recent_page_fingerprints[-1] == fp:
            self.consecutive_stagnant_pages += 1
        else:
            self.consecutive_stagnant_pages = 0
        self.recent_page_fingerprints.append(fp)
        if len(self.recent_page_fingerprints) > 5:
            self.recent_page_fingerprints = self.recent_page_fingerprints[-5:]

    def _update_repetition_stats(self) -> None:
        if not self.recent_action_hashes:
            self.max_repetition_count = 0
            self.most_repeated_hash = None
            return
        counts: dict[str, int] = {}
        for h in self.recent_action_hashes:
            counts[h] = counts.get(h, 0) + 1
        self.most_repeated_hash = max(counts, key=lambda k: counts[k])
        self.max_repetition_count = counts[self.most_repeated_hash]

    def get_nudge_message(self) -> str | None:
        messages: list[str] = []
        if self.max_repetition_count >= 12:
            messages.append(f'Heads up: you have repeated a similar action {self.max_repetition_count} times in the last {len(self.recent_action_hashes)} actions. If you are making progress with each repetition, keep going. If not, a different approach might get you there faster.')
        elif self.max_repetition_count >= 8:
            messages.append(f'Heads up: you have repeated a similar action {self.max_repetition_count} times in the last {len(self.recent_action_hashes)} actions. Are you still making progress with each attempt? If so, carry on. Otherwise, it might be worth trying a different approach.')
        elif self.max_repetition_count >= 5:
            messages.append(f'Heads up: you have repeated a similar action {self.max_repetition_count} times in the last {len(self.recent_action_hashes)} actions. If this is intentional and making progress, carry on. If not, it might be worth reconsidering your approach.')
        if self.consecutive_stagnant_pages >= 5:
            messages.append(f'The page content has not changed across {self.consecutive_stagnant_pages} consecutive actions. Your actions might not be having the intended effect. It could be worth trying a different element or approach.')
        if messages:
            return '\n\n'.join(messages)
        return None

class AgentState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    agent_id: str = Field(default_factory=uuid7str)
    n_steps: int = 1
    consecutive_failures: int = 0
    last_result: list[ActionResult] | None = None
    plan: list[PlanItem] | None = None
    current_plan_item_index: int = 0
    plan_generation_step: int | None = None
    last_model_output: AgentOutput | None = None
    paused: bool = False
    stopped: bool = False
    session_initialized: bool = False
    follow_up_task: bool = False
    message_manager_state: MessageManagerState = Field(default_factory=MessageManagerState)
    file_system_state: FileSystemState | None = None
    loop_detector: ActionLoopDetector = Field(default_factory=ActionLoopDetector)

@dataclass
class AgentStepInfo:
    step_number: int
    max_steps: int

    def is_last_step(self) -> bool:
        return self.step_number >= self.max_steps - 1

class JudgementResult(BaseModel):
    reasoning: str | None = Field(default=None, description='Explanation of the judgement')
    verdict: bool = Field(description='Whether the trace was successful or not')
    failure_reason: str | None = Field(default=None, description='Max 5 sentences explanation of why the task was not completed successfully in case of failure. If verdict is true, use an empty string.')
    impossible_task: bool = Field(default=False, description='True if the task was impossible to complete due to vague instructions, broken website, inaccessible links, missing login credentials, or other insurmountable obstacles')
    reached_captcha: bool = Field(default=False, description='True if the agent encountered captcha challenges during task execution')

class ActionResult(BaseModel):
    is_done: bool | None = False
    success: bool | None = None
    judgement: JudgementResult | None = None
    error: str | None = None
    attachments: list[str] | None = None
    images: list[dict[str, Any]] | None = None
    long_term_memory: str | None = None
    extracted_content: str | None = None
    include_extracted_content_only_once: bool = False
    metadata: dict | None = None
    include_in_memory: bool = False

    @model_validator(mode='after')
    def validate_success_requires_done(self):
        if self.success is True and self.is_done is not True:
            raise ValueError('success=True can only be set when is_done=True. For regular actions that succeed, leave success as None. Use success=False only for actions that fail.')
        return self

class RerunSummaryAction(BaseModel):
    summary: str = Field(description='Summary of what happened during the rerun')
    success: bool = Field(description='Whether the rerun completed successfully based on visual inspection')
    completion_status: Literal['complete', 'partial', 'failed'] = Field(description='Status of rerun completion: complete (all steps succeeded), partial (some steps succeeded), failed (task did not complete)')

class StepMetadata(BaseModel):
    step_start_time: float
    step_end_time: float
    step_number: int
    step_interval: float | None = None

    @property
    def duration_seconds(self) -> float:
        return self.step_end_time - self.step_start_time

class PlanItem(BaseModel):
    text: str
    status: Literal['pending', 'current', 'done', 'skipped'] = 'pending'

class AgentBrain(BaseModel):
    thinking: str | None = None
    evaluation_previous_goal: str
    memory: str
    next_goal: str

class AgentOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra='forbid')
    thinking: str | None = None
    evaluation_previous_goal: str | None = None
    memory: str | None = None
    next_goal: str | None = None
    current_plan_item: int | None = None
    plan_update: list[str] | None = None
    action: list[ActionModel] = Field(..., json_schema_extra={'min_items': 1})

    @classmethod
    def model_json_schema(cls, **kwargs):
        schema = super().model_json_schema(**kwargs)
        schema['required'] = ['evaluation_previous_goal', 'memory', 'next_goal', 'action']
        return schema

    @property
    def current_state(self) -> AgentBrain:
        return AgentBrain(thinking=self.thinking, evaluation_previous_goal=self.evaluation_previous_goal if self.evaluation_previous_goal else '', memory=self.memory if self.memory else '', next_goal=self.next_goal if self.next_goal else '')

    @staticmethod
    def type_with_custom_actions(custom_actions: type[ActionModel]) -> type[AgentOutput]:
        model_ = create_model('AgentOutput', __base__=AgentOutput, action=(list[custom_actions], Field(..., description='List of actions to execute', json_schema_extra={'min_items': 1})), __module__=AgentOutput.__module__)
        return model_

    @staticmethod
    def type_with_custom_actions_no_thinking(custom_actions: type[ActionModel]) -> type[AgentOutput]:

        class AgentOutputNoThinking(AgentOutput):

            @classmethod
            def model_json_schema(cls, **kwargs):
                schema = super().model_json_schema(**kwargs)
                del schema['properties']['thinking']
                schema['required'] = ['evaluation_previous_goal', 'memory', 'next_goal', 'action']
                return schema
        model = create_model('AgentOutput', __base__=AgentOutputNoThinking, action=(list[custom_actions], Field(..., json_schema_extra={'min_items': 1})), __module__=AgentOutputNoThinking.__module__)
        return model

    @staticmethod
    def type_with_custom_actions_flash_mode(custom_actions: type[ActionModel]) -> type[AgentOutput]:

        class AgentOutputFlashMode(AgentOutput):

            @classmethod
            def model_json_schema(cls, **kwargs):
                schema = super().model_json_schema(**kwargs)
                del schema['properties']['thinking']
                del schema['properties']['evaluation_previous_goal']
                del schema['properties']['next_goal']
                schema['properties'].pop('current_plan_item', None)
                schema['properties'].pop('plan_update', None)
                schema['required'] = ['memory', 'action']
                return schema
        model = create_model('AgentOutput', __base__=AgentOutputFlashMode, action=(list[custom_actions], Field(..., json_schema_extra={'min_items': 1})), __module__=AgentOutputFlashMode.__module__)
        return model

class AgentHistory(BaseModel):
    model_output: AgentOutput | None
    result: list[ActionResult]
    state: BrowserStateHistory
    metadata: StepMetadata | None = None
    state_message: str | None = None
    model_config = ConfigDict(arbitrary_types_allowed=True, protected_namespaces=())

    @staticmethod
    def get_interacted_element(model_output: AgentOutput, selector_map: DOMSelectorMap) -> list[DOMInteractedElement | None]:
        elements = []
        for action in model_output.action:
            index = action.get_index()
            if index is not None and index in selector_map:
                el = selector_map[index]
                elements.append(DOMInteractedElement.load_from_enhanced_dom_tree(el))
            else:
                elements.append(None)
        return elements

    def _filter_sensitive_data_from_string(self, value: str, sensitive_data: dict[str, str | dict[str, str]] | None) -> str:
        if not sensitive_data:
            return value
        sensitive_values: dict[str, str] = {}
        for key_or_domain, content in sensitive_data.items():
            if isinstance(content, dict):
                for key, val in content.items():
                    if val:
                        sensitive_values[key] = val
            elif content:
                sensitive_values[key_or_domain] = content
        if not sensitive_values:
            return value
        for key, val in sensitive_values.items():
            value = value.replace(val, f'<secret>{key}</secret>')
        return value

    def _filter_sensitive_data_from_dict(self, data: dict[str, Any], sensitive_data: dict[str, str | dict[str, str]] | None) -> dict[str, Any]:
        if not sensitive_data:
            return data
        filtered_data = {}
        for key, value in data.items():
            if isinstance(value, str):
                filtered_data[key] = self._filter_sensitive_data_from_string(value, sensitive_data)
            elif isinstance(value, dict):
                filtered_data[key] = self._filter_sensitive_data_from_dict(value, sensitive_data)
            elif isinstance(value, list):
                filtered_data[key] = [self._filter_sensitive_data_from_string(item, sensitive_data) if isinstance(item, str) else self._filter_sensitive_data_from_dict(item, sensitive_data) if isinstance(item, dict) else item for item in value]
            else:
                filtered_data[key] = value
        return filtered_data

    def model_dump(self, sensitive_data: dict[str, str | dict[str, str]] | None=None, **kwargs) -> dict[str, Any]:
        model_output_dump = None
        if self.model_output:
            action_dump = [action.model_dump(exclude_none=True, mode='json') for action in self.model_output.action]
            if sensitive_data:
                action_dump = [self._filter_sensitive_data_from_dict(action, sensitive_data) if 'input' in action else action for action in action_dump]
            model_output_dump = {'evaluation_previous_goal': self.model_output.evaluation_previous_goal, 'memory': self.model_output.memory, 'next_goal': self.model_output.next_goal, 'action': action_dump}
            if self.model_output.thinking is not None:
                model_output_dump['thinking'] = self.model_output.thinking
            if self.model_output.current_plan_item is not None:
                model_output_dump['current_plan_item'] = self.model_output.current_plan_item
            if self.model_output.plan_update is not None:
                model_output_dump['plan_update'] = self.model_output.plan_update
        result_dump = [r.model_dump(exclude_none=True, mode='json') for r in self.result]
        return {'model_output': model_output_dump, 'result': result_dump, 'state': self.state.to_dict(), 'metadata': self.metadata.model_dump() if self.metadata else None, 'state_message': self.state_message}
AgentStructuredOutput = TypeVar('AgentStructuredOutput', bound=BaseModel)

class AgentHistoryList(BaseModel, Generic[AgentStructuredOutput]):
    history: list[AgentHistory]
    usage: UsageSummary | None = None
    _output_model_schema: type[AgentStructuredOutput] | None = None

    def total_duration_seconds(self) -> float:
        total = 0.0
        for h in self.history:
            if h.metadata:
                total += h.metadata.duration_seconds
        return total

    def __len__(self) -> int:
        return len(self.history)

    def __str__(self) -> str:
        return f'AgentHistoryList(all_results={self.action_results()}, all_model_outputs={self.model_actions()})'

    def add_item(self, history_item: AgentHistory) -> None:
        self.history.append(history_item)

    def __repr__(self) -> str:
        return self.__str__()

    def save_to_file(self, filepath: str | Path, sensitive_data: dict[str, str | dict[str, str]] | None=None) -> None:
        try:
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            data = self.model_dump(sensitive_data=sensitive_data)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            raise e

    def model_dump(self, **kwargs) -> dict[str, Any]:
        return {'history': [h.model_dump(**kwargs) for h in self.history]}

    @classmethod
    def load_from_dict(cls, data: dict[str, Any], output_model: type[AgentOutput]) -> AgentHistoryList:
        for h in data['history']:
            if h['model_output']:
                if isinstance(h['model_output'], dict):
                    h['model_output'] = output_model.model_validate(h['model_output'])
                else:
                    h['model_output'] = None
            if 'interacted_element' not in h['state']:
                h['state']['interacted_element'] = None
        history = cls.model_validate(data)
        return history

    @classmethod
    def load_from_file(cls, filepath: str | Path, output_model: type[AgentOutput]) -> AgentHistoryList:
        with open(filepath, encoding='utf-8') as f:
            data = json.load(f)
        return cls.load_from_dict(data, output_model)

    def last_action(self) -> None | dict:
        if self.history and self.history[-1].model_output:
            return self.history[-1].model_output.action[-1].model_dump(exclude_none=True, mode='json')
        return None

    def errors(self) -> list[str | None]:
        errors = []
        for h in self.history:
            step_errors = [r.error for r in h.result if r.error]
            errors.append(step_errors[0] if step_errors else None)
        return errors

    def final_result(self) -> None | str:
        if self.history and self.history[-1].result[-1].extracted_content:
            return self.history[-1].result[-1].extracted_content
        return None

    def is_done(self) -> bool:
        if self.history and len(self.history[-1].result) > 0:
            last_result = self.history[-1].result[-1]
            return last_result.is_done is True
        return False

    def is_successful(self) -> bool | None:
        if self.history and len(self.history[-1].result) > 0:
            last_result = self.history[-1].result[-1]
            if last_result.is_done is True:
                return last_result.success
        return None

    def has_errors(self) -> bool:
        return any((error is not None for error in self.errors()))

    def judgement(self) -> dict | None:
        if self.history and len(self.history[-1].result) > 0:
            last_result = self.history[-1].result[-1]
            if last_result.judgement:
                return last_result.judgement.model_dump()
        return None

    def is_judged(self) -> bool:
        if self.history and len(self.history[-1].result) > 0:
            last_result = self.history[-1].result[-1]
            return last_result.judgement is not None
        return False

    def is_validated(self) -> bool | None:
        if self.history and len(self.history[-1].result) > 0:
            last_result = self.history[-1].result[-1]
            if last_result.judgement:
                return last_result.judgement.verdict
        return None

    def urls(self) -> list[str | None]:
        return [h.state.url if h.state.url is not None else None for h in self.history]

    def screenshot_paths(self, n_last: int | None=None, return_none_if_not_screenshot: bool=True) -> list[str | None]:
        if n_last == 0:
            return []
        if n_last is None:
            if return_none_if_not_screenshot:
                return [h.state.screenshot_path if h.state.screenshot_path is not None else None for h in self.history]
            else:
                return [h.state.screenshot_path for h in self.history if h.state.screenshot_path is not None]
        elif return_none_if_not_screenshot:
            return [h.state.screenshot_path if h.state.screenshot_path is not None else None for h in self.history[-n_last:]]
        else:
            return [h.state.screenshot_path for h in self.history[-n_last:] if h.state.screenshot_path is not None]

    def screenshots(self, n_last: int | None=None, return_none_if_not_screenshot: bool=True) -> list[str | None]:
        if n_last == 0:
            return []
        history_items = self.history if n_last is None else self.history[-n_last:]
        screenshots = []
        for item in history_items:
            screenshot_b64 = item.state.get_screenshot()
            if screenshot_b64:
                screenshots.append(screenshot_b64)
            elif return_none_if_not_screenshot:
                screenshots.append(None)
        return screenshots

    def action_names(self) -> list[str]:
        action_names = []
        for action in self.model_actions():
            actions = list(action.keys())
            if actions:
                action_names.append(actions[0])
        return action_names

    def model_thoughts(self) -> list[AgentBrain]:
        return [h.model_output.current_state for h in self.history if h.model_output]

    def model_outputs(self) -> list[AgentOutput]:
        return [h.model_output for h in self.history if h.model_output]

    def model_actions(self) -> list[dict]:
        outputs = []
        for h in self.history:
            if h.model_output:
                interacted_elements = h.state.interacted_element or [None] * len(h.model_output.action)
                for action, interacted_element in zip(h.model_output.action, interacted_elements):
                    output = action.model_dump(exclude_none=True, mode='json')
                    output['interacted_element'] = interacted_element
                    outputs.append(output)
        return outputs

    def action_history(self) -> list[list[dict]]:
        step_outputs = []
        for h in self.history:
            step_actions = []
            if h.model_output:
                interacted_elements = h.state.interacted_element or [None] * len(h.model_output.action)
                for action, interacted_element, result in zip(h.model_output.action, interacted_elements, h.result):
                    action_output = action.model_dump(exclude_none=True, mode='json')
                    action_output['interacted_element'] = interacted_element
                    action_output['result'] = result.long_term_memory if result and result.long_term_memory else None
                    step_actions.append(action_output)
            step_outputs.append(step_actions)
        return step_outputs

    def action_results(self) -> list[ActionResult]:
        results = []
        for h in self.history:
            results.extend([r for r in h.result if r])
        return results

    def extracted_content(self) -> list[str]:
        content = []
        for h in self.history:
            content.extend([r.extracted_content for r in h.result if r.extracted_content])
        return content

    def model_actions_filtered(self, include: list[str] | None=None) -> list[dict]:
        if include is None:
            include = []
        outputs = self.model_actions()
        result = []
        for o in outputs:
            for i in include:
                if i == list(o.keys())[0]:
                    result.append(o)
        return result

    def number_of_steps(self) -> int:
        return len(self.history)

    def agent_steps(self) -> list[str]:
        steps = []
        for i, h in enumerate(self.history):
            step_text = f'Step {i + 1}:\n'
            if h.model_output and h.model_output.action:
                actions_list = [action.model_dump(exclude_none=True, mode='json') for action in h.model_output.action]
                action_json = json.dumps(actions_list, indent=1)
                step_text += f'Actions: {action_json}\n'
            if h.result:
                for j, result in enumerate(h.result):
                    if result.extracted_content:
                        content = str(result.extracted_content)
                        step_text += f'Result {j + 1}: {content}\n'
                    if result.error:
                        error = str(result.error)
                        step_text += f'Error {j + 1}: {error}\n'
            steps.append(step_text)
        return steps

    @property
    def structured_output(self) -> AgentStructuredOutput | None:
        final_result = self.final_result()
        if final_result is not None and self._output_model_schema is not None:
            return self._output_model_schema.model_validate_json(final_result)
        return None

    def get_structured_output(self, output_model: type[AgentStructuredOutput]) -> AgentStructuredOutput | None:
        final_result = self.final_result()
        if final_result is not None:
            return output_model.model_validate_json(final_result)
        return None

class AgentError:
    VALIDATION_ERROR = 'Invalid model output format. Please follow the correct schema.'
    RATE_LIMIT_ERROR = 'Rate limit reached. Waiting before retry.'
    NO_VALID_ACTION = 'No valid action found'

    @staticmethod
    def format_error(error: Exception, include_trace: bool=False) -> str:
        message = ''
        if isinstance(error, ValidationError):
            return f'{AgentError.VALIDATION_ERROR}\nDetails: {str(error)}'
        from openai import RateLimitError
        if isinstance(error, RateLimitError):
            return AgentError.RATE_LIMIT_ERROR
        error_str = str(error)
        if 'LLM response missing required fields' in error_str or 'Expected format: AgentOutput' in error_str:
            lines = error_str.split('\n')
            main_error = lines[0] if lines else error_str
            helpful_msg = f'{main_error}\n\nThe previous response had an invalid output structure. Please stick to the required output format. \n\n'
            if include_trace:
                helpful_msg += f'\n\nFull stacktrace:\n{traceback.format_exc()}'
            return helpful_msg
        if include_trace:
            return f'{str(error)}\nStacktrace:\n{traceback.format_exc()}'
        return f'{str(error)}'

class DetectedVariable(BaseModel):
    name: str
    original_value: str
    type: str = 'string'
    format: str | None = None

class VariableMetadata(BaseModel):
    detected_variables: dict[str, DetectedVariable] = Field(default_factory=dict)