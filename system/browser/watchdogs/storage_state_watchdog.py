import asyncio
import json
import os
from pathlib import Path
from typing import Any, ClassVar
from bubus import BaseEvent
from cdp_use.cdp.network import Cookie
from pydantic import Field, PrivateAttr
from system.browser.events import BrowserConnectedEvent, BrowserStopEvent, LoadStorageStateEvent, SaveStorageStateEvent, StorageStateLoadedEvent, StorageStateSavedEvent
from system.browser.watchdog_base import BaseWatchdog
from system.utils import create_task_with_error_handling

class StorageStateWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [BrowserConnectedEvent, BrowserStopEvent, SaveStorageStateEvent, LoadStorageStateEvent]
    EMITS: ClassVar[list[type[BaseEvent]]] = [StorageStateSavedEvent, StorageStateLoadedEvent]
    auto_save_interval: float = Field(default=30.0)
    save_on_change: bool = Field(default=True)
    _monitoring_task: asyncio.Task | None = PrivateAttr(default=None)
    _last_cookie_state: list[dict] = PrivateAttr(default_factory=list)
    _save_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

    async def on_BrowserConnectedEvent(self, event: BrowserConnectedEvent) -> None:
        self.logger.debug('[StorageStateWatchdog] 🍪 Initializing auth/cookies sync <-> with storage_state.json file')
        await self._start_monitoring()
        await self.event_bus.dispatch(LoadStorageStateEvent())

    async def on_BrowserStopEvent(self, event: BrowserStopEvent) -> None:
        self.logger.debug('[StorageStateWatchdog] Stopping storage_state monitoring')
        await self._stop_monitoring()

    async def on_SaveStorageStateEvent(self, event: SaveStorageStateEvent) -> None:
        path = event.path
        if path is None:
            if self.browser_session.browser_profile.storage_state:
                path = str(self.browser_session.browser_profile.storage_state)
            else:
                path = None
        await self._save_storage_state(path)

    async def on_LoadStorageStateEvent(self, event: LoadStorageStateEvent) -> None:
        path = event.path
        if path is None:
            if self.browser_session.browser_profile.storage_state:
                path = str(self.browser_session.browser_profile.storage_state)
            else:
                path = None
        await self._load_storage_state(path)

    async def _start_monitoring(self) -> None:
        if self._monitoring_task and (not self._monitoring_task.done()):
            return
        assert self.browser_session.cdp_client is not None
        self._monitoring_task = create_task_with_error_handling(self._monitor_storage_changes(), name='monitor_storage_changes', logger_instance=self.logger, suppress_exceptions=True)

    async def _stop_monitoring(self) -> None:
        if self._monitoring_task and (not self._monitoring_task.done()):
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass

    async def _check_for_cookie_changes_cdp(self, event: dict) -> None:
        try:
            headers = event.get('headers', {})
            if 'set-cookie' in headers or 'Set-Cookie' in headers:
                self.logger.debug('[StorageStateWatchdog] Cookie change detected via CDP')
                if self.save_on_change:
                    await self._save_storage_state()
        except Exception as e:
            self.logger.warning(f'[StorageStateWatchdog] Error checking for cookie changes: {e}')

    async def _monitor_storage_changes(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.auto_save_interval)
                if await self._have_cookies_changed():
                    self.logger.debug('[StorageStateWatchdog] Detected changes to sync with storage_state.json')
                    await self._save_storage_state()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f'[StorageStateWatchdog] Error in monitoring loop: {e}')

    async def _have_cookies_changed(self) -> bool:
        if not self.browser_session.cdp_client:
            return False
        try:
            current_cookies = await self.browser_session._cdp_get_cookies()
            current_cookie_set = {(c.get('name', ''), c.get('domain', ''), c.get('path', '')): c.get('value', '') for c in current_cookies}
            last_cookie_set = {(c.get('name', ''), c.get('domain', ''), c.get('path', '')): c.get('value', '') for c in self._last_cookie_state}
            return current_cookie_set != last_cookie_set
        except Exception as e:
            self.logger.debug(f'[StorageStateWatchdog] Error comparing cookies: {e}')
            return False

    async def _save_storage_state(self, path: str | None=None) -> None:
        async with self._save_lock:
            assert await self.browser_session.get_or_create_cdp_session(target_id=None)
            save_path = path or self.browser_session.browser_profile.storage_state
            if not save_path:
                return
            if isinstance(save_path, dict):
                self.logger.debug('[StorageStateWatchdog] Storage state is already a dict, skipping file save')
                return
            try:
                storage_state = await self.browser_session._cdp_get_storage_state()
                self._last_cookie_state = storage_state.get('cookies', []).copy()
                json_path = Path(save_path).expanduser().resolve()
                json_path.parent.mkdir(parents=True, exist_ok=True)
                merged_state = storage_state
                if json_path.exists():
                    try:
                        existing_state = json.loads(json_path.read_text())
                        merged_state = self._merge_storage_states(existing_state, dict(storage_state))
                    except Exception as e:
                        self.logger.error(f'[StorageStateWatchdog] Failed to merge with existing state: {e}')
                temp_path = json_path.with_suffix('.json.tmp')
                temp_path.write_text(json.dumps(merged_state, indent=4))
                if json_path.exists():
                    backup_path = json_path.with_suffix('.json.bak')
                    json_path.replace(backup_path)
                temp_path.replace(json_path)
                self.event_bus.dispatch(StorageStateSavedEvent(path=str(json_path), cookies_count=len(merged_state.get('cookies', [])), origins_count=len(merged_state.get('origins', []))))
                self.logger.debug(f"[StorageStateWatchdog] Saved storage state to {json_path} ({len(merged_state.get('cookies', []))} cookies, {len(merged_state.get('origins', []))} origins)")
            except Exception as e:
                self.logger.error(f'[StorageStateWatchdog] Failed to save storage state: {e}')

    async def _load_storage_state(self, path: str | None=None) -> None:
        if not self.browser_session.cdp_client:
            self.logger.warning('[StorageStateWatchdog] No CDP client available for loading')
            return
        load_path = path or self.browser_session.browser_profile.storage_state
        if not load_path or not os.path.exists(str(load_path)):
            return
        try:
            import anyio
            content = await anyio.Path(str(load_path)).read_text()
            storage = json.loads(content)
            if 'cookies' in storage and storage['cookies']:
                normalized_cookies: list[Cookie] = []
                for cookie in storage['cookies']:
                    if not isinstance(cookie, dict):
                        normalized_cookies.append(cookie)
                        continue
                    c = dict(cookie)
                    expires = c.get('expires')
                    if expires in (0, 0.0, -1, -1.0):
                        c.pop('expires', None)
                    normalized_cookies.append(Cookie(**c))
                await self.browser_session._cdp_set_cookies(normalized_cookies)
                self._last_cookie_state = storage['cookies'].copy()
                self.logger.debug(f"[StorageStateWatchdog] Added {len(storage['cookies'])} cookies from storage state")
            if 'origins' in storage and storage['origins']:
                for origin in storage['origins']:
                    origin_value = origin.get('origin')
                    if not origin_value:
                        continue
                    if origin.get('localStorage'):
                        lines = []
                        for item in origin['localStorage']:
                            lines.append(f"window.localStorage.setItem({json.dumps(item['name'])}, {json.dumps(item['value'])});")
                        script = f"(function(){{\n  if (window.location && window.location.origin !== {json.dumps(origin_value)}) return;\n  try {{\n    {' '.join(lines)}\n  }} catch (e) {{}}\n}})();"
                        await self.browser_session._cdp_add_init_script(script)
                    if origin.get('sessionStorage'):
                        lines = []
                        for item in origin['sessionStorage']:
                            lines.append(f"window.sessionStorage.setItem({json.dumps(item['name'])}, {json.dumps(item['value'])});")
                        script = f"(function(){{\n  if (window.location && window.location.origin !== {json.dumps(origin_value)}) return;\n  try {{\n    {' '.join(lines)}\n  }} catch (e) {{}}\n}})();"
                        await self.browser_session._cdp_add_init_script(script)
                self.logger.debug(f"[StorageStateWatchdog] Applied localStorage/sessionStorage from {len(storage['origins'])} origins")
            self.event_bus.dispatch(StorageStateLoadedEvent(path=str(load_path), cookies_count=len(storage.get('cookies', [])), origins_count=len(storage.get('origins', []))))
            self.logger.debug(f'[StorageStateWatchdog] Loaded storage state from: {load_path}')
        except Exception as e:
            self.logger.error(f'[StorageStateWatchdog] Failed to load storage state: {e}')

    @staticmethod
    def _merge_storage_states(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        merged = existing.copy()
        existing_cookies = {(c['name'], c['domain'], c['path']): c for c in existing.get('cookies', [])}
        for cookie in new.get('cookies', []):
            key = (cookie['name'], cookie['domain'], cookie['path'])
            existing_cookies[key] = cookie
        merged['cookies'] = list(existing_cookies.values())
        existing_origins = {origin['origin']: origin for origin in existing.get('origins', [])}
        for origin in new.get('origins', []):
            existing_origins[origin['origin']] = origin
        merged['origins'] = list(existing_origins.values())
        return merged

    async def get_current_cookies(self) -> list[dict[str, Any]]:
        if not self.browser_session.cdp_client:
            return []
        try:
            cookies = await self.browser_session._cdp_get_cookies()
            return [dict(cookie) for cookie in cookies]
        except Exception as e:
            self.logger.error(f'[StorageStateWatchdog] Failed to get cookies: {e}')
            return []

    async def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        if not self.browser_session.cdp_client:
            self.logger.warning('[StorageStateWatchdog] No CDP client available for adding cookies')
            return
        try:
            cookie_objects = [Cookie(**cookie_dict) if isinstance(cookie_dict, dict) else cookie_dict for cookie_dict in cookies]
            await self.browser_session._cdp_set_cookies(cookie_objects)
            self.logger.debug(f'[StorageStateWatchdog] Added {len(cookies)} cookies')
        except Exception as e:
            self.logger.error(f'[StorageStateWatchdog] Failed to add cookies: {e}')