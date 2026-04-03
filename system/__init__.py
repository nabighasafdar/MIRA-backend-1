import os
from typing import TYPE_CHECKING
from system.logging_config import setup_logging
if os.environ.get('BROWSER_USE_SETUP_LOGGING', 'true').lower() != 'false':
    import logging
    from system.config import CONFIG
    log_level = logging.DEBUG if CONFIG.IN_DOCKER else logging.INFO
    logger = setup_logging(log_level=log_level)
else:
    import logging
    logger = logging.getLogger('system')
from asyncio import base_subprocess
_original_del = base_subprocess.BaseSubprocessTransport.__del__

def _patched_del(self):
    try:
        if hasattr(self, '_loop') and self._loop and self._loop.is_closed():
            return
        _original_del(self)
    except RuntimeError as e:
        if 'Event loop is closed' in str(e):
            pass
        else:
            raise
base_subprocess.BaseSubprocessTransport.__del__ = _patched_del
if TYPE_CHECKING:
    from system.agent.prompts import SystemPrompt
    from system.agent.service import Agent
    from system.agent.views import ActionModel, ActionResult, AgentHistoryList
    from system.browser import BrowserProfile, BrowserSession
    from system.browser import BrowserSession as Browser
    from system.code_use.service import CodeAgent
    from system.dom.service import DomService
    from system.llm import models
    from system.llm.google.chat import ChatGoogle
    from system.llm.openai.chat import ChatOpenAI
    from system.sandbox import sandbox
    from system.tools.service import Controller, Tools
_LAZY_IMPORTS = {'CodeAgent': ('system.code_use.service', 'CodeAgent'), 'Agent': ('system.agent.service', 'Agent'), 'SystemPrompt': ('system.agent.prompts', 'SystemPrompt'), 'ActionModel': ('system.agent.views', 'ActionModel'), 'ActionResult': ('system.agent.views', 'ActionResult'), 'AgentHistoryList': ('system.agent.views', 'AgentHistoryList'), 'BrowserSession': ('system.browser', 'BrowserSession'), 'Browser': ('system.browser', 'BrowserSession'), 'BrowserProfile': ('system.browser', 'BrowserProfile'), 'Tools': ('system.tools.service', 'Tools'), 'Controller': ('system.tools.service', 'Controller'), 'DomService': ('system.dom.service', 'DomService'), 'ChatOpenAI': ('system.llm.openai.chat', 'ChatOpenAI'), 'ChatGoogle': ('system.llm.google.chat', 'ChatGoogle'), 'models': ('system.llm.models', None), 'sandbox': ('system.sandbox', 'sandbox')}

def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            from importlib import import_module
            module = import_module(module_path)
            if attr_name is None:
                attr = module
            else:
                attr = getattr(module, attr_name)
            globals()[name] = attr
            return attr
        except ImportError as e:
            raise ImportError(f'Failed to import {name} from {module_path}: {e}') from e
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
__all__ = ['Agent', 'CodeAgent', 'BrowserSession', 'Browser', 'BrowserProfile', 'Controller', 'DomService', 'SystemPrompt', 'ActionResult', 'ActionModel', 'AgentHistoryList', 'ChatOpenAI', 'ChatGoogle', 'Tools', 'Controller', 'models', 'sandbox']