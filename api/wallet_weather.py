"""Wallet pre-check for weather tasks — ask, save, resume before the browser agent runs."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from api.job_input import UserInputGate
from api.mira_persona import get_wallet_location, task_has_explicit_location, task_mentions_weather
from api.supabase_data import fetch_wallet_items, upsert_wallet_item

WEATHER_CITY_QUESTION = 'What city should I check the weather for?'


@dataclass
class WeatherResolveResult:
	task: str
	items: list[dict[str, Any]]
	location_hint: str | None
	aborted: bool = False


async def resolve_weather_location_or_ask(
	job_id: str,
	user_id: str,
	task: str,
	items: list[dict[str, Any]],
	input_gate: UserInputGate,
	emit_async,
) -> WeatherResolveResult:
	"""
	If the user asked about weather without a city and wallet has none:
	pause in chat, save answer to wallet, then return an updated task.
	"""
	if not task_mentions_weather(task):
		return WeatherResolveResult(task=task, items=items, location_hint=None)

	if task_has_explicit_location(task):
		return WeatherResolveResult(task=task, items=items, location_hint=None)

	city = get_wallet_location(items)
	if city:
		hint = (
			f'<weather_city>\n'
			f'The user\'s saved city for weather is: {city}\n'
			f'Use this location. Do not call prompt_user for city again.\n'
			f'</weather_city>'
		)
		if city.lower() not in task.lower():
			task = f'{task.strip()} in {city}'
		return WeatherResolveResult(task=task, items=items, location_hint=hint)

	answer = await input_gate.wait(WEATHER_CITY_QUESTION, emit_async, job_id)
	if not answer:
		await emit_async({
			'event_type': 'done',
			'final_result': (
				'I need your city to check the weather. '
				'Run the task again and tell me your city when I ask.'
			),
			'successful': False,
			'phase': 'main',
		})
		return WeatherResolveResult(task=task, items=items, location_hint=None, aborted=True)

	ok = await upsert_wallet_item(user_id, 'city', answer)
	if ok:
		await emit_async({'event_type': 'wallet_item_saved', 'label': 'city', 'value': answer})

	items = await fetch_wallet_items(user_id)
	task = f'Check the weather today in {answer}.'
	hint = (
		f'<weather_city>\n'
		f'The user just provided their city: {answer}\n'
		f'Search weather for {answer} and summarize today\'s conditions.\n'
		f'</weather_city>'
	)
	return WeatherResolveResult(task=task, items=items, location_hint=hint)
