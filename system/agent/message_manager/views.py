from __future__ import annotations
from typing import TYPE_CHECKING, Any
from pydantic import BaseModel, ConfigDict, Field
from system.llm.messages import BaseMessage
if TYPE_CHECKING:
    pass

class HistoryItem(BaseModel):
    step_number: int | None = None
    evaluation_previous_goal: str | None = None
    memory: str | None = None
    next_goal: str | None = None
    action_results: str | None = None
    error: str | None = None
    system_message: str | None = None
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def model_post_init(self, __context) -> None:
        if self.error is not None and self.system_message is not None:
            raise ValueError('Cannot have both error and system_message at the same time')

    def to_string(self) -> str:
        step_str = 'step' if self.step_number is not None else 'step_unknown'
        if self.error:
            return f'<{step_str}>\n{self.error}'
        elif self.system_message:
            return self.system_message
        else:
            content_parts = []
            if self.evaluation_previous_goal:
                content_parts.append(f'{self.evaluation_previous_goal}')
            if self.memory:
                content_parts.append(f'{self.memory}')
            if self.next_goal:
                content_parts.append(f'{self.next_goal}')
            if self.action_results:
                content_parts.append(self.action_results)
            content = '\n'.join(content_parts)
            return f'<{step_str}>\n{content}'

class MessageHistory(BaseModel):
    system_message: BaseMessage | None = None
    state_message: BaseMessage | None = None
    context_messages: list[BaseMessage] = Field(default_factory=list)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_messages(self) -> list[BaseMessage]:
        messages = []
        if self.system_message:
            messages.append(self.system_message)
        if self.state_message:
            messages.append(self.state_message)
        messages.extend(self.context_messages)
        return messages

class MessageManagerState(BaseModel):
    history: MessageHistory = Field(default_factory=MessageHistory)
    tool_id: int = 1
    agent_history_items: list[HistoryItem] = Field(default_factory=lambda: [HistoryItem(step_number=0, system_message='Agent initialized')])
    read_state_description: str = ''
    read_state_images: list[dict[str, Any]] = Field(default_factory=list)
    compacted_memory: str | None = None
    compaction_count: int = 0
    last_compaction_step: int | None = None
    model_config = ConfigDict(arbitrary_types_allowed=True)