"""Reuse one browser per user+chat so follow-up commands keep the same tab."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from system.browser.profile import BrowserProfile
from system.browser.session import BrowserSession

logger = logging.getLogger(__name__)

_PROFILE_ROOT = Path(os.getenv('MIRA_BROWSER_PROFILE_DIR', '/tmp/mira_browser_profiles'))
_IDLE_SECONDS = float(os.getenv('MIRA_BROWSER_IDLE_SECONDS', '1800'))


def _pool_key(user_id: str, chat_id: str | None) -> str:
	if chat_id:
		return f'{user_id}:{chat_id}'
	return user_id


def _headless() -> bool:
	headless_env = os.getenv('MIRA_HEADLESS', 'true').strip().lower()
	return headless_env not in ('0', 'false', 'no', 'off')


def _keep_browser_open() -> bool:
	return os.getenv('MIRA_CLOSE_BROWSER_AFTER_JOB', '').strip().lower() not in ('1', 'true', 'yes')


@dataclass
class _PoolEntry:
	session: BrowserSession
	profile_dir: Path
	lock: asyncio.Lock = field(default_factory=asyncio.Lock)
	last_used: float = field(default_factory=time.time)


_pool: dict[str, _PoolEntry] = {}
_pool_lock = asyncio.Lock()


def wrap_task_for_continuation(task: str, *, continuing: bool) -> str:
	if not continuing:
		return task
	prefix = (
		'<session_continuation>\n'
		'The browser is ALREADY OPEN from this chat. Continue in the SAME tab and page.\n'
		'Do NOT restart the browser or open a fresh window.\n'
		'Build on what is on screen (e.g. if YouTube is open, search or click from there).\n'
		'Only go to a new site if the user clearly asks for a different website.\n'
		'</session_continuation>\n\n'
	)
	return prefix + task


async def _evict_idle() -> None:
	now = time.time()
	stale = [k for k, e in _pool.items() if now - e.last_used > _IDLE_SECONDS]
	for key in stale:
		entry = _pool.pop(key, None)
		if not entry:
			continue
		try:
			await entry.session.stop()
		except Exception:
			logger.debug('pool evict stop failed', exc_info=True)
		logger.info('Evicted idle browser session %s', key)


async def acquire_session(user_id: str, chat_id: str | None) -> tuple[BrowserSession, bool]:
	"""Return (session, is_continuation). Caller must hold entry.lock until release."""
	await _evict_idle()
	key = _pool_key(user_id, chat_id)

	async with _pool_lock:
		entry = _pool.get(key)
		if entry is not None:
			entry.last_used = time.time()
			await entry.lock.acquire()
			return entry.session, True

		profile_dir = _PROFILE_ROOT / key.replace(':', '_')
		profile_dir.mkdir(parents=True, exist_ok=True)
		profile = BrowserProfile(
			headless=_headless(),
			user_data_dir=str(profile_dir),
			keep_alive=True,
		)
		session = BrowserSession(browser_profile=profile)
		await session.start()
		entry = _PoolEntry(session=session, profile_dir=profile_dir)
		_pool[key] = entry
		await entry.lock.acquire()
		return session, False


async def release_session(user_id: str, chat_id: str | None, *, close_browser: bool = False) -> None:
	key = _pool_key(user_id, chat_id)
	async with _pool_lock:
		entry = _pool.get(key)
		if not entry:
			return
		entry.last_used = time.time()
		if entry.lock.locked():
			entry.lock.release()
		if close_browser or not _keep_browser_open():
			try:
				await entry.session.stop()
			except Exception:
				logger.debug('pool release stop failed', exc_info=True)
			_pool.pop(key, None)
			logger.info('Closed browser session %s', key)
