from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from system.config import CONFIG

logger = logging.getLogger(__name__)


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


async def persist_assistant_message(chat_id: str, content: str) -> None:
	"""Save agent reply to Supabase so chat history survives missed SSE connections."""
	text = (content or '').strip()
	if not text or not CONFIG.SUPABASE_URL or not CONFIG.SUPABASE_SERVICE_ROLE_KEY:
		return
	base = CONFIG.SUPABASE_URL.rstrip('/')
	headers = _headers(CONFIG.SUPABASE_SERVICE_ROLE_KEY)
	async with httpx.AsyncClient(timeout=30.0) as client:
		check = await client.get(
			f'{base}/rest/v1/messages',
			params={
				'chat_id': f'eq.{chat_id}',
				'select': 'role,content',
				'order': 'created_at.desc',
				'limit': '1',
			},
			headers=headers,
		)
		if check.status_code == 200:
			rows = check.json()
			if rows and rows[0].get('role') == 'assistant' and (rows[0].get('content') or '').strip() == text:
				return
		r = await client.post(
			f'{base}/rest/v1/messages',
			json={'chat_id': chat_id, 'role': 'assistant', 'content': text},
			headers=headers,
		)
		if r.status_code not in (200, 201):
			logger.warning('persist_assistant_message failed: %s', (r.text or '')[:200] or r.status_code)
			return
		await client.patch(
			f'{base}/rest/v1/chats',
			params={'id': f'eq.{chat_id}'},
			json={'updated_at': datetime.now(timezone.utc).isoformat()},
			headers=headers,
		)


def wallet_to_extend_message(items: list[dict[str, Any]]) -> str | None:
	if not items:
		return None
	lines = [
		'The user Info Wallet may contain saved personal details and login credentials.',
		'Use saved values directly — do not call prompt_user for information already listed here.',
		'Use credentials ONLY on matching login/account pages the user intends to reach.',
	]
	for i, row in enumerate(items, 1):
		label = row.get('label') or f'Item {i}'
		url = row.get('url')
		user = row.get('username') or ''
		pw = row.get('password') or ''
		if pw:
			if url:
				lines.append(f'- [{label}] site_hint={url} username={user} password={pw}')
			else:
				lines.append(f'- [{label}] username={user} password={pw}')
		elif user:
			if url:
				lines.append(f'- [{label}] saved_value={user} site_hint={url}')
			else:
				lines.append(f'- [{label}] saved_value={user}')
	return '\n'.join(lines)


async def upsert_wallet_item(
	user_id: str,
	label: str,
	value: str,
	url: str | None = None,
	*,
	is_credential: bool = False,
	password: str | None = None,
) -> bool:
	"""Create or update a wallet row by label (case-insensitive)."""
	if not label or not value:
		return False
	if not CONFIG.SUPABASE_URL or not CONFIG.SUPABASE_SERVICE_ROLE_KEY:
		return False
	base = CONFIG.SUPABASE_URL.rstrip('/')
	headers = _headers(CONFIG.SUPABASE_SERVICE_ROLE_KEY)
	existing = next(
		(
			row
			for row in await fetch_wallet_items(user_id)
			if (row.get('label') or '').strip().lower() == label.lower()
		),
		None,
	)
	payload: dict[str, Any] = {
		'label': label,
		'username': value,
		'password': password if is_credential else (password or ''),
		'url': url,
		'updated_at': datetime.now(timezone.utc).isoformat(),
	}
	async with httpx.AsyncClient(timeout=30.0) as client:
		if existing and existing.get('id'):
			r = await client.patch(
				f'{base}/rest/v1/info_wallet_items',
				params={'id': f'eq.{existing["id"]}', 'user_id': f'eq.{user_id}'},
				json=payload,
				headers=headers,
			)
			return r.status_code in (200, 204)
		payload['user_id'] = user_id
		r = await client.post(f'{base}/rest/v1/info_wallet_items', json=payload, headers=headers)
		return r.status_code in (200, 201)


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
