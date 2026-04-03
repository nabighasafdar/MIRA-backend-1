import logging
import os
from collections.abc import Callable
from functools import wraps
from typing import Any, Literal, TypeVar, cast
logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()
F = TypeVar('F', bound=Callable[..., Any])

def _is_debug_mode() -> bool:
    lmnr_debug_mode = os.getenv('LMNR_LOGGING_LEVEL', '').lower()
    if lmnr_debug_mode == 'debug':
        return True
    return False
_LMNR_AVAILABLE = False
_lmnr_observe = None
try:
    from lmnr import observe as _lmnr_observe
    if os.environ.get('system_VERBOSE_OBSERVABILITY', 'false').lower() == 'true':
        logger.debug('Lmnr is available for observability')
    _LMNR_AVAILABLE = True
except ImportError:
    if os.environ.get('system_VERBOSE_OBSERVABILITY', 'false').lower() == 'true':
        logger.debug('Lmnr is not available for observability')
    _LMNR_AVAILABLE = False

def _create_no_op_decorator(name: str | None=None, ignore_input: bool=False, ignore_output: bool=False, metadata: dict[str, Any] | None=None, **kwargs: Any) -> Callable[[F], F]:
    import asyncio

    def decorator(func: F) -> F:
        if asyncio.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                return await func(*args, **kwargs)
            return cast(F, async_wrapper)
        else:

            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            return cast(F, sync_wrapper)
    return decorator

def observe(name: str | None=None, ignore_input: bool=False, ignore_output: bool=False, metadata: dict[str, Any] | None=None, span_type: Literal['DEFAULT', 'LLM', 'TOOL']='DEFAULT', **kwargs: Any) -> Callable[[F], F]:
    kwargs = {'name': name, 'ignore_input': ignore_input, 'ignore_output': ignore_output, 'metadata': metadata, 'span_type': span_type, 'tags': ['observe', 'observe_debug'], **kwargs}
    if _LMNR_AVAILABLE and _lmnr_observe:
        return cast(Callable[[F], F], _lmnr_observe(**kwargs))
    else:
        return _create_no_op_decorator(**kwargs)

def observe_debug(name: str | None=None, ignore_input: bool=False, ignore_output: bool=False, metadata: dict[str, Any] | None=None, span_type: Literal['DEFAULT', 'LLM', 'TOOL']='DEFAULT', **kwargs: Any) -> Callable[[F], F]:
    kwargs = {'name': name, 'ignore_input': ignore_input, 'ignore_output': ignore_output, 'metadata': metadata, 'span_type': span_type, 'tags': ['observe_debug'], **kwargs}
    if _LMNR_AVAILABLE and _lmnr_observe and _is_debug_mode():
        return cast(Callable[[F], F], _lmnr_observe(**kwargs))
    else:
        return _create_no_op_decorator(**kwargs)

def is_lmnr_available() -> bool:
    return _LMNR_AVAILABLE

def is_debug_mode() -> bool:
    return _is_debug_mode()

def get_observability_status() -> dict[str, bool]:
    return {'lmnr_available': _LMNR_AVAILABLE, 'debug_mode': _is_debug_mode(), 'observe_active': _LMNR_AVAILABLE, 'observe_debug_active': _LMNR_AVAILABLE and _is_debug_mode()}