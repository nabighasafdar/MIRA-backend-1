"""
LLM Abstraction for MIRA
Based on browser-use's openai.types.chat parameter layer.
"""
from system.llm.base import BaseChatModel
from system.llm.messages import (
    BaseMessage,
    UserMessage,
    SystemMessage,
    AssistantMessage,
    ContentPartTextParam as ContentText,
    ContentPartImageParam as ContentImage,
)

# Lazy importing
_LAZY_IMPORTS = {
    'ChatOpenAI': ('system.llm.openai.chat', 'ChatOpenAI'),
    'ChatGoogle': ('system.llm.google.chat', 'ChatGoogle'),
}

# Cache for model instances - only created when accessed
_model_cache: dict[str, 'BaseChatModel'] = {}


def __getattr__(name: str):
    """"Lazy import mechanism for heavy chat model imports and model instances."""
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            from importlib import import_module

            module = import_module(module_path)
            attr = getattr(module, attr_name)
            return attr
        except ImportError as e:
            raise ImportError(f'Failed to import {name} from {module_path}: {e}') from e

    # Check cache first for model instances
    if name in _model_cache:
        return _model_cache[name]

    # Try to get model instances from models module on-demand
    try:
        from system.llm.models import __getattr__ as models_getattr

        attr = models_getattr(name)
        # Cache in our clean cache dict
        _model_cache[name] = attr
        return attr
    except (AttributeError, ImportError):
        pass

    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    'BaseMessage',
    'UserMessage',
    'SystemMessage',
    'AssistantMessage',
    'ContentText',
    'ContentImage',
    'BaseChatModel',
    'ChatOpenAI',
    'ChatGoogle',
]
