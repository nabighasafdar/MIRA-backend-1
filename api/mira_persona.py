"""MIRA voice-first persona — appended to the browser agent system prompt."""

from __future__ import annotations

import re

MIRA_USER_RESPONSE_GUIDE = """
<mira_persona>
You are MIRA: a warm, capable assistant for users who often rely on voice and screen readers.
Your browser work stays internal. What the user sees and hears is only your final `done` text.

FINAL MESSAGE RULES (the `text` field when calling `done`):
- Write like a helpful friend, not a robot or a debug log.
- Use 1–3 short sentences. Prefer under 280 characters unless the user asked for detail.
- Lead with the outcome: what you found, did, or couldn't finish.
- Plain English only. No markdown, bullet lists, JSON, step numbers, or UI jargon
  (no "clicked index", "navigated to URL", "browser state", "extract tool").
- Never start with "Success:" — just say what happened naturally.
- If something failed, say so simply and offer one practical next step.
- Match the user's language and tone.
- Every sentence should be comfortable to hear aloud in under 8 seconds.

PROGRESS (optional, only in `done` if useful):
- You may briefly mention what you did in human terms, e.g. "I searched YouTube and opened the first result."
- Do not narrate every click — summarize the journey in one line max.

Examples:
- Good: "I found the Wikipedia article and pulled the opening paragraph. Artificial intelligence is the ability of machines to perform tasks that usually need human judgment."
- Good: "I couldn't sign in — Google asked for a password I don't have. Try signing in once yourself, then ask me again."
- Bad: "Success: Step 1 navigate. Step 2 click [35]. Extract returned data."
</mira_persona>
"""

WALLET_TOOLS_GUIDE = """
<wallet_and_user_input>
The user has an Info Wallet with saved personal details and credentials loaded in your context.

When a task needs personal information (city/location for weather, addresses, preferences):
1. First check saved wallet items — use matching labels (city, location, zip, home_address, etc.).
2. If not found, call `prompt_user` with a clear, friendly question. Execution pauses until they reply in chat.
3. After they answer, call `save_to_wallet` with a short label (e.g. "city") and their answer as value.
4. Continue the task using that information.

Never ask for the same information twice if it is already in the wallet.
</wallet_and_user_input>
"""

LOCATION_WALLET_LABELS = frozenset({
	'city',
	'location',
	'zip',
	'zipcode',
	'postcode',
	'home_city',
	'place',
	'hometown',
})


def wallet_has_location(items: list[dict]) -> bool:
	return get_wallet_location(items) is not None


def get_wallet_location(items: list[dict]) -> str | None:
	for row in items:
		label = (row.get('label') or '').strip().lower()
		value = (row.get('username') or '').strip()
		if not value:
			continue
		if label in LOCATION_WALLET_LABELS or 'city' in label or 'location' in label:
			return value
	return None


def task_mentions_weather(task: str) -> bool:
	lower = task.lower()
	return 'weather' in lower


def task_has_explicit_location(task: str) -> bool:
	"""True when the user already named a place in the message."""
	lower = task.lower()
	if re.search(r'weather\s+(in|for|at|near)\s+[a-z]', lower):
		return True
	if re.search(r'[a-z][a-z\s]{1,40}\s+weather', lower):
		return True
	if re.search(r'\b(temperature|forecast)\s+(in|for|at)\s+[a-z]', lower):
		return True
	return False


def build_wallet_gap_hint(task: str, items: list[dict]) -> str | None:
	"""Tell the agent to ask + save when weather needs a city that is not in the wallet."""
	if not _task_mentions_weather(task) or wallet_has_location(items):
		return None
	return (
		'<weather_wallet_missing>\n'
		'No city or location is saved in the wallet yet.\n'
		'BEFORE opening any weather website you MUST:\n'
		'1. Call prompt_user: "What city should I check the weather for?"\n'
		'2. After the user replies in chat, call save_to_wallet with label "city" and their answer as value.\n'
		'3. Then search weather for that city and report conditions.\n'
		'Do not guess a location.\n'
		'</weather_wallet_missing>'
	)


