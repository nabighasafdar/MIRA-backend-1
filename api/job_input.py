from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class UserInputGate:
	"""Pause agent execution until the user answers a chat prompt."""

	def __init__(self) -> None:
		self._event = asyncio.Event()
		self._answer: str | None = None
		self._waiting = False

	async def wait(
		self,
		question: str,
		emit: Callable[[dict[str, Any]], Awaitable[None] | None],
		job_id: str,
		*,
		timeout: float = 600.0,
	) -> str:
		self._answer = None
		self._waiting = True
		self._event.clear()
		payload = {'event_type': 'needs_user_input', 'question': question, 'job_id': job_id}
		result = emit(payload)
		if asyncio.iscoroutine(result):
			await result
		try:
			await asyncio.wait_for(self._event.wait(), timeout=timeout)
		except asyncio.TimeoutError:
			return ''
		finally:
			self._waiting = False
		return (self._answer or '').strip()

	def provide(self, answer: str) -> bool:
		if not self._waiting or self._event.is_set():
			return False
		self._answer = answer
		self._event.set()
		return True

	def is_waiting(self) -> bool:
		return self._waiting and not self._event.is_set()
