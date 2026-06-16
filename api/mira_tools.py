from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from system.tools.service import Tools

from api.job_input import UserInputGate
from api.supabase_data import upsert_wallet_item


def build_mira_tools(
	user_id: str,
	input_gate: UserInputGate,
	emit: Callable[[dict[str, Any]], Awaitable[None] | None],
	job_id: str,
) -> Tools:
	"""Human-in-the-loop and wallet tools for production agent runs."""
	tools = Tools()

	@tools.action(
		'Ask the user a question in chat when required personal information is missing '
		'(e.g. city for weather, shipping address). Pauses until they reply.'
	)
	async def prompt_user(question: str) -> str:
		answer = await input_gate.wait(question, emit, job_id)
		if not answer:
			return 'The user did not provide an answer in time.'
		return f'The user replied: {answer}'

	@tools.action(
		"Save information to the user's wallet so it can be reused on future tasks without asking again. "
		'Use for locations, preferences, or credentials. label is a short key like "city" or "home_address".'
	)
	async def save_to_wallet(label: str, value: str, site_url: str = '') -> str:
		ok = await upsert_wallet_item(
			user_id,
			label.strip(),
			value.strip(),
			site_url.strip() or None,
		)
		if ok:
			result = emit({'event_type': 'wallet_item_saved', 'label': label.strip(), 'value': value.strip()})
			if asyncio.iscoroutine(result):
				await result
			return f'Successfully saved "{label}" to the user wallet.'
		return f'Failed to save "{label}" to wallet.'

	return tools
