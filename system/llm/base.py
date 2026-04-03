from typing import Any, Protocol, TypeVar, overload, runtime_checkable
from pydantic import BaseModel
from system.llm.messages import BaseMessage
from system.llm.views import ChatInvokeCompletion
T = TypeVar('T', bound=BaseModel)

@runtime_checkable
class BaseChatModel(Protocol):
    _verified_api_keys: bool = False
    model: str

    @property
    def provider(self) -> str:
        ...

    @property
    def name(self) -> str:
        ...

    @property
    def model_name(self) -> str:
        return self.model

    @overload
    async def ainvoke(self, messages: list[BaseMessage], output_format: None=None, **kwargs: Any) -> ChatInvokeCompletion[str]:
        ...

    @overload
    async def ainvoke(self, messages: list[BaseMessage], output_format: type[T], **kwargs: Any) -> ChatInvokeCompletion[T]:
        ...

    async def ainvoke(self, messages: list[BaseMessage], output_format: type[T] | None=None, **kwargs: Any) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        ...

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: type, handler: Any) -> Any:
        from pydantic_core import core_schema
        return core_schema.any_schema()