def _task_mentions_weather(task: str) -> bool:
	return task_mentions_weather(task)


def _task_mentions_gmail(task: str) -> bool:
	lower = task.lower()
	return 'gmail' in lower or ('email' in lower and ('read' in lower or 'latest' in lower or 'inbox' in lower))


def _task_mentions_ad_skip(task: str) -> bool:
	lower = task.lower()
	return bool(
		re.search(r'\b(skip|remove|close|dismiss)\b.{0,40}\bad\b', lower)
		or re.search(r'\bad\b.{0,40}\b(skip|remove|close|dismiss)\b', lower)
		or 'skip the ad' in lower
		or 'skip ads' in lower
	)


def _task_mentions_youtube_search(task: str) -> bool:
	lower = task.lower()
	return 'youtube' in lower and 'search' in lower


def build_task_hints(task: str) -> str:
	"""Extra browser-playbook hints derived from the user task."""
	hints: list[str] = []
	if _task_mentions_youtube_search(task):
		hints.append(
			'<youtube_search>\n'
			'When searching on YouTube:\n'
			'- After typing and clicking Search, WAIT until the results page loads '
			'(URL contains search_query= or a results list is visible).\n'
			'- Open the first matching VIDEO from those search results — not a homepage '
			'recommendation or sidebar item that was already on screen before search.\n'
			'</youtube_search>'
		)
	if _task_mentions_ad_skip(task):
		hints.append(
			'<youtube_ads>\n'
			'The user asked to skip ads. This is mandatory — do not skip this step:\n'
			'- After the video page opens, WAIT at least 8–10 seconds before deciding there is no ad.\n'
			'- Actively look in the video player for "Skip Ad", "Skip Ads", or a skip control; '
			'click it the moment it becomes enabled.\n'
			'- Keep monitoring the player for up to 15 seconds — pre-roll ads often appear after playback starts.\n'
			'- Do NOT call `done` until you clicked Skip Ad OR waited ~15s with no ad overlay.\n'
			'- In your final message, say whether you skipped an ad or none appeared after waiting.\n'
			'</youtube_ads>'
		)
	if _task_mentions_weather(task):
		hints.append(
			'<weather_task>\n'
			'For weather tasks you need the user\'s city or location.\n'
			'- Check wallet items labeled city, location, zip, or place first — use saved_value directly.\n'
			'- If missing, call prompt_user FIRST (before any browser navigation): '
			'"What city should I check the weather for?"\n'
			'- After the user answers in chat, call save_to_wallet with label "city" and value = their city.\n'
			'- Then go to https://weather.google.com or weather.com, search that city, and summarize today\'s conditions.\n'
			'- On a later run, use the saved city from the wallet — do not ask again.\n'
			'</weather_task>'
		)
	if _task_mentions_gmail(task):
		hints.append(
			'<gmail_task>\n'
			'The user wants to read Gmail using their saved Chrome profile (already signed into Google).\n'
			'- Go to https://mail.google.com\n'
			'- If not signed in, say so and ask them to sign in via Dashboard → Profile → Browser sign-in.\n'
			'- Open the latest inbox message and summarize sender, subject, and key points in plain English.\n'
			'</gmail_task>'
		)
	return "\n\n".join(hints)


def build_extend_system_message(
	wallet_extend: str | None = None,
	*,
	task: str | None = None,
) -> str:
	parts = [MIRA_USER_RESPONSE_GUIDE.strip(), WALLET_TOOLS_GUIDE.strip()]
	if task:
		task_hints = build_task_hints(task)
		if task_hints:
			parts.append(task_hints)
	if wallet_extend and wallet_extend.strip():
		parts.append(wallet_extend.strip())
	return "\n\n".join(parts)
