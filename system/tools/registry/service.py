import asyncio
import functools
import inspect
import logging
import re
from collections.abc import Callable
from inspect import Parameter, iscoroutinefunction, signature
from types import UnionType
from typing import Any, Generic, Optional, TypeVar, Union, get_args, get_origin
import pyotp
from pydantic import BaseModel, Field, RootModel, create_model
from system.browser import BrowserSession
from system.filesystem.file_system import FileSystem
from system.llm.base import BaseChatModel
from system.observability import observe_debug
from system.telemetry.service import ProductTelemetry
from system.tools.registry.views import ActionModel, ActionRegistry, RegisteredAction, SpecialActionParameters
from system.utils import is_new_tab_page, match_url_with_domain_pattern, time_execution_async
Context = TypeVar('Context')
logger = logging.getLogger(__name__)

class Registry(Generic[Context]):

    def __init__(self, exclude_actions: list[str] | None=None):
        self.registry = ActionRegistry()
        self.telemetry = ProductTelemetry()
        self.exclude_actions = list(exclude_actions) if exclude_actions is not None else []

    def exclude_action(self, action_name: str) -> None:
        if action_name not in self.exclude_actions:
            self.exclude_actions.append(action_name)
        if action_name in self.registry.actions:
            del self.registry.actions[action_name]
            logger.debug(f'Excluded action "{action_name}" from registry')

    def _get_special_param_types(self) -> dict[str, type | UnionType | None]:
        return {'context': None, 'browser_session': BrowserSession, 'page_url': str, 'cdp_client': None, 'page_extraction_llm': BaseChatModel, 'available_file_paths': list, 'has_sensitive_data': bool, 'file_system': FileSystem, 'extraction_schema': None}

    def _normalize_action_function_signature(self, func: Callable, description: str, param_model: type[BaseModel] | None=None) -> tuple[Callable, type[BaseModel]]:
        sig = signature(func)
        parameters = list(sig.parameters.values())
        special_param_types = self._get_special_param_types()
        special_param_names = set(special_param_types.keys())
        for param in parameters:
            if param.kind == Parameter.VAR_KEYWORD:
                raise ValueError(f"Action '{func.__name__}' has **{param.name} which is not allowed. Actions must have explicit positional parameters only.")
        action_params = []
        special_params = []
        param_model_provided = param_model is not None
        for i, param in enumerate(parameters):
            if i == 0 and param_model_provided and (param.name not in special_param_names):
                continue
            if param.name in special_param_names:
                expected_type = special_param_types.get(param.name)
                if param.annotation != Parameter.empty and expected_type is not None:
                    param_type = param.annotation
                    origin = get_origin(param_type)
                    if origin is Union:
                        args = get_args(param_type)
                        param_type = next((arg for arg in args if arg is not type(None)), param_type)
                    types_compatible = param_type == expected_type or (inspect.isclass(param_type) and inspect.isclass(expected_type) and issubclass(param_type, expected_type)) or (expected_type is list and (param_type is list or get_origin(param_type) is list))
                    if not types_compatible:
                        expected_type_name = getattr(expected_type, '__name__', str(expected_type))
                        param_type_name = getattr(param_type, '__name__', str(param_type))
                        raise ValueError(f"Action '{func.__name__}' parameter '{param.name}: {param_type_name}' conflicts with special argument injected by tools: '{param.name}: {expected_type_name}'")
                special_params.append(param)
            else:
                action_params.append(param)
        if not param_model_provided:
            if action_params:
                params_dict = {}
                for param in action_params:
                    annotation = param.annotation if param.annotation != Parameter.empty else str
                    default = ... if param.default == Parameter.empty else param.default
                    params_dict[param.name] = (annotation, default)
                param_model = create_model(f'{func.__name__}_Params', __base__=ActionModel, **params_dict)
            else:
                param_model = create_model(f'{func.__name__}_Params', __base__=ActionModel)
        assert param_model is not None, f'param_model is None for {func.__name__}'

        @functools.wraps(func)
        async def normalized_wrapper(*args, params: BaseModel | None=None, **kwargs):
            if args:
                raise TypeError(f'{func.__name__}() does not accept positional arguments, only keyword arguments are allowed')
            call_args = []
            call_kwargs = {}
            if param_model_provided and parameters and (parameters[0].name not in special_param_names):
                if params is None:
                    raise ValueError(f"{func.__name__}() missing required 'params' argument")
                pass
            elif params is None and action_params:
                action_kwargs = {}
                for param in action_params:
                    if param.name in kwargs:
                        action_kwargs[param.name] = kwargs[param.name]
                if action_kwargs:
                    params = param_model(**action_kwargs)
            params_dict = params.model_dump() if params is not None else {}
            for i, param in enumerate(parameters):
                if param_model_provided and i == 0 and (param.name not in special_param_names):
                    call_args.append(params)
                elif param.name in special_param_names:
                    if param.name in kwargs:
                        value = kwargs[param.name]
                        if value is None and param.default == Parameter.empty:
                            if param.name == 'browser_session':
                                raise ValueError(f'Action {func.__name__} requires browser_session but none provided.')
                            elif param.name == 'page_extraction_llm':
                                raise ValueError(f'Action {func.__name__} requires page_extraction_llm but none provided.')
                            elif param.name == 'file_system':
                                raise ValueError(f'Action {func.__name__} requires file_system but none provided.')
                            elif param.name == 'page':
                                raise ValueError(f'Action {func.__name__} requires page but none provided.')
                            elif param.name == 'available_file_paths':
                                raise ValueError(f'Action {func.__name__} requires available_file_paths but none provided.')
                            elif param.name == 'file_system':
                                raise ValueError(f'Action {func.__name__} requires file_system but none provided.')
                            else:
                                raise ValueError(f"{func.__name__}() missing required special parameter '{param.name}'")
                        call_args.append(value)
                    elif param.default != Parameter.empty:
                        call_args.append(param.default)
                    elif param.name == 'browser_session':
                        raise ValueError(f'Action {func.__name__} requires browser_session but none provided.')
                    elif param.name == 'page_extraction_llm':
                        raise ValueError(f'Action {func.__name__} requires page_extraction_llm but none provided.')
                    elif param.name == 'file_system':
                        raise ValueError(f'Action {func.__name__} requires file_system but none provided.')
                    elif param.name == 'page':
                        raise ValueError(f'Action {func.__name__} requires page but none provided.')
                    elif param.name == 'available_file_paths':
                        raise ValueError(f'Action {func.__name__} requires available_file_paths but none provided.')
                    elif param.name == 'file_system':
                        raise ValueError(f'Action {func.__name__} requires file_system but none provided.')
                    else:
                        raise ValueError(f"{func.__name__}() missing required special parameter '{param.name}'")
                elif param.name in params_dict:
                    call_args.append(params_dict[param.name])
                elif param.default != Parameter.empty:
                    call_args.append(param.default)
                else:
                    raise ValueError(f"{func.__name__}() missing required parameter '{param.name}'")
            if iscoroutinefunction(func):
                return await func(*call_args)
            else:
                return await asyncio.to_thread(func, *call_args)
        new_params = [Parameter('params', Parameter.KEYWORD_ONLY, default=None, annotation=Optional[param_model])]
        for sp in special_params:
            new_params.append(Parameter(sp.name, Parameter.KEYWORD_ONLY, default=sp.default, annotation=sp.annotation))
        new_params.append(Parameter('kwargs', Parameter.VAR_KEYWORD))
        normalized_wrapper.__signature__ = sig.replace(parameters=new_params)
        return (normalized_wrapper, param_model)

    def _create_param_model(self, function: Callable) -> type[BaseModel]:
        sig = signature(function)
        special_param_names = set(SpecialActionParameters.model_fields.keys())
        params = {name: (param.annotation, ... if param.default == param.empty else param.default) for name, param in sig.parameters.items() if name not in special_param_names}
        return create_model(f'{function.__name__}_parameters', __base__=ActionModel, **params)

    def action(self, description: str, param_model: type[BaseModel] | None=None, domains: list[str] | None=None, allowed_domains: list[str] | None=None, terminates_sequence: bool=False):
        if allowed_domains is not None and domains is not None:
            raise ValueError("Cannot specify both 'domains' and 'allowed_domains' - they are aliases for the same parameter")
        final_domains = allowed_domains if allowed_domains is not None else domains

        def decorator(func: Callable):
            if func.__name__ in self.exclude_actions:
                return func
            normalized_func, actual_param_model = self._normalize_action_function_signature(func, description, param_model)
            action = RegisteredAction(name=func.__name__, description=description, function=normalized_func, param_model=actual_param_model, domains=final_domains, terminates_sequence=terminates_sequence)
            self.registry.actions[func.__name__] = action
            return normalized_func
        return decorator

    @observe_debug(ignore_input=True, ignore_output=True, name='execute_action')
    @time_execution_async('--execute_action')
    async def execute_action(self, action_name: str, params: dict, browser_session: BrowserSession | None=None, page_extraction_llm: BaseChatModel | None=None, file_system: FileSystem | None=None, sensitive_data: dict[str, str | dict[str, str]] | None=None, available_file_paths: list[str] | None=None, extraction_schema: dict | None=None) -> Any:
        if action_name not in self.registry.actions:
            raise ValueError(f'Action {action_name} not found')
        action = self.registry.actions[action_name]
        try:
            try:
                validated_params = action.param_model(**params)
            except Exception as e:
                raise ValueError(f'Invalid parameters {params} for action {action_name}: {type(e)}: {e}') from e
            if sensitive_data:
                current_url = None
                if browser_session and browser_session.agent_focus_target_id:
                    try:
                        target = browser_session.session_manager.get_target(browser_session.agent_focus_target_id)
                        if target:
                            current_url = target.url
                    except Exception:
                        pass
                validated_params = self._replace_sensitive_data(validated_params, sensitive_data, current_url)
            special_context = {'browser_session': browser_session, 'page_extraction_llm': page_extraction_llm, 'available_file_paths': available_file_paths, 'has_sensitive_data': action_name == 'input' and bool(sensitive_data), 'file_system': file_system, 'extraction_schema': extraction_schema}
            if action_name == 'input':
                special_context['sensitive_data'] = sensitive_data
            if browser_session:
                try:
                    special_context['page_url'] = await browser_session.get_current_page_url()
                except Exception:
                    special_context['page_url'] = None
                special_context['cdp_client'] = browser_session.cdp_client
            try:
                return await action.function(params=validated_params, **special_context)
            except Exception as e:
                raise
        except ValueError as e:
            if 'requires browser_session but none provided' in str(e) or 'requires page_extraction_llm but none provided' in str(e):
                raise RuntimeError(str(e)) from e
            else:
                raise RuntimeError(f'Error executing action {action_name}: {str(e)}') from e
        except TimeoutError as e:
            raise RuntimeError(f'Error executing action {action_name} due to timeout.') from e
        except Exception as e:
            raise RuntimeError(f'Error executing action {action_name}: {str(e)}') from e

    def _log_sensitive_data_usage(self, placeholders_used: set[str], current_url: str | None) -> None:
        if placeholders_used:
            url_info = f' on {current_url}' if current_url and (not is_new_tab_page(current_url)) else ''
            logger.info(f"🔒 Using sensitive data placeholders: {', '.join(sorted(placeholders_used))}{url_info}")

    def _replace_sensitive_data(self, params: BaseModel, sensitive_data: dict[str, Any], current_url: str | None=None) -> BaseModel:
        secret_pattern = re.compile('<secret>(.*?)</secret>')
        all_missing_placeholders = set()
        replaced_placeholders = set()
        applicable_secrets = {}
        for domain_or_key, content in sensitive_data.items():
            if isinstance(content, dict):
                if current_url and (not is_new_tab_page(current_url)):
                    if match_url_with_domain_pattern(current_url, domain_or_key):
                        applicable_secrets.update(content)
            else:
                applicable_secrets[domain_or_key] = content
        applicable_secrets = {k: v for k, v in applicable_secrets.items() if v}

        def recursively_replace_secrets(value: str | dict | list) -> str | dict | list:
            if isinstance(value, str):
                matches = secret_pattern.findall(value)
                for placeholder in matches:
                    if placeholder in applicable_secrets:
                        if placeholder.endswith('bu_2fa_code'):
                            totp = pyotp.TOTP(applicable_secrets[placeholder], digits=6)
                            replacement_value = totp.now()
                        else:
                            replacement_value = applicable_secrets[placeholder]
                        value = value.replace(f'<secret>{placeholder}</secret>', replacement_value)
                        replaced_placeholders.add(placeholder)
                    else:
                        all_missing_placeholders.add(placeholder)
                if value in applicable_secrets:
                    placeholder_name = value
                    if placeholder_name.endswith('bu_2fa_code'):
                        totp = pyotp.TOTP(applicable_secrets[placeholder_name], digits=6)
                        value = totp.now()
                    else:
                        value = applicable_secrets[placeholder_name]
                    replaced_placeholders.add(placeholder_name)
                return value
            elif isinstance(value, dict):
                return {k: recursively_replace_secrets(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [recursively_replace_secrets(v) for v in value]
            return value
        params_dump = params.model_dump()
        processed_params = recursively_replace_secrets(params_dump)
        self._log_sensitive_data_usage(replaced_placeholders, current_url)
        if all_missing_placeholders:
            logger.warning(f"Missing or empty keys in sensitive_data dictionary: {', '.join(all_missing_placeholders)}")
        return type(params).model_validate(processed_params)

    def create_action_model(self, include_actions: list[str] | None=None, page_url: str | None=None) -> type[ActionModel]:
        from typing import Union
        available_actions: dict[str, RegisteredAction] = {}
        for name, action in self.registry.actions.items():
            if include_actions is not None and name not in include_actions:
                continue
            if page_url is None:
                if action.domains is None:
                    available_actions[name] = action
                continue
            domain_is_allowed = self.registry._match_domains(action.domains, page_url)
            if domain_is_allowed:
                available_actions[name] = action
        individual_action_models: list[type[BaseModel]] = []
        for name, action in available_actions.items():
            individual_model = create_model(f"{name.title().replace('_', '')}ActionModel", __base__=ActionModel, **{name: (action.param_model, Field(description=action.description))})
            individual_action_models.append(individual_model)
        if not individual_action_models:
            return create_model('EmptyActionModel', __base__=ActionModel)
        if len(individual_action_models) == 1:
            result_model = individual_action_models[0]
        else:
            union_type = Union[tuple(individual_action_models)]

            class ActionModelUnion(RootModel[union_type]):

                def get_index(self) -> int | None:
                    if hasattr(self.root, 'get_index'):
                        return self.root.get_index()
                    return None

                def set_index(self, index: int):
                    if hasattr(self.root, 'set_index'):
                        self.root.set_index(index)

                def model_dump(self, **kwargs):
                    if hasattr(self.root, 'model_dump'):
                        return self.root.model_dump(**kwargs)
                    return super().model_dump(**kwargs)
            ActionModelUnion.__name__ = 'ActionModel'
            ActionModelUnion.__qualname__ = 'ActionModel'
            result_model = ActionModelUnion
        return result_model

    def get_prompt_description(self, page_url: str | None=None) -> str:
        return self.registry.get_prompt_description(page_url=page_url)