from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal
from system.config import CONFIG

@dataclass
class BaseTelemetryEvent(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    def properties(self) -> dict[str, Any]:
        props = {k: v for k, v in asdict(self).items() if k != 'name'}
        props['is_docker'] = CONFIG.IN_DOCKER
        return props

@dataclass
class AgentTelemetryEvent(BaseTelemetryEvent):
    task: str
    model: str
    model_provider: str
    max_steps: int
    max_actions_per_step: int
    use_vision: bool | Literal['auto']
    version: str
    source: str
    cdp_url: str | None
    agent_type: str | None
    action_errors: Sequence[str | None]
    action_history: Sequence[list[dict] | None]
    urls_visited: Sequence[str | None]
    steps: int
    total_input_tokens: int
    total_output_tokens: int
    prompt_cached_tokens: int
    total_tokens: int
    total_duration_seconds: float
    success: bool | None
    final_result_response: str | None
    error_message: str | None
    judge_verdict: bool | None = None
    judge_reasoning: str | None = None
    judge_failure_reason: str | None = None
    judge_reached_captcha: bool | None = None
    judge_impossible_task: bool | None = None
    name: str = 'agent_event'

@dataclass
class MCPClientTelemetryEvent(BaseTelemetryEvent):
    server_name: str
    command: str
    tools_discovered: int
    version: str
    action: str
    tool_name: str | None = None
    duration_seconds: float | None = None
    error_message: str | None = None
    name: str = 'mcp_client_event'

@dataclass
class MCPServerTelemetryEvent(BaseTelemetryEvent):
    version: str
    action: str
    tool_name: str | None = None
    duration_seconds: float | None = None
    error_message: str | None = None
    parent_process_cmdline: str | None = None
    name: str = 'mcp_server_event'

@dataclass
class CLITelemetryEvent(BaseTelemetryEvent):
    version: str
    action: str
    mode: str
    model: str | None = None
    model_provider: str | None = None
    duration_seconds: float | None = None
    error_message: str | None = None
    name: str = 'cli_event'