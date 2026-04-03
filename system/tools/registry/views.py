from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from pydantic import BaseModel, ConfigDict
from system.browser import BrowserSession
from system.filesystem.file_system import FileSystem
from system.llm.base import BaseChatModel
if TYPE_CHECKING:
    pass

class RegisteredAction(BaseModel):
    name: str
    description: str
    function: Callable
    param_model: type[BaseModel]
    terminates_sequence: bool = False
    domains: list[str] | None = None
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def prompt_description(self) -> str:
        schema = self.param_model.model_json_schema()
        params = []
        if 'properties' in schema:
            for param_name, param_info in schema['properties'].items():
                param_desc = param_name
                if 'type' in param_info:
                    param_type = param_info['type']
                    param_desc += f'={param_type}'
                if 'description' in param_info:
                    param_desc += f" ({param_info['description']})"
                params.append(param_desc)
        if params:
            return f"{self.name}: {self.description}. ({', '.join(params)})"
        else:
            return f'{self.name}: {self.description}'

class ActionModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra='forbid')

    def get_index(self) -> int | None:
        params = self.model_dump(exclude_unset=True).values()
        if not params:
            return None
        for param in params:
            if param is not None and 'index' in param:
                return param['index']
        return None

    def set_index(self, index: int):
        action_data = self.model_dump(exclude_unset=True)
        action_name = next(iter(action_data.keys()))
        action_params = getattr(self, action_name)
        if hasattr(action_params, 'index'):
            action_params.index = index

class ActionRegistry(BaseModel):
    actions: dict[str, RegisteredAction] = {}

    @staticmethod
    def _match_domains(domains: list[str] | None, url: str) -> bool:
        if domains is None or not url:
            return True
        from system.utils import match_url_with_domain_pattern
        for domain_pattern in domains:
            if match_url_with_domain_pattern(url, domain_pattern):
                return True
        return False

    def get_prompt_description(self, page_url: str | None=None) -> str:
        if page_url is None:
            return '\n'.join((action.prompt_description() for action in self.actions.values() if action.domains is None))
        filtered_actions = []
        for action in self.actions.values():
            if not action.domains:
                continue
            if self._match_domains(action.domains, page_url):
                filtered_actions.append(action)
        return '\n'.join((action.prompt_description() for action in filtered_actions))

class SpecialActionParameters(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    context: Any | None = None
    browser_session: BrowserSession | None = None
    page_url: str | None = None
    cdp_client: Any | None = None
    page_extraction_llm: BaseChatModel | None = None
    file_system: FileSystem | None = None
    available_file_paths: list[str] | None = None
    has_sensitive_data: bool = False
    extraction_schema: dict | None = None

    @classmethod
    def get_browser_requiring_params(cls) -> set[str]:
        return {'browser_session', 'cdp_client', 'page_url'}