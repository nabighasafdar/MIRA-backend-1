import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, overload
from google import genai
from google.auth.credentials import Credentials
from google.genai import types
from google.genai.types import MediaModality
from pydantic import BaseModel
from system.llm.base import BaseChatModel
from system.llm.exceptions import ModelProviderError
from system.llm.google.serializer import GoogleMessageSerializer
from system.llm.messages import BaseMessage
from system.llm.schema import SchemaOptimizer
from system.llm.views import ChatInvokeCompletion, ChatInvokeUsage
T = TypeVar('T', bound=BaseModel)
VerifiedGeminiModels = Literal['gemini-2.0-flash', 'gemini-2.0-flash-exp', 'gemini-2.0-flash-lite-preview-02-05', 'Gemini-2.0-exp', 'gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-flash-latest', 'gemini-flash-lite-latest', 'gemini-2.5-pro', 'gemini-3-pro-preview', 'gemini-3-flash-preview', 'gemma-3-27b-it', 'gemma-3-4b', 'gemma-3-12b', 'gemma-3n-e2b', 'gemma-3n-e4b']

@dataclass
class ChatGoogle(BaseChatModel):
    model: VerifiedGeminiModels | str
    temperature: float | None = 0.5
    top_p: float | None = None
    seed: int | None = None
    thinking_budget: int | None = None
    thinking_level: Literal['minimal', 'low', 'medium', 'high'] | None = None
    max_output_tokens: int | None = 8096
    config: types.GenerateContentConfigDict | None = None
    include_system_in_user: bool = False
    supports_structured_output: bool = True
    max_retries: int = 5
    retryable_status_codes: list[int] = field(default_factory=lambda: [429, 500, 502, 503, 504])
    retry_base_delay: float = 1.0
    retry_max_delay: float = 60.0
    api_key: str | None = None
    vertexai: bool | None = None
    credentials: Credentials | None = None
    project: str | None = None
    location: str | None = None
    http_options: types.HttpOptions | types.HttpOptionsDict | None = None
    _client: genai.Client | None = None

    @property
    def provider(self) -> str:
        return 'google'

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(f'system.llm.google.{self.model}')

    def _get_client_params(self) -> dict[str, Any]:
        base_params = {'api_key': self.api_key, 'vertexai': self.vertexai, 'credentials': self.credentials, 'project': self.project, 'location': self.location, 'http_options': self.http_options}
        client_params = {k: v for k, v in base_params.items() if v is not None}
        return client_params

    def get_client(self) -> genai.Client:
        if self._client is not None:
            return self._client
        client_params = self._get_client_params()
        self._client = genai.Client(**client_params)
        return self._client

    @property
    def name(self) -> str:
        return str(self.model)

    def _get_stop_reason(self, response: types.GenerateContentResponse) -> str | None:
        if hasattr(response, 'candidates') and response.candidates:
            return str(response.candidates[0].finish_reason) if hasattr(response.candidates[0], 'finish_reason') else None
        return None

    def _get_usage(self, response: types.GenerateContentResponse) -> ChatInvokeUsage | None:
        usage: ChatInvokeUsage | None = None
        if response.usage_metadata is not None:
            image_tokens = 0
            if response.usage_metadata.prompt_tokens_details is not None:
                image_tokens = sum((detail.token_count or 0 for detail in response.usage_metadata.prompt_tokens_details if detail.modality == MediaModality.IMAGE))
            usage = ChatInvokeUsage(prompt_tokens=response.usage_metadata.prompt_token_count or 0, completion_tokens=(response.usage_metadata.candidates_token_count or 0) + (response.usage_metadata.thoughts_token_count or 0), total_tokens=response.usage_metadata.total_token_count or 0, prompt_cached_tokens=response.usage_metadata.cached_content_token_count, prompt_cache_creation_tokens=None, prompt_image_tokens=image_tokens)
        return usage

    @overload
    async def ainvoke(self, messages: list[BaseMessage], output_format: None=None, **kwargs: Any) -> ChatInvokeCompletion[str]:
        ...

    @overload
    async def ainvoke(self, messages: list[BaseMessage], output_format: type[T], **kwargs: Any) -> ChatInvokeCompletion[T]:
        ...

    async def ainvoke(self, messages: list[BaseMessage], output_format: type[T] | None=None, **kwargs: Any) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        contents, system_instruction = GoogleMessageSerializer.serialize_messages(messages, include_system_in_user=self.include_system_in_user)
        config: types.GenerateContentConfigDict = {}
        if self.config:
            config = self.config.copy()
        if self.temperature is not None:
            config['temperature'] = self.temperature
        if system_instruction:
            config['system_instruction'] = system_instruction
        if self.top_p is not None:
            config['top_p'] = self.top_p
        if self.seed is not None:
            config['seed'] = self.seed
        is_gemini_3_pro = 'gemini-3-pro' in self.model
        is_gemini_3_flash = 'gemini-3-flash' in self.model
        if is_gemini_3_pro:
            if self.thinking_budget is not None:
                self.logger.warning(f'thinking_budget={self.thinking_budget} is deprecated for Gemini 3 Pro and may cause suboptimal performance. Use thinking_level instead.')
            if self.thinking_level in ('minimal', 'medium'):
                self.logger.warning(f'thinking_level="{self.thinking_level}" is not supported for Gemini 3 Pro. Only "low" and "high" are valid. Falling back to "low".')
                self.thinking_level = 'low'
            if self.thinking_level is None:
                self.thinking_level = 'low'
            level = types.ThinkingLevel(self.thinking_level.upper())
            config['thinking_config'] = types.ThinkingConfigDict(thinking_level=level)
        elif is_gemini_3_flash:
            if self.thinking_level is not None:
                level = types.ThinkingLevel(self.thinking_level.upper())
                config['thinking_config'] = types.ThinkingConfigDict(thinking_level=level)
            else:
                if self.thinking_budget is None:
                    self.thinking_budget = -1
                config['thinking_config'] = types.ThinkingConfigDict(thinking_budget=self.thinking_budget)
        else:
            if self.thinking_level is not None:
                self.logger.warning(f'thinking_level="{self.thinking_level}" is not supported for this model. Use thinking_budget instead (0 to disable, -1 for dynamic, or token count).')
            if self.thinking_budget is None and ('gemini-2.5' in self.model or 'gemini-flash' in self.model):
                self.thinking_budget = -1
            if self.thinking_budget is not None:
                config['thinking_config'] = types.ThinkingConfigDict(thinking_budget=self.thinking_budget)
        if self.max_output_tokens is not None:
            config['max_output_tokens'] = self.max_output_tokens

        async def _make_api_call():
            start_time = time.time()
            self.logger.debug(f'🚀 Starting API call to {self.model}')
            try:
                if output_format is None:
                    self.logger.debug('📄 Requesting text response')
                    response = await self.get_client().aio.models.generate_content(model=self.model, contents=contents, config=config)
                    elapsed = time.time() - start_time
                    self.logger.debug(f'✅ Got text response in {elapsed:.2f}s')
                    text = response.text or ''
                    if not text:
                        self.logger.warning('⚠️ Empty text response received')
                    usage = self._get_usage(response)
                    return ChatInvokeCompletion(completion=text, usage=usage, stop_reason=self._get_stop_reason(response))
                elif self.supports_structured_output:
                    self.logger.debug(f'🔧 Requesting structured output for {output_format.__name__}')
                    config['response_mime_type'] = 'application/json'
                    optimized_schema = SchemaOptimizer.create_gemini_optimized_schema(output_format)
                    gemini_schema = self._fix_gemini_schema(optimized_schema)
                    config['response_schema'] = gemini_schema
                    response = await self.get_client().aio.models.generate_content(model=self.model, contents=contents, config=config)
                    elapsed = time.time() - start_time
                    self.logger.debug(f'✅ Got structured response in {elapsed:.2f}s')
                    usage = self._get_usage(response)
                    if response.parsed is None:
                        self.logger.debug('📝 Parsing JSON from text response')
                        if response.text:
                            try:
                                text = response.text.strip()
                                if text.startswith('```json') and text.endswith('```'):
                                    text = text[7:-3].strip()
                                    self.logger.debug('🔧 Stripped ```json``` wrapper from response')
                                elif text.startswith('```') and text.endswith('```'):
                                    text = text[3:-3].strip()
                                    self.logger.debug('🔧 Stripped ``` wrapper from response')
                                parsed_data = json.loads(text)
                                return ChatInvokeCompletion(completion=output_format.model_validate(parsed_data), usage=usage, stop_reason=self._get_stop_reason(response))
                            except (json.JSONDecodeError, ValueError) as e:
                                self.logger.error(f'❌ Failed to parse JSON response: {str(e)}')
                                self.logger.debug(f'Raw response text: {response.text[:200]}...')
                                raise ModelProviderError(message=f'Failed to parse or validate response {response}: {str(e)}', status_code=500, model=self.model) from e
                        else:
                            self.logger.error('❌ No response text received')
                            raise ModelProviderError(message=f'No response from model {response}', status_code=500, model=self.model)
                    if isinstance(response.parsed, output_format):
                        return ChatInvokeCompletion(completion=response.parsed, usage=usage, stop_reason=self._get_stop_reason(response))
                    else:
                        return ChatInvokeCompletion(completion=output_format.model_validate(response.parsed), usage=usage, stop_reason=self._get_stop_reason(response))
                else:
                    self.logger.debug(f'🔄 Using fallback JSON mode for {output_format.__name__}')
                    modified_messages = [m.model_copy(deep=True) for m in messages]
                    if modified_messages and isinstance(modified_messages[-1].content, str):
                        json_instruction = f'\n\nPlease respond with a valid JSON object that matches this schema: {SchemaOptimizer.create_optimized_json_schema(output_format)}'
                        modified_messages[-1].content += json_instruction
                    fallback_contents, fallback_system = GoogleMessageSerializer.serialize_messages(modified_messages, include_system_in_user=self.include_system_in_user)
                    fallback_config = config.copy()
                    if fallback_system:
                        fallback_config['system_instruction'] = fallback_system
                    response = await self.get_client().aio.models.generate_content(model=self.model, contents=fallback_contents, config=fallback_config)
                    elapsed = time.time() - start_time
                    self.logger.debug(f'✅ Got fallback response in {elapsed:.2f}s')
                    usage = self._get_usage(response)
                    if response.text:
                        try:
                            text = response.text.strip()
                            if text.startswith('```json') and text.endswith('```'):
                                text = text[7:-3].strip()
                            elif text.startswith('```') and text.endswith('```'):
                                text = text[3:-3].strip()
                            parsed_data = json.loads(text)
                            return ChatInvokeCompletion(completion=output_format.model_validate(parsed_data), usage=usage, stop_reason=self._get_stop_reason(response))
                        except (json.JSONDecodeError, ValueError) as e:
                            self.logger.error(f'❌ Failed to parse fallback JSON: {str(e)}')
                            self.logger.debug(f'Raw response text: {response.text[:200]}...')
                            raise ModelProviderError(message=f'Model does not support JSON mode and failed to parse JSON from text response: {str(e)}', status_code=500, model=self.model) from e
                    else:
                        self.logger.error('❌ No response text in fallback mode')
                        raise ModelProviderError(message='No response from model', status_code=500, model=self.model)
            except Exception as e:
                elapsed = time.time() - start_time
                self.logger.error(f'💥 API call failed after {elapsed:.2f}s: {type(e).__name__}: {e}')
                raise
        assert self.max_retries >= 1, 'max_retries must be at least 1'
        for attempt in range(self.max_retries):
            try:
                return await _make_api_call()
            except ModelProviderError as e:
                if e.status_code in self.retryable_status_codes and attempt < self.max_retries - 1:
                    delay = min(self.retry_base_delay * 2 ** attempt, self.retry_max_delay)
                    jitter = random.uniform(0, delay * 0.1)
                    total_delay = delay + jitter
                    self.logger.warning(f'⚠️ Got {e.status_code} error, retrying in {total_delay:.1f}s... (attempt {attempt + 1}/{self.max_retries})')
                    await asyncio.sleep(total_delay)
                    continue
                raise
            except Exception as e:
                error_message = str(e)
                status_code: int | None = None
                if hasattr(e, 'response'):
                    response_obj = getattr(e, 'response', None)
                    if response_obj and hasattr(response_obj, 'status_code'):
                        status_code = getattr(response_obj, 'status_code', None)
                if 'timeout' in error_message.lower() or 'cancelled' in error_message.lower():
                    if isinstance(e, asyncio.CancelledError) or 'CancelledError' in str(type(e)):
                        error_message = 'Gemini API request was cancelled (likely timeout). Consider: 1) Reducing input size, 2) Using a different model, 3) Checking network connectivity.'
                        status_code = 504
                    else:
                        status_code = 408
                elif any((indicator in error_message.lower() for indicator in ['forbidden', '403'])):
                    status_code = 403
                elif any((indicator in error_message.lower() for indicator in ['rate limit', 'resource exhausted', 'quota exceeded', 'too many requests', '429'])):
                    status_code = 429
                elif any((indicator in error_message.lower() for indicator in ['service unavailable', 'internal server error', 'bad gateway', '503', '502', '500'])):
                    status_code = 503
                raise ModelProviderError(message=error_message, status_code=status_code or 502, model=self.name) from e
        raise RuntimeError('Retry loop completed without return or exception')

    def _fix_gemini_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        if '$defs' in schema:
            defs = schema.pop('$defs')

            def resolve_refs(obj: Any) -> Any:
                if isinstance(obj, dict):
                    if '$ref' in obj:
                        ref = obj.pop('$ref')
                        ref_name = ref.split('/')[-1]
                        if ref_name in defs:
                            resolved = defs[ref_name].copy()
                            for key, value in obj.items():
                                if key != '$ref':
                                    resolved[key] = value
                            return resolve_refs(resolved)
                        return obj
                    else:
                        return {k: resolve_refs(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [resolve_refs(item) for item in obj]
                return obj
            schema = resolve_refs(schema)

        def clean_schema(obj: Any, parent_key: str | None=None) -> Any:
            if isinstance(obj, dict):
                cleaned = {}
                for key, value in obj.items():
                    is_metadata_title = key == 'title' and parent_key != 'properties'
                    if key not in ['additionalProperties', 'default'] and (not is_metadata_title):
                        cleaned_value = clean_schema(value, parent_key=key)
                        if key == 'properties' and isinstance(cleaned_value, dict) and (len(cleaned_value) == 0) and isinstance(obj.get('type', ''), str) and (obj.get('type', '').upper() == 'OBJECT'):
                            cleaned['properties'] = {'_placeholder': {'type': 'string'}}
                        else:
                            cleaned[key] = cleaned_value
                if isinstance(cleaned.get('type', ''), str) and cleaned.get('type', '').upper() == 'OBJECT' and ('properties' in cleaned) and isinstance(cleaned['properties'], dict) and (len(cleaned['properties']) == 0):
                    cleaned['properties'] = {'_placeholder': {'type': 'string'}}
                return cleaned
            elif isinstance(obj, list):
                return [clean_schema(item, parent_key=parent_key) for item in obj]
            return obj
        return clean_schema(schema)