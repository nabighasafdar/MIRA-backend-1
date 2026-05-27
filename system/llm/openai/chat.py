from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, overload
import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from openai.types.chat import ChatCompletionContentPartTextParam
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.shared.chat_model import ChatModel
from openai.types.shared_params.reasoning_effort import ReasoningEffort
from openai.types.shared_params.response_format_json_schema import JSONSchema, ResponseFormatJSONSchema
from pydantic import BaseModel
from system.llm.base import BaseChatModel
from system.llm.exceptions import ModelProviderError, ModelRateLimitError
from system.llm.messages import BaseMessage
from system.llm.openai.serializer import OpenAIMessageSerializer
from system.llm.schema import SchemaOptimizer
from system.llm.views import ChatInvokeCompletion, ChatInvokeUsage
T = TypeVar('T', bound=BaseModel)

@dataclass
class ChatOpenAI(BaseChatModel):
    model: ChatModel | str
    temperature: float | None = 0.2
    frequency_penalty: float | None = 0.3
    reasoning_effort: ReasoningEffort = 'low'
    seed: int | None = None
    service_tier: Literal['auto', 'default', 'flex', 'priority', 'scale'] | None = None
    top_p: float | None = None
    add_schema_to_system_prompt: bool = False
    dont_force_structured_output: bool = False
    remove_min_items_from_schema: bool = False
    remove_defaults_from_schema: bool = False
    api_key: str | None = None
    organization: str | None = None
    project: str | None = None
    base_url: str | httpx.URL | None = None
    websocket_base_url: str | httpx.URL | None = None
    timeout: float | httpx.Timeout | None = None
    max_retries: int = 5
    default_headers: Mapping[str, str] | None = None
    default_query: Mapping[str, object] | None = None
    http_client: httpx.AsyncClient | None = None
    _strict_response_validation: bool = False
    max_completion_tokens: int | None = 4096
    reasoning_models: list[ChatModel | str] | None = field(default_factory=lambda: ['o4-mini', 'o3', 'o3-mini', 'o1', 'o1-pro', 'o3-pro', 'gpt-5', 'gpt-5-mini', 'gpt-5-nano'])

    @property
    def provider(self) -> str:
        return 'openai'

    def _get_client_params(self) -> dict[str, Any]:
        base_params = {'api_key': self.api_key, 'organization': self.organization, 'project': self.project, 'base_url': self.base_url, 'websocket_base_url': self.websocket_base_url, 'timeout': self.timeout, 'max_retries': self.max_retries, 'default_headers': self.default_headers, 'default_query': self.default_query, '_strict_response_validation': self._strict_response_validation}
        client_params = {k: v for k, v in base_params.items() if v is not None}
        if self.http_client is not None:
            client_params['http_client'] = self.http_client
        return client_params

    def get_client(self) -> AsyncOpenAI:
        client_params = self._get_client_params()
        return AsyncOpenAI(**client_params)

    @property
    def name(self) -> str:
        return str(self.model)

    def _get_usage(self, response: ChatCompletion) -> ChatInvokeUsage | None:
        if response.usage is not None:
            usage = ChatInvokeUsage(prompt_tokens=response.usage.prompt_tokens, prompt_cached_tokens=response.usage.prompt_tokens_details.cached_tokens if response.usage.prompt_tokens_details is not None else None, prompt_cache_creation_tokens=None, prompt_image_tokens=None, completion_tokens=response.usage.completion_tokens, total_tokens=response.usage.total_tokens)
        else:
            usage = None
        return usage

    @overload
    async def ainvoke(self, messages: list[BaseMessage], output_format: None=None, **kwargs: Any) -> ChatInvokeCompletion[str]:
        ...

    @overload
    async def ainvoke(self, messages: list[BaseMessage], output_format: type[T], **kwargs: Any) -> ChatInvokeCompletion[T]:
        ...

    async def ainvoke(self, messages: list[BaseMessage], output_format: type[T] | None=None, **kwargs: Any) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        openai_messages = OpenAIMessageSerializer.serialize_messages(messages)
        try:
            model_params: dict[str, Any] = {}
            if self.temperature is not None:
                model_params['temperature'] = self.temperature
            if self.frequency_penalty is not None:
                model_params['frequency_penalty'] = self.frequency_penalty
            if self.max_completion_tokens is not None:
                model_params['max_completion_tokens'] = self.max_completion_tokens
            if self.top_p is not None:
                model_params['top_p'] = self.top_p
            if self.seed is not None:
                model_params['seed'] = self.seed
            if self.service_tier is not None:
                model_params['service_tier'] = self.service_tier
            if self.reasoning_models and any((str(m).lower() in str(self.model).lower() for m in self.reasoning_models)):
                model_params['reasoning_effort'] = self.reasoning_effort
                model_params.pop('temperature', None)
                model_params.pop('frequency_penalty', None)
            if output_format is None:
                response = await self.get_client().chat.completions.create(model=self.model, messages=openai_messages, **model_params)
                choice = response.choices[0] if response.choices else None
                if choice is None:
                    base_url = str(self.base_url) if self.base_url is not None else None
                    hint = f' (base_url={base_url})' if base_url is not None else ''
                    raise ModelProviderError(message=f'Invalid OpenAI chat completion response: missing or empty `choices`. If you are using a proxy via `base_url`, ensure it implements the OpenAI `/v1/chat/completions` schema and returns `choices` as a non-empty list.{hint}', status_code=502, model=self.name)
                usage = self._get_usage(response)
                return ChatInvokeCompletion(completion=choice.message.content or '', usage=usage, stop_reason=choice.finish_reason)
            else:
                response_format: JSONSchema = {'name': 'agent_output', 'strict': True, 'schema': SchemaOptimizer.create_optimized_json_schema(output_format, remove_min_items=self.remove_min_items_from_schema, remove_defaults=self.remove_defaults_from_schema)}
                if self.add_schema_to_system_prompt and openai_messages and (openai_messages[0]['role'] == 'system'):
                    schema_text = f'\n<json_schema>\n{response_format}\n</json_schema>'
                    if isinstance(openai_messages[0]['content'], str):
                        openai_messages[0]['content'] += schema_text
                    elif isinstance(openai_messages[0]['content'], Iterable):
                        openai_messages[0]['content'] = list(openai_messages[0]['content']) + [ChatCompletionContentPartTextParam(text=schema_text, type='text')]
                if self.dont_force_structured_output:
                    response = await self.get_client().chat.completions.create(model=self.model, messages=openai_messages, **model_params)
                else:
                    response = await self.get_client().chat.completions.create(model=self.model, messages=openai_messages, response_format=ResponseFormatJSONSchema(json_schema=response_format, type='json_schema'), **model_params)
                choice = response.choices[0] if response.choices else None
                if choice is None:
                    base_url = str(self.base_url) if self.base_url is not None else None
                    hint = f' (base_url={base_url})' if base_url is not None else ''
                    raise ModelProviderError(message=f'Invalid OpenAI chat completion response: missing or empty `choices`. If you are using a proxy via `base_url`, ensure it implements the OpenAI `/v1/chat/completions` schema and returns `choices` as a non-empty list.{hint}', status_code=502, model=self.name)
                if choice.message.content is None:
                    raise ModelProviderError(message='Failed to parse structured output from model response', status_code=500, model=self.name)
                usage = self._get_usage(response)
                parsed = output_format.model_validate_json(choice.message.content)
                return ChatInvokeCompletion(completion=parsed, usage=usage, stop_reason=choice.finish_reason)
        except ModelProviderError:
            raise
        except RateLimitError as e:
            raise ModelRateLimitError(message=e.message, model=self.name) from e
        except APIConnectionError as e:
            raise ModelProviderError(message=str(e), model=self.name) from e
        except APIStatusError as e:
            raise ModelProviderError(message=e.message, status_code=e.status_code, model=self.name) from e
        except Exception as e:
            raise ModelProviderError(message=str(e), model=self.name) from e