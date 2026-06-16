"""Per-user persistent Chromium profiles (YouTube / Google sign-in)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_PROFILE_ROOT = Path(os.getenv('MIRA_BROWSER_PROFILE_DIR', '/tmp/mira_browser_profiles'))
_MARKER = '.mira_profile_initialized'


def user_profile_dir(user_id: str) -> Path:
	safe = user_id.replace(':', '_').replace('/', '_')
	path = _PROFILE_ROOT / safe
	path.mkdir(parents=True, exist_ok=True)
	return path


def profile_dir_exists(user_id: str) -> bool:
	return user_profile_dir(user_id).exists()


def profile_has_login_data(user_id: str) -> bool:
	"""Heuristic: Chrome wrote cookies or local state after a real session."""
	root = user_profile_dir(user_id)
	candidates = [
		root / 'Default' / 'Cookies',
		root / 'Default' / 'Network' / 'Cookies',
		root / 'Local State',
	]
	for path in candidates:
		try:
			if path.is_file() and path.stat().st_size > 128:
				return True
		except OSError:
			continue
	return False


def mark_profile_initialized_local(user_id: str) -> None:
	"""Write marker only when Chrome actually persisted session data."""
	if not profile_has_login_data(user_id):
		return
	profile_dir = user_profile_dir(user_id)
	(profile_dir / _MARKER).write_text('ok', encoding='utf-8')
	logger.info('Marked local browser profile initialized for user %s', user_id[:8])


def persistent_profiles_enabled() -> bool:
	return os.getenv('MIRA_PERSISTENT_PROFILES', 'true').strip().lower() not in ('0', 'false', 'no', 'off')


PROFILE_SETUP_TASK = """<browser_profile_setup>
This is a ONE-TIME browser profile setup for MIRA (not the user's main task).

1. Open https://www.youtube.com
2. If you see "Sign in", click it and navigate to the Google sign-in page.
3. If wallet credentials apply on a login form, use them to sign in.
4. If already signed in on YouTube, also open https://mail.google.com in a new tab to confirm Gmail access.
5. If Gmail loads the inbox, stop — the profile is ready.
6. If CAPTCHA or 2FA appears, call prompt_user asking the user to complete it in the Chromium window, then click "I've signed in" in Dashboard.
7. Stop once YouTube or Gmail inbox is loaded — do not search or browse further.
8. Call done with a short message that the browser profile is ready and logins will be saved.
</browser_profile_setup>"""
