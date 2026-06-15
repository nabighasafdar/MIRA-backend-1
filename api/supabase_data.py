from __future__ import annotations

from typing import Any

import httpx

from system.config import CONFIG


def _headers(service_key: str) -> dict[str, str]:
	return {
		'apikey': service_key,
		'Authorization': f'Bearer {service_key}',
		'Accept': 'application/json',
		'Prefer': 'return=representation',
	}


async def fetch_browser_profile_ready(user_id: str) -> bool:
	if not CONFIG.SUPABASE_URL or not CONFIG.SUPABASE_SERVICE_ROLE_KEY:
		return False
	url = f'{CONFIG.SUPABASE_URL.rstrip("/")}/rest/v1/profiles'
	params = {'id': f'eq.{user_id}', 'select': 'browser_profile_ready'}
	async with httpx.AsyncClient(timeout=30.0) as client:
		r = await client.get(url, params=params, headers=_headers(CONFIG.SUPABASE_SERVICE_ROLE_KEY))
		if r.status_code != 200:
			return False
		rows = r.json()
	if not rows:
		return False
	return bool(rows[0].get('browser_profile_ready'))


async def set_browser_profile_ready(user_id: str, ready: bool) -> None:
	if not CONFIG.SUPABASE_URL or not CONFIG.SUPABASE_SERVICE_ROLE_KEY:
		return
	url = f'{CONFIG.SUPABASE_URL.rstrip("/")}/rest/v1/profiles'
	params = {'id': f'eq.{user_id}'}
	payload = {'browser_profile_ready': ready}
	async with httpx.AsyncClient(timeout=30.0) as client:
		await client.patch(url, params=params, json=payload, headers=_headers(CONFIG.SUPABASE_SERVICE_ROLE_KEY))


async def is_browser_profile_ready(user_id: str) -> bool:
	from api.browser_profile import profile_has_login_data

	if profile_has_login_data(user_id):
		return True
	return await fetch_browser_profile_ready(user_id)


async def fetch_profile_llm_key(user_id: str) -> str | None:
	if not CONFIG.SUPABASE_URL or not CONFIG.SUPABASE_SERVICE_ROLE_KEY:
		return None
	url = f'{CONFIG.SUPABASE_URL.rstrip("/")}/rest/v1/profiles'
	params = {'id': f'eq.{user_id}', 'select': 'llm_api_key'}
	async with httpx.AsyncClient(timeout=30.0) as client:
		r = await client.get(url, params=params, headers=_headers(CONFIG.SUPABASE_SERVICE_ROLE_KEY))
		if r.status_code != 200:
			return None
		rows = r.json()
	if not rows:
		return None
	return rows[0].get('llm_api_key')


async def fetch_wallet_items(user_id: str) -> list[dict[str, Any]]:
	if not CONFIG.SUPABASE_URL or not CONFIG.SUPABASE_SERVICE_ROLE_KEY:
		return []
	url = f'{CONFIG.SUPABASE_URL.rstrip("/")}/rest/v1/info_wallet_items'
	params = {'user_id': f'eq.{user_id}', 'select': 'id,label,url,username,password'}
	async with httpx.AsyncClient(timeout=30.0) as client:
		r = await client.get(url, params=params, headers=_headers(CONFIG.SUPABASE_SERVICE_ROLE_KEY))
		if r.status_code != 200:
			return []
		return list(r.json())


async def fetch_bookmark_workflow(user_id: str, bookmark_id: str) -> dict[str, Any] | None:
	if not CONFIG.SUPABASE_URL or not CONFIG.SUPABASE_SERVICE_ROLE_KEY:
		return None
	url = f'{CONFIG.SUPABASE_URL.rstrip("/")}/rest/v1/bookmarks'
	params = {'id': f'eq.{bookmark_id}', 'user_id': f'eq.{user_id}', 'select': 'id,name,agent_workflow'}
	async with httpx.AsyncClient(timeout=30.0) as client:
		r = await client.get(url, params=params, headers=_headers(CONFIG.SUPABASE_SERVICE_ROLE_KEY))
		if r.status_code != 200:
			return None
	rows = r.json()
	if not rows:
		return None
	return rows[0]


def wallet_to_extend_message(items: list[dict[str, Any]]) -> str | None:
	if not items:
		return None
	lines = [
		'Use the following saved credentials ONLY on matching login/account pages the user intends to reach.',
	]
	for i, row in enumerate(items, 1):
		label = row.get('label') or f'Credential {i}'
		url = row.get('url')
		user = row.get('username') or ''
		pw = row.get('password') or ''
		if url:
			lines.append(f'- [{label}] site_hint={url} username={user} password={pw}')
		else:
			lines.append(f'- [{label}] username={user} password={pw}')
	return '\n'.join(lines)


def bookmark_workflow_to_template(agent_workflow: Any) -> dict | None:
	"""Resolve WorkflowTemplate JSON from bookmarks.agent_workflow."""
	if agent_workflow is None:
		return None
	if isinstance(agent_workflow, dict):
		if 'workflow_id' in agent_workflow and 'steps' in agent_workflow:
			return agent_workflow
		if 'workflow' in agent_workflow and isinstance(agent_workflow['workflow'], str):
			raw = agent_workflow['workflow'].strip()
			if raw.startswith('{') and 'workflow_id' in raw:
				import json

				try:
					return json.loads(raw)
				except json.JSONDecodeError:
					pass
			return None
	return None
