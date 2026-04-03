import os
from typing import TYPE_CHECKING

from system.logging_config import setup_logging

# Only set up logging if not in MCP mode or if explicitly requested
if os.environ.get('BROWSER_USE_SETUP_LOGGING', 'true').lower() != 'false':
	import logging
	from system.config import CONFIG

	# Get log level from config/environment (default to INFO)
	log_level = logging.DEBUG if CONFIG.IN_DOCKER else logging.INFO

	# Set up logging with file handlers if specified
	logger = setup_logging(log_level=log_level)
else:
	import logging

	logger = logging.getLogger('system')

# Monkeypatch BaseSubprocessTransport.__del__ to handle closed event loops gracefully
from asyncio import base_subprocess

_original_del = base_subprocess.BaseSubprocessTransport.__del__


def _patched_del(self):
	"""Patched __del__ that handles closed event loops without throwing noisy red-herring errors like RuntimeError: Event loop is closed"""
	try:
		# Check if the event loop is closed before calling the original
		if hasattr(self, '_loop') and self._loop and self._loop.is_closed():
			# Event loop is closed, skip cleanup that requires the loop
			return
		_original_del(self)
	except RuntimeError as e:
		if 'Event loop is closed' in str(e):
			# Silently ignore this specific error
			pass
		else:
			raise


base_subprocess.BaseSubprocessTransport.__del__ = _patched_del


# Type stubs for lazy imports - fixes linter warnings
if TYPE_CHECKING:
	from system.agent.prompts import SystemPrompt
	from system.agent.service import Agent

	# from system.agent.service import Agent
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

	# Lazy imports mapping - only import when actually accessed
_LAZY_IMPORTS = {
	# Agent service (heavy due to dependencies)
	# 'Agent': ('system.agent.service', 'Agent'),
	# Code-use agent (Jupyter notebook-like execution)
	'CodeAgent': ('system.code_use.service', 'CodeAgent'),
	'Agent': ('system.agent.service', 'Agent'),
	# System prompt (moderate weight due to agent.views imports)
	'SystemPrompt': ('system.agent.prompts', 'SystemPrompt'),
	# Agent views (very heavy - over 1 second!)
	'ActionModel': ('system.agent.views', 'ActionModel'),
	'ActionResult': ('system.agent.views', 'ActionResult'),
	'AgentHistoryList': ('system.agent.views', 'AgentHistoryList'),
	'BrowserSession': ('system.browser', 'BrowserSession'),
	'Browser': ('system.browser', 'BrowserSession'),  # Alias for BrowserSession
	'BrowserProfile': ('system.browser', 'BrowserProfile'),
	# Tools (moderate weight)
	'Tools': ('system.tools.service', 'Tools'),
	'Controller': ('system.tools.service', 'Controller'),  # alias
	# DOM service (moderate weight)
	'DomService': ('system.dom.service', 'DomService'),
	# Chat models (very heavy imports)
	'ChatOpenAI': ('system.llm.openai.chat', 'ChatOpenAI'),
	'ChatGoogle': ('system.llm.google.chat', 'ChatGoogle'),
	# LLM models module
	'models': ('system.llm.models', None),
	# Sandbox execution
	'sandbox': ('system.sandbox', 'sandbox'),
}


def __getattr__(name: str):
	"""Lazy import mechanism - only import modules when they're actually accessed."""
	if name in _LAZY_IMPORTS:
		module_path, attr_name = _LAZY_IMPORTS[name]
		try:
			from importlib import import_module

			module = import_module(module_path)
			if attr_name is None:
				# For modules like 'models', return the module itself
				attr = module
			else:
				attr = getattr(module, attr_name)
			# Cache the imported attribute in the module's globals
			globals()[name] = attr
			return attr
		except ImportError as e:
			raise ImportError(f'Failed to import {name} from {module_path}: {e}') from e

	raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
	'Agent',
	'CodeAgent',
	# 'CodeAgent',
	'BrowserSession',
	'Browser',  # Alias for BrowserSession
	'BrowserProfile',
	'Controller',
	'DomService',
	'SystemPrompt',
	'ActionResult',
	'ActionModel',
	'AgentHistoryList',
	# Chat models
	'ChatOpenAI',
	'ChatGoogle',
	'Tools',
	'Controller',
	# LLM models module
	'models',
	# Sandbox execution
	'sandbox',
]
