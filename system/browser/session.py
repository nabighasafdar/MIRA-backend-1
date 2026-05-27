import asyncio
import logging
import time
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self, Union, cast, overload
from urllib.parse import urlparse, urlunparse
from uuid import UUID
import httpx
from bubus import EventBus
from cdp_use import CDPClient
from cdp_use.cdp.fetch import AuthRequiredEvent, RequestPausedEvent
from cdp_use.cdp.network import Cookie
from cdp_use.cdp.target import SessionID, TargetID
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from uuid_extensions import uuid7str
from system.browser.cloud.cloud import CloudBrowserAuthError, CloudBrowserClient, CloudBrowserError
from system.browser.cloud.views import CloudBrowserParams, CreateBrowserRequest, ProxyCountryCode
from system.browser.events import AgentFocusChangedEvent, BrowserConnectedEvent, BrowserErrorEvent, BrowserLaunchEvent, BrowserLaunchResult, BrowserReconnectedEvent, BrowserReconnectingEvent, BrowserStartEvent, BrowserStateRequestEvent, BrowserStopEvent, BrowserStoppedEvent, CloseTabEvent, FileDownloadedEvent, NavigateToUrlEvent, NavigationCompleteEvent, NavigationStartedEvent, SwitchTabEvent, TabClosedEvent, TabCreatedEvent
from system.browser.profile import BrowserProfile, ProxySettings
from system.browser.views import BrowserStateSummary, TabInfo
from system.dom.views import DOMRect, EnhancedDOMTreeNode, TargetInfo
from system.observability import observe_debug
from system.utils import _log_pretty_url, create_task_with_error_handling, is_new_tab_page
if TYPE_CHECKING:
    from system.actor.page import Page
    from system.browser.watchdogs.captcha_watchdog import CaptchaWaitResult
DEFAULT_BROWSER_PROFILE = BrowserProfile()
_LOGGED_UNIQUE_SESSION_IDS = set()
red = '\x1b[91m'
reset = '\x1b[0m'

class Target(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, revalidate_instances='never')
    target_id: TargetID
    target_type: str
    url: str = 'about:blank'
    title: str = 'Unknown title'

class CDPSession(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, revalidate_instances='never')
    cdp_client: CDPClient
    target_id: TargetID
    session_id: SessionID
    _lifecycle_events: Any = PrivateAttr(default=None)
    _lifecycle_lock: Any = PrivateAttr(default=None)

class BrowserSession(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True, extra='forbid', revalidate_instances='never')

    @overload
    def __init__(self, *, cloud_profile_id: UUID | str | None=None, cloud_proxy_country_code: ProxyCountryCode | None=None, cloud_timeout: int | None=None, profile_id: UUID | str | None=None, proxy_country_code: ProxyCountryCode | None=None, timeout: int | None=None, use_cloud: bool | None=None, cloud_browser: bool | None=None, cloud_browser_params: CloudBrowserParams | None=None, id: str | None=None, headers: dict[str, str] | None=None, allowed_domains: list[str] | None=None, prohibited_domains: list[str] | None=None, keep_alive: bool | None=None, minimum_wait_page_load_time: float | None=None, wait_for_network_idle_page_load_time: float | None=None, wait_between_actions: float | None=None, captcha_solver: bool | None=None, auto_download_pdfs: bool | None=None, cookie_whitelist_domains: list[str] | None=None, cross_origin_iframes: bool | None=None, highlight_elements: bool | None=None, dom_highlight_elements: bool | None=None, paint_order_filtering: bool | None=None, max_iframes: int | None=None, max_iframe_depth: int | None=None) -> None:
        ...

    @overload
    def __init__(self, *, id: str | None=None, cdp_url: str | None=None, browser_profile: BrowserProfile | None=None, executable_path: str | Path | None=None, headless: bool | None=None, user_data_dir: str | Path | None=None, args: list[str] | None=None, downloads_path: str | Path | None=None, headers: dict[str, str] | None=None, allowed_domains: list[str] | None=None, prohibited_domains: list[str] | None=None, keep_alive: bool | None=None, minimum_wait_page_load_time: float | None=None, wait_for_network_idle_page_load_time: float | None=None, wait_between_actions: float | None=None, auto_download_pdfs: bool | None=None, cookie_whitelist_domains: list[str] | None=None, cross_origin_iframes: bool | None=None, highlight_elements: bool | None=None, dom_highlight_elements: bool | None=None, paint_order_filtering: bool | None=None, max_iframes: int | None=None, max_iframe_depth: int | None=None, env: dict[str, str | float | bool] | None=None, ignore_default_args: list[str] | Literal[True] | None=None, channel: str | None=None, chromium_sandbox: bool | None=None, devtools: bool | None=None, traces_dir: str | Path | None=None, accept_downloads: bool | None=None, permissions: list[str] | None=None, user_agent: str | None=None, screen: dict | None=None, viewport: dict | None=None, no_viewport: bool | None=None, device_scale_factor: float | None=None, record_har_content: str | None=None, record_har_mode: str | None=None, record_har_path: str | Path | None=None, record_video_dir: str | Path | None=None, record_video_framerate: int | None=None, record_video_size: dict | None=None, storage_state: str | Path | dict[str, Any] | None=None, disable_security: bool | None=None, deterministic_rendering: bool | None=None, proxy: ProxySettings | None=None, enable_default_extensions: bool | None=None, captcha_solver: bool | None=None, window_size: dict | None=None, window_position: dict | None=None, filter_highlight_ids: bool | None=None, profile_directory: str | None=None) -> None:
        ...

    def __init__(self, id: str | None=None, cdp_url: str | None=None, is_local: bool=False, browser_profile: BrowserProfile | None=None, cloud_profile_id: UUID | str | None=None, cloud_proxy_country_code: ProxyCountryCode | None=None, cloud_timeout: int | None=None, profile_id: UUID | str | None=None, proxy_country_code: ProxyCountryCode | None=None, timeout: int | None=None, headers: dict[str, str] | None=None, env: dict[str, str | float | bool] | None=None, executable_path: str | Path | None=None, headless: bool | None=None, args: list[str] | None=None, ignore_default_args: list[str] | Literal[True] | None=None, channel: str | None=None, chromium_sandbox: bool | None=None, devtools: bool | None=None, downloads_path: str | Path | None=None, traces_dir: str | Path | None=None, accept_downloads: bool | None=None, permissions: list[str] | None=None, user_agent: str | None=None, screen: dict | None=None, viewport: dict | None=None, no_viewport: bool | None=None, device_scale_factor: float | None=None, record_har_content: str | None=None, record_har_mode: str | None=None, record_har_path: str | Path | None=None, record_video_dir: str | Path | None=None, record_video_framerate: int | None=None, record_video_size: dict | None=None, user_data_dir: str | Path | None=None, storage_state: str | Path | dict[str, Any] | None=None, use_cloud: bool | None=None, cloud_browser: bool | None=None, cloud_browser_params: CloudBrowserParams | None=None, disable_security: bool | None=None, deterministic_rendering: bool | None=None, allowed_domains: list[str] | None=None, prohibited_domains: list[str] | None=None, keep_alive: bool | None=None, proxy: ProxySettings | None=None, enable_default_extensions: bool | None=None, captcha_solver: bool | None=None, window_size: dict | None=None, window_position: dict | None=None, minimum_wait_page_load_time: float | None=None, wait_for_network_idle_page_load_time: float | None=None, wait_between_actions: float | None=None, filter_highlight_ids: bool | None=None, auto_download_pdfs: bool | None=None, profile_directory: str | None=None, cookie_whitelist_domains: list[str] | None=None, cross_origin_iframes: bool | None=None, highlight_elements: bool | None=None, dom_highlight_elements: bool | None=None, paint_order_filtering: bool | None=None, max_iframes: int | None=None, max_iframe_depth: int | None=None):
        profile_kwargs = {k: v for k, v in locals().items() if k not in ['self', 'browser_profile', 'id', 'cloud_profile_id', 'cloud_proxy_country_code', 'cloud_timeout', 'profile_id', 'proxy_country_code', 'timeout'] and v is not None}
        final_profile_id = cloud_profile_id if cloud_profile_id is not None else profile_id
        final_proxy_country_code = cloud_proxy_country_code if cloud_proxy_country_code is not None else proxy_country_code
        final_timeout = cloud_timeout if cloud_timeout is not None else timeout
        if final_profile_id is not None or final_proxy_country_code is not None or final_timeout is not None:
            cloud_params = CreateBrowserRequest(cloud_profile_id=final_profile_id, cloud_proxy_country_code=final_proxy_country_code, cloud_timeout=final_timeout)
            profile_kwargs['cloud_browser_params'] = cloud_params
            profile_kwargs['use_cloud'] = True
        if 'cloud_browser' in profile_kwargs:
            profile_kwargs['use_cloud'] = profile_kwargs.pop('cloud_browser')
        if cloud_browser_params is not None:
            profile_kwargs['use_cloud'] = True
        if is_local is False and executable_path is not None:
            profile_kwargs['is_local'] = True
        use_cloud = profile_kwargs.get('use_cloud') or profile_kwargs.get('cloud_browser')
        if not cdp_url and (not use_cloud):
            profile_kwargs['is_local'] = True
        if browser_profile is not None:
            merged_kwargs = {**browser_profile.model_dump(exclude_unset=True), **profile_kwargs}
            resolved_browser_profile = BrowserProfile(**merged_kwargs)
        else:
            resolved_browser_profile = BrowserProfile(**profile_kwargs)
        super().__init__(id=id or str(uuid7str()), browser_profile=resolved_browser_profile)
    id: str = Field(default_factory=lambda: str(uuid7str()), description='Unique identifier for this browser session')
    browser_profile: BrowserProfile = Field(default_factory=lambda: DEFAULT_BROWSER_PROFILE, description='BrowserProfile() options to use for the session, otherwise a default profile will be used')
    llm_screenshot_size: tuple[int, int] | None = Field(default=None, description='Target size (width, height) to resize screenshots before sending to LLM. Coordinates from LLM will be scaled back to original viewport size.')
    _original_viewport_size: tuple[int, int] | None = PrivateAttr(default=None)

    @classmethod
    def from_system_chrome(cls, profile_directory: str | None=None, **kwargs: Any) -> Self:
        from system.skill_cli.utils import find_chrome_executable, get_chrome_profile_path, list_chrome_profiles
        executable_path = find_chrome_executable()
        if executable_path is None:
            raise RuntimeError('Chrome not found. Please install Chrome or use Browser() with explicit executable_path.\nExpected locations:\n  macOS: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n  Linux: /usr/bin/google-chrome or /usr/bin/chromium\n  Windows: C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe')
        user_data_dir = get_chrome_profile_path(None)
        if user_data_dir is None:
            raise RuntimeError('Could not detect Chrome profile directory for your platform.\nExpected locations:\n  macOS: ~/Library/Application Support/Google/Chrome\n  Linux: ~/.config/google-chrome\n  Windows: %LocalAppData%\\Google\\Chrome\\User Data')
        profiles = list_chrome_profiles()
        if profile_directory is None:
            if profiles:
                profile_directory = profiles[0]['directory']
                logging.getLogger('system').info(f"Auto-selected Chrome profile: {profiles[0]['name']} ({profile_directory})")
            else:
                profile_directory = 'Default'
        return cls(executable_path=executable_path, user_data_dir=user_data_dir, profile_directory=profile_directory, **kwargs)

    @classmethod
    def list_chrome_profiles(cls) -> list[dict[str, str]]:
        from system.skill_cli.utils import list_chrome_profiles
        return list_chrome_profiles()

    @property
    def cdp_url(self) -> str | None:
        return self.browser_profile.cdp_url

    @property
    def is_local(self) -> bool:
        return self.browser_profile.is_local

    @property
    def is_cdp_connected(self) -> bool:
        if self._cdp_client_root is None or self._cdp_client_root.ws is None:
            return False
        try:
            from websockets.protocol import State
            return self._cdp_client_root.ws.state is State.OPEN
        except Exception:
            return False

    async def wait_if_captcha_solving(self, timeout: float | None=None) -> 'CaptchaWaitResult | None':
        if self._captcha_watchdog is not None:
            return await self._captcha_watchdog.wait_if_captcha_solving(timeout=timeout)
        return None

    @property
    def is_reconnecting(self) -> bool:
        return self._reconnecting

    @property
    def cloud_browser(self) -> bool:
        return self.browser_profile.use_cloud
    event_bus: EventBus = Field(default_factory=EventBus)
    agent_focus_target_id: TargetID | None = None
    _cdp_client_root: CDPClient | None = PrivateAttr(default=None)
    _connection_lock: Any = PrivateAttr(default=None)
    session_manager: Any = Field(default=None, exclude=True)
    _cached_browser_state_summary: Any = PrivateAttr(default=None)
    _cached_selector_map: dict[int, EnhancedDOMTreeNode] = PrivateAttr(default_factory=dict)
    _downloaded_files: list[str] = PrivateAttr(default_factory=list)
    _closed_popup_messages: list[str] = PrivateAttr(default_factory=list)
    _crash_watchdog: Any | None = PrivateAttr(default=None)
    _downloads_watchdog: Any | None = PrivateAttr(default=None)
    _aboutblank_watchdog: Any | None = PrivateAttr(default=None)
    _security_watchdog: Any | None = PrivateAttr(default=None)
    _storage_state_watchdog: Any | None = PrivateAttr(default=None)
    _local_browser_watchdog: Any | None = PrivateAttr(default=None)
    _default_action_watchdog: Any | None = PrivateAttr(default=None)
    _dom_watchdog: Any | None = PrivateAttr(default=None)
    _screenshot_watchdog: Any | None = PrivateAttr(default=None)
    _permissions_watchdog: Any | None = PrivateAttr(default=None)
    _recording_watchdog: Any | None = PrivateAttr(default=None)
    _captcha_watchdog: Any | None = PrivateAttr(default=None)
    _watchdogs_attached: bool = PrivateAttr(default=False)
    _cloud_browser_client: CloudBrowserClient = PrivateAttr(default_factory=lambda: CloudBrowserClient())
    RECONNECT_WAIT_TIMEOUT: float = 54.0
    _reconnecting: bool = PrivateAttr(default=False)
    _reconnect_event: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
    _reconnect_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)
    _reconnect_task: asyncio.Task | None = PrivateAttr(default=None)
    _intentional_stop: bool = PrivateAttr(default=False)
    _logger: Any = PrivateAttr(default=None)

    @property
    def logger(self) -> Any:
        return logging.getLogger(f'system.{self}')

    @cached_property
    def _id_for_logs(self) -> str:
        str_id = self.id[-4:]
        port_number = (self.cdp_url or 'no-cdp').rsplit(':', 1)[-1].split('/', 1)[0].strip()
        port_is_random = not port_number.startswith('922')
        port_is_unique_enough = port_number not in _LOGGED_UNIQUE_SESSION_IDS
        if port_number and port_number.isdigit() and port_is_random and port_is_unique_enough:
            _LOGGED_UNIQUE_SESSION_IDS.add(port_number)
            str_id = port_number
        return str_id

    @property
    def _tab_id_for_logs(self) -> str:
        return self.agent_focus_target_id[-2:] if self.agent_focus_target_id else f'{red}--{reset}'

    def __repr__(self) -> str:
        return f'BrowserSession🅑 {self._id_for_logs} 🅣 {self._tab_id_for_logs} (cdp_url={self.cdp_url}, profile={self.browser_profile})'

    def __str__(self) -> str:
        return f'BrowserSession🅑 {self._id_for_logs} 🅣 {self._tab_id_for_logs}'

    async def reset(self) -> None:
        self._intentional_stop = True
        if self._reconnect_task and (not self._reconnect_task.done()):
            self._reconnect_task.cancel()
            self._reconnect_task = None
        self._reconnecting = False
        self._reconnect_event.set()
        cdp_status = 'connected' if self._cdp_client_root else 'not connected'
        session_mgr_status = 'exists' if self.session_manager else 'None'
        self.logger.debug(f"🔄 Resetting browser session (CDP: {cdp_status}, SessionManager: {session_mgr_status}, focus: {(self.agent_focus_target_id[-4:] if self.agent_focus_target_id else 'None')})")
        if self.session_manager:
            await self.session_manager.clear()
            self.session_manager = None
        if self._cdp_client_root:
            try:
                await self._cdp_client_root.stop()
                self.logger.debug('Closed CDP client WebSocket during reset')
            except Exception as e:
                self.logger.debug(f'Error closing CDP client during reset: {e}')
        self._cdp_client_root = None
        self._cached_browser_state_summary = None
        self._cached_selector_map.clear()
        self._downloaded_files.clear()
        self.agent_focus_target_id = None
        if self.is_local:
            self.browser_profile.cdp_url = None
        self._crash_watchdog = None
        self._downloads_watchdog = None
        self._aboutblank_watchdog = None
        self._security_watchdog = None
        self._storage_state_watchdog = None
        self._local_browser_watchdog = None
        self._default_action_watchdog = None
        self._dom_watchdog = None
        self._screenshot_watchdog = None
        self._permissions_watchdog = None
        self._recording_watchdog = None
        self._captcha_watchdog = None
        self._watchdogs_attached = False
        self._intentional_stop = False
        self.logger.info('✅ Browser session reset complete')

    def model_post_init(self, __context) -> None:
        self._connection_lock = asyncio.Lock()
        self._reconnect_event = asyncio.Event()
        self._reconnect_event.set()
        from system.browser.watchdog_base import BaseWatchdog
        start_handlers = self.event_bus.handlers.get('BrowserStartEvent', [])
        start_handler_names = [getattr(h, '__name__', str(h)) for h in start_handlers]
        if any(('on_BrowserStartEvent' in name for name in start_handler_names)):
            raise RuntimeError('[BrowserSession] Duplicate handler registration attempted! on_BrowserStartEvent is already registered. This likely means BrowserSession was initialized multiple times with the same EventBus.')
        BaseWatchdog.attach_handler_to_session(self, BrowserStartEvent, self.on_BrowserStartEvent)
        BaseWatchdog.attach_handler_to_session(self, BrowserStopEvent, self.on_BrowserStopEvent)
        BaseWatchdog.attach_handler_to_session(self, NavigateToUrlEvent, self.on_NavigateToUrlEvent)
        BaseWatchdog.attach_handler_to_session(self, SwitchTabEvent, self.on_SwitchTabEvent)
        BaseWatchdog.attach_handler_to_session(self, TabCreatedEvent, self.on_TabCreatedEvent)
        BaseWatchdog.attach_handler_to_session(self, TabClosedEvent, self.on_TabClosedEvent)
        BaseWatchdog.attach_handler_to_session(self, AgentFocusChangedEvent, self.on_AgentFocusChangedEvent)
        BaseWatchdog.attach_handler_to_session(self, FileDownloadedEvent, self.on_FileDownloadedEvent)
        BaseWatchdog.attach_handler_to_session(self, CloseTabEvent, self.on_CloseTabEvent)

    @observe_debug(ignore_input=True, ignore_output=True, name='browser_session_start')
    async def start(self) -> None:
        start_event = self.event_bus.dispatch(BrowserStartEvent())
        await start_event
        await start_event.event_result(raise_if_any=True, raise_if_none=False)

    async def kill(self) -> None:
        self._intentional_stop = True
        self.logger.debug('🛑 kill() called - stopping browser with force=True and resetting state')
        from system.browser.events import SaveStorageStateEvent
        save_event = self.event_bus.dispatch(SaveStorageStateEvent())
        await save_event
        await self.event_bus.dispatch(BrowserStopEvent(force=True))
        await self.event_bus.stop(clear=True, timeout=5)
        await self.reset()
        self.event_bus = EventBus()

    async def stop(self) -> None:
        self._intentional_stop = True
        self.logger.debug('⏸️  stop() called - stopping browser gracefully (force=False) and resetting state')
        from system.browser.events import SaveStorageStateEvent
        save_event = self.event_bus.dispatch(SaveStorageStateEvent())
        await save_event
        await self.event_bus.dispatch(BrowserStopEvent(force=False))
        await self.event_bus.stop(clear=True, timeout=5)
        await self.reset()
        self.event_bus = EventBus()

    @observe_debug(ignore_input=True, ignore_output=True, name='browser_start_event_handler')
    async def on_BrowserStartEvent(self, event: BrowserStartEvent) -> dict[str, str]:
        await self.attach_all_watchdogs()
        try:
            if not self.cdp_url:
                if self.browser_profile.use_cloud or self.browser_profile.cloud_browser_params is not None:
                    try:
                        cloud_params = self.browser_profile.cloud_browser_params or CreateBrowserRequest()
                        cloud_browser_response = await self._cloud_browser_client.create_browser(cloud_params)
                        self.browser_profile.cdp_url = cloud_browser_response.cdpUrl
                        self.browser_profile.is_local = False
                        self.logger.info('🌤️ Successfully connected to cloud browser service')
                    except CloudBrowserAuthError:
                        raise CloudBrowserAuthError('Authentication failed for cloud browser service. Set system_API_KEY environment variable. You can also create an API key at https://cloud.browser-use.com/new-api-key')
                    except CloudBrowserError as e:
                        raise CloudBrowserError(f'Failed to create cloud browser: {e}')
                elif self.is_local:
                    launch_event = self.event_bus.dispatch(BrowserLaunchEvent())
                    await launch_event
                    launch_result: BrowserLaunchResult = cast(BrowserLaunchResult, await launch_event.event_result(raise_if_none=True, raise_if_any=True))
                    self.browser_profile.cdp_url = launch_result.cdp_url
                else:
                    raise ValueError('Got BrowserSession(is_local=False) but no cdp_url was provided to connect to!')
            assert self.cdp_url and '://' in self.cdp_url
            async with self._connection_lock:
                if self._cdp_client_root is None:
                    try:
                        await asyncio.wait_for(self.connect(cdp_url=self.cdp_url), timeout=15.0)
                    except TimeoutError:
                        cdp_client = cast(CDPClient | None, self._cdp_client_root)
                        if cdp_client is not None:
                            try:
                                await cdp_client.stop()
                            except Exception:
                                pass
                            self._cdp_client_root = None
                        manager = self.session_manager
                        if manager is not None:
                            try:
                                await manager.clear()
                            except Exception:
                                pass
                            self.session_manager = None
                        self.agent_focus_target_id = None
                        raise RuntimeError(f'connect() timed out after 15s — CDP connection to {self.cdp_url} is too slow or unresponsive')
                    assert self.cdp_client is not None
                    await self.event_bus.dispatch(BrowserConnectedEvent(cdp_url=self.cdp_url))
                    pass
            return {'cdp_url': self.cdp_url}
        except Exception as e:
            self.event_bus.dispatch(BrowserErrorEvent(error_type='BrowserStartEventError', message=f'Failed to start browser: {type(e).__name__} {e}', details={'cdp_url': self.cdp_url, 'is_local': self.is_local}))
            raise

    async def on_NavigateToUrlEvent(self, event: NavigateToUrlEvent) -> None:
        self.logger.debug(f'[on_NavigateToUrlEvent] Received NavigateToUrlEvent: url={event.url}, new_tab={event.new_tab}')
        if not self.agent_focus_target_id:
            self.logger.warning('Cannot navigate - browser not connected')
            return
        target_id = None
        current_target_id = self.agent_focus_target_id
        current_target = self.session_manager.get_target(current_target_id)
        if event.new_tab and is_new_tab_page(current_target.url):
            self.logger.debug(f'[on_NavigateToUrlEvent] Already on blank tab ({current_target.url}), reusing')
            event.new_tab = False
        try:
            self.logger.debug(f'[on_NavigateToUrlEvent] Processing new_tab={event.new_tab}')
            if event.new_tab:
                page_targets = self.session_manager.get_all_page_targets()
                self.logger.debug(f'[on_NavigateToUrlEvent] Found {len(page_targets)} existing tabs')
                for idx, target in enumerate(page_targets):
                    self.logger.debug(f'[on_NavigateToUrlEvent] Tab {idx}: url={target.url}, targetId={target.target_id}')
                    if target.url == 'about:blank' and target.target_id != current_target_id:
                        target_id = target.target_id
                        self.logger.debug(f'Reusing existing about:blank tab #{target_id[-4:]}')
                        break
                if not target_id:
                    self.logger.debug('[on_NavigateToUrlEvent] No reusable about:blank tab found, creating new tab...')
                    try:
                        target_id = await self._cdp_create_new_page('about:blank')
                        self.logger.debug(f'Created new tab #{target_id[-4:]}')
                        await self.event_bus.dispatch(TabCreatedEvent(target_id=target_id, url='about:blank'))
                    except Exception as e:
                        self.logger.error(f'[on_NavigateToUrlEvent] Failed to create new tab: {type(e).__name__}: {e}')
                        target_id = current_target_id
                        self.logger.warning(f'[on_NavigateToUrlEvent] Falling back to current tab #{target_id[-4:]}')
            else:
                target_id = target_id or current_target_id
            if self.agent_focus_target_id is None or self.agent_focus_target_id != target_id:
                self.logger.debug(f"[on_NavigateToUrlEvent] Switching to target tab {target_id[-4:]} (current: {(self.agent_focus_target_id[-4:] if self.agent_focus_target_id else 'none')})")
                await self.event_bus.dispatch(SwitchTabEvent(target_id=target_id))
            else:
                self.logger.debug(f'[on_NavigateToUrlEvent] Already on target tab {target_id[-4:]}, skipping SwitchTabEvent')
            assert self.agent_focus_target_id is not None and self.agent_focus_target_id == target_id, 'Agent focus not updated to new target_id after SwitchTabEvent should have switched to it'
            await self.event_bus.dispatch(NavigationStartedEvent(target_id=target_id, url=event.url))
            await self._navigate_and_wait(event.url, target_id, wait_until=event.wait_until)
            await self._close_extension_options_pages()
            self.logger.debug(f'Dispatching NavigationCompleteEvent for {event.url} (tab #{target_id[-4:]})')
            await self.event_bus.dispatch(NavigationCompleteEvent(target_id=target_id, url=event.url, status=None))
            await self.event_bus.dispatch(AgentFocusChangedEvent(target_id=target_id, url=event.url))
        except Exception as e:
            self.logger.error(f'Navigation failed: {type(e).__name__}: {e}')
            if 'target_id' in locals() and target_id:
                await self.event_bus.dispatch(NavigationCompleteEvent(target_id=target_id, url=event.url, error_message=f'{type(e).__name__}: {e}'))
                await self.event_bus.dispatch(AgentFocusChangedEvent(target_id=target_id, url=event.url))
            raise

    async def _navigate_and_wait(self, url: str, target_id: str, timeout: float | None=None, wait_until: str='load') -> None:
        cdp_session = await self.get_or_create_cdp_session(target_id, focus=False)
        if timeout is None:
            target = self.session_manager.get_target(target_id)
            current_url = target.url
            same_domain = url.split('/')[2] == current_url.split('/')[2] if url.startswith('http') and current_url.startswith('http') else False
            timeout = 3.0 if same_domain else 8.0
        nav_start_time = asyncio.get_event_loop().time()
        nav_timeout = 20.0
        try:
            nav_result = await asyncio.wait_for(cdp_session.cdp_client.send.Page.navigate(params={'url': url, 'transitionType': 'address_bar'}, session_id=cdp_session.session_id), timeout=nav_timeout)
        except TimeoutError:
            duration_ms = (asyncio.get_event_loop().time() - nav_start_time) * 1000
            raise RuntimeError(f'Page.navigate() timed out after {nav_timeout}s ({duration_ms:.0f}ms) for {url}')
        if nav_result.get('errorText'):
            raise RuntimeError(f"Navigation failed: {nav_result['errorText']}")
        if wait_until == 'commit':
            duration_ms = (asyncio.get_event_loop().time() - nav_start_time) * 1000
            self.logger.debug(f'✅ Page ready for {url} (commit, {duration_ms:.0f}ms)')
            return
        navigation_id = nav_result.get('loaderId')
        start_time = asyncio.get_event_loop().time()
        seen_events = []
        if not hasattr(cdp_session, '_lifecycle_events'):
            raise RuntimeError(f'❌ Lifecycle monitoring not enabled for {cdp_session.target_id[:8]}! This is a bug - SessionManager should have initialized it. Session: {cdp_session}')
        acceptable_events: set[str] = {'networkIdle'}
        if wait_until in ('load', 'domcontentloaded'):
            acceptable_events.add('load')
        if wait_until == 'domcontentloaded':
            acceptable_events.add('DOMContentLoaded')
        poll_interval = 0.05
        while asyncio.get_event_loop().time() - start_time < timeout:
            try:
                for event_data in list(cdp_session._lifecycle_events):
                    event_name = event_data.get('name')
                    event_loader_id = event_data.get('loaderId')
                    event_str = f"{event_name}(loader={(event_loader_id[:8] if event_loader_id else 'none')})"
                    if event_str not in seen_events:
                        seen_events.append(event_str)
                    if event_loader_id and navigation_id and (event_loader_id != navigation_id):
                        continue
                    if event_name in acceptable_events:
                        duration_ms = (asyncio.get_event_loop().time() - nav_start_time) * 1000
                        self.logger.debug(f'✅ Page ready for {url} ({event_name}, {duration_ms:.0f}ms)')
                        return
            except Exception as e:
                self.logger.debug(f'Error polling lifecycle events: {e}')
            await asyncio.sleep(poll_interval)
        duration_ms = (asyncio.get_event_loop().time() - nav_start_time) * 1000
        if not seen_events:
            self.logger.error(f'❌ No lifecycle events received for {url} after {duration_ms:.0f}ms! Monitoring may have failed. Target: {cdp_session.target_id[:8]}')
        else:
            self.logger.warning(f'⚠️ Page readiness timeout ({timeout}s, {duration_ms:.0f}ms) for {url}')

    async def on_SwitchTabEvent(self, event: SwitchTabEvent) -> TargetID:
        if not self.agent_focus_target_id:
            raise RuntimeError('Cannot switch tabs - browser not connected')
        page_targets = self.session_manager.get_all_page_targets()
        if event.target_id is None:
            if page_targets:
                event.target_id = page_targets[-1].target_id
            else:
                assert self._cdp_client_root is not None, 'CDP client root not initialized - browser may not be connected yet'
                new_target = await self._cdp_client_root.send.Target.createTarget(params={'url': 'about:blank'})
                target_id = new_target['targetId']
                self.event_bus.dispatch(TabCreatedEvent(url='about:blank', target_id=target_id))
                self.event_bus.dispatch(AgentFocusChangedEvent(target_id=target_id, url='about:blank'))
                return target_id
        assert event.target_id is not None, 'target_id must be set at this point'
        cdp_session = await self.get_or_create_cdp_session(target_id=event.target_id, focus=True)
        await cdp_session.cdp_client.send.Target.activateTarget(params={'targetId': event.target_id})
        target = self.session_manager.get_target(event.target_id)
        await self.event_bus.dispatch(AgentFocusChangedEvent(target_id=target.target_id, url=target.url))
        return target.target_id

    async def on_CloseTabEvent(self, event: CloseTabEvent) -> None:
        try:
            await self.event_bus.dispatch(TabClosedEvent(target_id=event.target_id))
            try:
                cdp_session = await self.get_or_create_cdp_session(target_id=None, focus=False)
                await cdp_session.cdp_client.send.Target.closeTarget(params={'targetId': event.target_id})
            except Exception as e:
                self.logger.debug(f'Target may already be closed: {e}')
        except Exception as e:
            self.logger.warning(f'Error during tab close cleanup: {e}')

    async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
        if self.browser_profile.viewport and (not self.browser_profile.no_viewport):
            try:
                viewport_width = self.browser_profile.viewport.width
                viewport_height = self.browser_profile.viewport.height
                device_scale_factor = self.browser_profile.device_scale_factor or 1.0
                self.logger.info(f'Setting viewport to {viewport_width}x{viewport_height} with device scale factor {device_scale_factor} whereas original device scale factor was {self.browser_profile.device_scale_factor}')
                await self._cdp_set_viewport(viewport_width, viewport_height, device_scale_factor, target_id=event.target_id)
                self.logger.debug(f'Applied viewport {viewport_width}x{viewport_height} to tab {event.target_id[-8:]}')
            except Exception as e:
                self.logger.warning(f'Failed to set viewport for new tab {event.target_id[-8:]}: {e}')

    async def on_TabClosedEvent(self, event: TabClosedEvent) -> None:
        if not self.agent_focus_target_id:
            return
        current_target_id = self.agent_focus_target_id
        if current_target_id == event.target_id:
            await self.event_bus.dispatch(SwitchTabEvent(target_id=None))

    async def on_AgentFocusChangedEvent(self, event: AgentFocusChangedEvent) -> None:
        self.logger.debug(f'🔄 AgentFocusChangedEvent received: target_id=...{event.target_id[-4:]} url={event.url}')
        if self._dom_watchdog:
            self._dom_watchdog.clear_cache()
        self._cached_browser_state_summary = None
        self._cached_selector_map.clear()
        self.logger.debug('🔄 Cached browser state cleared')
        if event.target_id:
            await self.get_or_create_cdp_session(target_id=event.target_id, focus=True)
            if self.browser_profile.viewport and (not self.browser_profile.no_viewport):
                try:
                    viewport_width = self.browser_profile.viewport.width
                    viewport_height = self.browser_profile.viewport.height
                    device_scale_factor = self.browser_profile.device_scale_factor or 1.0
                    await self._cdp_set_viewport(viewport_width, viewport_height, device_scale_factor, target_id=event.target_id)
                    self.logger.debug(f'Applied viewport {viewport_width}x{viewport_height} to tab {event.target_id[-8:]}')
                except Exception as e:
                    self.logger.warning(f'Failed to set viewport for tab {event.target_id[-8:]}: {e}')
        else:
            raise RuntimeError('AgentFocusChangedEvent received with no target_id for newly focused tab')

    async def on_FileDownloadedEvent(self, event: FileDownloadedEvent) -> None:
        self.logger.debug(f'FileDownloadedEvent received: {event.file_name} at {event.path}')
        if event.path and event.path not in self._downloaded_files:
            self._downloaded_files.append(event.path)
            self.logger.info(f'📁 Tracked download: {event.file_name} ({len(self._downloaded_files)} total downloads in session)')
        elif not event.path:
            self.logger.warning(f'FileDownloadedEvent has no path: {event}')
        else:
            self.logger.debug(f'File already tracked: {event.path}')

    async def on_BrowserStopEvent(self, event: BrowserStopEvent) -> None:
        try:
            if self.browser_profile.keep_alive and (not event.force):
                self.event_bus.dispatch(BrowserStoppedEvent(reason='Kept alive due to keep_alive=True'))
                return
            if self.browser_profile.use_cloud:
                try:
                    await self._cloud_browser_client.stop_browser()
                    self.logger.info('🌤️ Cloud browser session cleaned up')
                except Exception as e:
                    self.logger.debug(f'Failed to cleanup cloud browser session: {e}')
            self.logger.info(f'📢 on_BrowserStopEvent - Calling reset() (force={event.force}, keep_alive={self.browser_profile.keep_alive})')
            await self.reset()
            if self.is_local:
                self.browser_profile.cdp_url = None
            stop_event = self.event_bus.dispatch(BrowserStoppedEvent(reason='Stopped by request'))
            await stop_event
        except Exception as e:
            self.event_bus.dispatch(BrowserErrorEvent(error_type='BrowserStopEventError', message=f'Failed to stop browser: {type(e).__name__} {e}', details={'cdp_url': self.cdp_url, 'is_local': self.is_local}))

    @property
    def cdp_client(self) -> CDPClient:
        assert self._cdp_client_root is not None, 'CDP client not initialized - browser may not be connected yet'
        return self._cdp_client_root

    async def new_page(self, url: str | None=None) -> 'Page':
        from cdp_use.cdp.target.commands import CreateTargetParameters
        params: CreateTargetParameters = {'url': url or 'about:blank'}
        result = await self.cdp_client.send.Target.createTarget(params)
        target_id = result['targetId']
        from system.actor.page import Page as Target
        return Target(self, target_id)

    async def get_current_page(self) -> 'Page | None':
        target_info = await self.get_current_target_info()
        if not target_info:
            return None
        from system.actor.page import Page as Target
        return Target(self, target_info['targetId'])

    async def must_get_current_page(self) -> 'Page':
        page = await self.get_current_page()
        if not page:
            raise RuntimeError('No current target found')
        return page

    async def get_pages(self) -> list['Page']:
        from system.actor.page import Page as PageActor
        page_targets = self.session_manager.get_all_page_targets() if self.session_manager else []
        targets = []
        for target in page_targets:
            targets.append(PageActor(self, target.target_id))
        return targets

    def get_focused_target(self) -> 'Target | None':
        if not self.session_manager:
            return None
        return self.session_manager.get_focused_target()

    def get_page_targets(self) -> list['Target']:
        if not self.session_manager:
            return []
        return self.session_manager.get_all_page_targets()

    async def close_page(self, page: 'Union[Page, str]') -> None:
        from cdp_use.cdp.target.commands import CloseTargetParameters
        from system.actor.page import Page as Target
        if isinstance(page, Target):
            target_id = page._target_id
        else:
            target_id = str(page)
        params: CloseTargetParameters = {'targetId': target_id}
        await self.cdp_client.send.Target.closeTarget(params)

    async def cookies(self) -> list['Cookie']:
        result = await self.cdp_client.send.Storage.getCookies()
        return result['cookies']

    async def clear_cookies(self) -> None:
        await self.cdp_client.send.Network.clearBrowserCookies()

    async def export_storage_state(self, output_path: str | Path | None=None) -> dict[str, Any]:
        from pathlib import Path
        cookies = await self._cdp_get_cookies()
        storage_state = {'cookies': [{'name': c['name'], 'value': c['value'], 'domain': c['domain'], 'path': c['path'], 'expires': c.get('expires', -1), 'httpOnly': c.get('httpOnly', False), 'secure': c.get('secure', False), 'sameSite': c.get('sameSite', 'Lax')} for c in cookies], 'origins': []}
        if output_path:
            import json
            output_file = Path(output_path).expanduser().resolve()
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(json.dumps(storage_state, indent=2))
            self.logger.info(f'💾 Exported {len(cookies)} cookies to {output_file}')
        return storage_state

    async def get_or_create_cdp_session(self, target_id: TargetID | None=None, focus: bool=True) -> CDPSession:
        assert self._cdp_client_root is not None, 'Root CDP client not initialized'
        assert self.session_manager is not None, 'SessionManager not initialized'
        if target_id is None:
            focus_valid = await self.session_manager.ensure_valid_focus(timeout=5.0)
            if not focus_valid:
                raise ValueError('No valid agent focus available - target may have detached and recovery failed. This indicates browser is in an unstable state.')
            assert self.agent_focus_target_id is not None, 'Focus validation passed but agent_focus_target_id is None'
            target_id = self.agent_focus_target_id
        session = self.session_manager._get_session_for_target(target_id)
        if not session:
            self.logger.debug(f'[SessionManager] Waiting for target {target_id[:8]}... to attach...')
            for attempt in range(20):
                await asyncio.sleep(0.1)
                session = self.session_manager._get_session_for_target(target_id)
                if session:
                    self.logger.debug(f'[SessionManager] Target appeared after {attempt * 100}ms')
                    break
            if not session:
                raise ValueError(f'Target {target_id} not found - may have detached or never existed')
        is_valid = await self.session_manager.validate_session(target_id)
        if not is_valid:
            raise ValueError(f'Target {target_id} has detached - no active sessions')
        if focus and self.agent_focus_target_id != target_id:
            target = self.session_manager.get_target(target_id)
            target_type = target.target_type if target else 'unknown'
            if target_type == 'page':
                current_focus = self.agent_focus_target_id[:8] if self.agent_focus_target_id else 'None'
                self.logger.debug(f'[SessionManager] Switching focus: {current_focus}... → {target_id[:8]}...')
                self.agent_focus_target_id = target_id
            else:
                current_focus = self.agent_focus_target_id[:8] if self.agent_focus_target_id else 'None'
                self.logger.debug(f'[SessionManager] Ignoring focus request for {target_type} target {target_id[:8]}... (agent_focus stays on {current_focus}...)')
        if focus:
            try:
                await asyncio.wait_for(session.cdp_client.send.Runtime.runIfWaitingForDebugger(session_id=session.session_id), timeout=3.0)
            except Exception:
                pass
        return session

    async def set_extra_headers(self, headers: dict[str, str], target_id: TargetID | None=None) -> None:
        if target_id is None:
            if not self.agent_focus_target_id:
                return
            target_id = self.agent_focus_target_id
        cdp_session = await self.get_or_create_cdp_session(target_id, focus=False)
        await cdp_session.cdp_client.send.Network.enable(session_id=cdp_session.session_id)
        await cdp_session.cdp_client.send.Network.setExtraHTTPHeaders(params={'headers': cast(Any, headers)}, session_id=cdp_session.session_id)

    @observe_debug(ignore_input=True, ignore_output=True, name='get_browser_state_summary')
    async def get_browser_state_summary(self, include_screenshot: bool=True, cached: bool=False, include_recent_events: bool=False) -> BrowserStateSummary:
        if cached and self._cached_browser_state_summary is not None and self._cached_browser_state_summary.dom_state:
            selector_map = self._cached_browser_state_summary.dom_state.selector_map
            if include_screenshot and (not self._cached_browser_state_summary.screenshot):
                self.logger.debug('⚠️ Cached browser state has no screenshot, fetching fresh state with screenshot')
            elif selector_map and len(selector_map) > 0:
                self.logger.debug('🔄 Using pre-cached browser state summary for open tab')
                return self._cached_browser_state_summary
            else:
                self.logger.debug('⚠️ Cached browser state has 0 interactive elements, fetching fresh state')
        event: BrowserStateRequestEvent = cast(BrowserStateRequestEvent, self.event_bus.dispatch(BrowserStateRequestEvent(include_dom=True, include_screenshot=include_screenshot, include_recent_events=include_recent_events)))
        result = await event.event_result(raise_if_none=True, raise_if_any=True)
        assert result is not None and result.dom_state is not None
        return result

    async def get_state_as_text(self) -> str:
        state = await self.get_browser_state_summary()
        assert state.dom_state is not None
        dom_state = state.dom_state
        return dom_state.llm_representation()

    async def attach_all_watchdogs(self) -> None:
        if self._watchdogs_attached:
            self.logger.debug('Watchdogs already attached, skipping duplicate attachment')
            return
        from system.browser.watchdogs.aboutblank_watchdog import AboutBlankWatchdog
        from system.browser.watchdogs.captcha_watchdog import CaptchaWatchdog
        from system.browser.watchdogs.default_action_watchdog import DefaultActionWatchdog
        from system.browser.watchdogs.dom_watchdog import DOMWatchdog
        from system.browser.watchdogs.downloads_watchdog import DownloadsWatchdog
        from system.browser.watchdogs.har_recording_watchdog import HarRecordingWatchdog
        from system.browser.watchdogs.local_browser_watchdog import LocalBrowserWatchdog
        from system.browser.watchdogs.permissions_watchdog import PermissionsWatchdog
        from system.browser.watchdogs.popups_watchdog import PopupsWatchdog
        from system.browser.watchdogs.recording_watchdog import RecordingWatchdog
        from system.browser.watchdogs.screenshot_watchdog import ScreenshotWatchdog
        from system.browser.watchdogs.security_watchdog import SecurityWatchdog
        from system.browser.watchdogs.storage_state_watchdog import StorageStateWatchdog
        DownloadsWatchdog.model_rebuild()
        self._downloads_watchdog = DownloadsWatchdog(event_bus=self.event_bus, browser_session=self)
        self._downloads_watchdog.attach_to_session()
        if self.browser_profile.auto_download_pdfs:
            self.logger.debug('📄 PDF auto-download enabled for this session')
        should_enable_storage_state = self.browser_profile.storage_state is not None or self.browser_profile.user_data_dir is not None
        if should_enable_storage_state:
            StorageStateWatchdog.model_rebuild()
            self._storage_state_watchdog = StorageStateWatchdog(event_bus=self.event_bus, browser_session=self, auto_save_interval=60.0, save_on_change=False)
            self._storage_state_watchdog.attach_to_session()
            self.logger.debug(f'🍪 StorageStateWatchdog enabled (storage_state: {bool(self.browser_profile.storage_state)}, user_data_dir: {bool(self.browser_profile.user_data_dir)})')
        else:
            self.logger.debug('🍪 StorageStateWatchdog disabled (no storage_state or user_data_dir configured)')
        LocalBrowserWatchdog.model_rebuild()
        self._local_browser_watchdog = LocalBrowserWatchdog(event_bus=self.event_bus, browser_session=self)
        self._local_browser_watchdog.attach_to_session()
        SecurityWatchdog.model_rebuild()
        self._security_watchdog = SecurityWatchdog(event_bus=self.event_bus, browser_session=self)
        self._security_watchdog.attach_to_session()
        AboutBlankWatchdog.model_rebuild()
        self._aboutblank_watchdog = AboutBlankWatchdog(event_bus=self.event_bus, browser_session=self)
        self._aboutblank_watchdog.attach_to_session()
        PopupsWatchdog.model_rebuild()
        self._popups_watchdog = PopupsWatchdog(event_bus=self.event_bus, browser_session=self)
        self._popups_watchdog.attach_to_session()
        PermissionsWatchdog.model_rebuild()
        self._permissions_watchdog = PermissionsWatchdog(event_bus=self.event_bus, browser_session=self)
        self._permissions_watchdog.attach_to_session()
        DefaultActionWatchdog.model_rebuild()
        self._default_action_watchdog = DefaultActionWatchdog(event_bus=self.event_bus, browser_session=self)
        self._default_action_watchdog.attach_to_session()
        ScreenshotWatchdog.model_rebuild()
        self._screenshot_watchdog = ScreenshotWatchdog(event_bus=self.event_bus, browser_session=self)
        self._screenshot_watchdog.attach_to_session()
        DOMWatchdog.model_rebuild()
        self._dom_watchdog = DOMWatchdog(event_bus=self.event_bus, browser_session=self)
        self._dom_watchdog.attach_to_session()
        RecordingWatchdog.model_rebuild()
        self._recording_watchdog = RecordingWatchdog(event_bus=self.event_bus, browser_session=self)
        self._recording_watchdog.attach_to_session()
        if self.browser_profile.record_har_path:
            HarRecordingWatchdog.model_rebuild()
            self._har_recording_watchdog = HarRecordingWatchdog(event_bus=self.event_bus, browser_session=self)
            self._har_recording_watchdog.attach_to_session()
        if self.browser_profile.captcha_solver:
            CaptchaWatchdog.model_rebuild()
            self._captcha_watchdog = CaptchaWatchdog(event_bus=self.event_bus, browser_session=self)
            self._captcha_watchdog.attach_to_session()
        self._watchdogs_attached = True

    async def connect(self, cdp_url: str | None=None) -> Self:
        self.browser_profile.cdp_url = cdp_url or self.cdp_url
        if not self.cdp_url:
            raise RuntimeError('Cannot setup CDP connection without CDP URL')
        if self._cdp_client_root is not None:
            self.logger.warning('⚠️ connect() called but CDP client already exists! Cleaning up old connection before creating new one.')
            try:
                await self._cdp_client_root.stop()
            except Exception as e:
                self.logger.debug(f'Error stopping old CDP client: {e}')
            self._cdp_client_root = None
        if not self.cdp_url.startswith('ws'):
            parsed_url = urlparse(self.cdp_url)
            path = parsed_url.path.rstrip('/')
            if not path.endswith('/json/version'):
                path = path + '/json/version'
            url = urlunparse((parsed_url.scheme, parsed_url.netloc, path, parsed_url.params, parsed_url.query, parsed_url.fragment))
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                headers = self.browser_profile.headers or {}
                version_info = await client.get(url, headers=headers)
                self.logger.debug(f'Raw version info: {str(version_info)}')
                self.browser_profile.cdp_url = version_info.json()['webSocketDebuggerUrl']
        assert self.cdp_url is not None, 'CDP URL is None.'
        browser_location = 'local browser' if self.is_local else 'remote browser'
        self.logger.debug(f'🌎 Connecting to existing chromium-based browser via CDP: {self.cdp_url} -> ({browser_location})')
        try:
            headers = getattr(self.browser_profile, 'headers', None)
            self._cdp_client_root = CDPClient(self.cdp_url, additional_headers=headers, max_ws_frame_size=200 * 1024 * 1024)
            assert self._cdp_client_root is not None
            await self._cdp_client_root.start()
            from system.browser.session_manager import SessionManager
            self.session_manager = SessionManager(self)
            await self.session_manager.start_monitoring()
            self.logger.debug('Event-driven session manager started')
            await self._cdp_client_root.send.Target.setAutoAttach(params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True})
            self.logger.debug('CDP client connected with auto-attach enabled')
            page_targets_from_manager = self.session_manager.get_all_page_targets()
            from system.utils import is_new_tab_page

            async def _redirect_newtab(target):
                target_url = target.url
                target_id = target.target_id
                self.logger.debug(f'🔄 Redirecting {target_url} to about:blank for target {target_id}')
                try:
                    session = await self.get_or_create_cdp_session(target_id, focus=False)
                    await session.cdp_client.send.Page.navigate(params={'url': 'about:blank'}, session_id=session.session_id)
                    target.url = 'about:blank'
                except Exception as e:
                    self.logger.warning(f'Failed to redirect {target_url}: {e}')
            redirect_tasks = [_redirect_newtab(target) for target in page_targets_from_manager if is_new_tab_page(target.url) and target.url != 'about:blank']
            if redirect_tasks:
                await asyncio.gather(*redirect_tasks, return_exceptions=True)
            if not page_targets_from_manager:
                new_target = await self._cdp_client_root.send.Target.createTarget(params={'url': 'about:blank'})
                target_id = new_target['targetId']
                self.logger.debug(f'📄 Created new blank page: {target_id}')
            else:
                target_id = page_targets_from_manager[0].target_id
                self.logger.debug(f'📄 Using existing page: {target_id}')
            try:
                await self.get_or_create_cdp_session(target_id, focus=True)
                self.logger.debug(f'📄 Agent focus set to {target_id[:8]}...')
            except ValueError as e:
                raise RuntimeError(f'Failed to get session for initial target {target_id}: {e}') from e
            await self._setup_proxy_auth()
            self._intentional_stop = False
            self._attach_ws_drop_callback()
            if self.agent_focus_target_id:
                target = self.session_manager.get_target(self.agent_focus_target_id)
                if target.title == 'Unknown title':
                    self.logger.warning('Target created but title is unknown (may be normal for about:blank)')
            for idx, target in enumerate(page_targets_from_manager):
                target_url = target.url
                self.logger.debug(f'Dispatching TabCreatedEvent for initial tab {idx}: {target_url}')
                self.event_bus.dispatch(TabCreatedEvent(url=target_url, target_id=target.target_id))
            if page_targets_from_manager:
                initial_url = page_targets_from_manager[0].url
                self.event_bus.dispatch(AgentFocusChangedEvent(target_id=page_targets_from_manager[0].target_id, url=initial_url))
                self.logger.debug(f'Initial agent focus set to tab 0: {initial_url}')
        except Exception as e:
            self.logger.error(f'❌ FATAL: Failed to setup CDP connection: {e}')
            self.logger.error('❌ Browser cannot continue without CDP connection')
            if self.session_manager:
                try:
                    await self.session_manager.clear()
                    self.logger.debug('Cleared SessionManager state after initialization failure')
                except Exception as cleanup_error:
                    self.logger.debug(f'Error clearing SessionManager: {cleanup_error}')
            if self._cdp_client_root:
                try:
                    await self._cdp_client_root.stop()
                    self.logger.debug('Closed CDP client WebSocket after initialization failure')
                except Exception as cleanup_error:
                    self.logger.debug(f'Error closing CDP client: {cleanup_error}')
            self.session_manager = None
            self._cdp_client_root = None
            self.agent_focus_target_id = None
            raise RuntimeError(f'Failed to establish CDP connection to browser: {e}') from e
        return self

    async def _setup_proxy_auth(self) -> None:
        assert self._cdp_client_root
        try:
            proxy_cfg = self.browser_profile.proxy
            username = proxy_cfg.username if proxy_cfg else None
            password = proxy_cfg.password if proxy_cfg else None
            if not username or not password:
                self.logger.debug('Proxy credentials not provided; skipping proxy auth setup')
                return
            try:
                await self._cdp_client_root.send.Fetch.enable(params={'handleAuthRequests': True})
                self.logger.debug('Fetch.enable(handleAuthRequests=True) enabled on root client')
            except Exception as e:
                self.logger.debug(f'Fetch.enable on root failed: {type(e).__name__}: {e}')
            try:
                if self.agent_focus_target_id:
                    cdp_session = await self.get_or_create_cdp_session(self.agent_focus_target_id, focus=False)
                    await cdp_session.cdp_client.send.Fetch.enable(params={'handleAuthRequests': True}, session_id=cdp_session.session_id)
                    self.logger.debug('Fetch.enable(handleAuthRequests=True) enabled on focused session')
            except Exception as e:
                self.logger.debug(f'Fetch.enable on focused session failed: {type(e).__name__}: {e}')

            def _on_auth_required(event: AuthRequiredEvent, session_id: SessionID | None=None):
                request_id = event.get('requestId') or event.get('request_id')
                if not request_id:
                    return
                challenge = event.get('authChallenge') or event.get('auth_challenge') or {}
                source = (challenge.get('source') or '').lower()
                if source == 'proxy' and request_id:

                    async def _respond():
                        assert self._cdp_client_root
                        try:
                            await self._cdp_client_root.send.Fetch.continueWithAuth(params={'requestId': request_id, 'authChallengeResponse': {'response': 'ProvideCredentials', 'username': username, 'password': password}}, session_id=session_id)
                        except Exception as e:
                            self.logger.debug(f'Proxy auth respond failed: {type(e).__name__}: {e}')
                    create_task_with_error_handling(_respond(), name='auth_respond', logger_instance=self.logger, suppress_exceptions=True)
                else:

                    async def _default():
                        assert self._cdp_client_root
                        try:
                            await self._cdp_client_root.send.Fetch.continueWithAuth(params={'requestId': request_id, 'authChallengeResponse': {'response': 'Default'}}, session_id=session_id)
                        except Exception as e:
                            self.logger.debug(f'Default auth respond failed: {type(e).__name__}: {e}')
                    if request_id:
                        create_task_with_error_handling(_default(), name='auth_default', logger_instance=self.logger, suppress_exceptions=True)

            def _on_request_paused(event: RequestPausedEvent, session_id: SessionID | None=None):
                request_id = event.get('requestId') or event.get('request_id')
                if not request_id:
                    return

                async def _continue():
                    assert self._cdp_client_root
                    try:
                        await self._cdp_client_root.send.Fetch.continueRequest(params={'requestId': request_id}, session_id=session_id)
                    except Exception:
                        pass
                create_task_with_error_handling(_continue(), name='request_continue', logger_instance=self.logger, suppress_exceptions=True)
            try:
                self._cdp_client_root.register.Fetch.authRequired(_on_auth_required)
                self._cdp_client_root.register.Fetch.requestPaused(_on_request_paused)
                if self.agent_focus_target_id:
                    cdp_session = await self.get_or_create_cdp_session(self.agent_focus_target_id, focus=False)
                    cdp_session.cdp_client.register.Fetch.authRequired(_on_auth_required)
                    cdp_session.cdp_client.register.Fetch.requestPaused(_on_request_paused)
                self.logger.debug('Registered Fetch.authRequired handlers')
            except Exception as e:
                self.logger.debug(f'Failed to register authRequired handlers: {type(e).__name__}: {e}')
            try:
                if self.agent_focus_target_id:
                    cdp_session = await self.get_or_create_cdp_session(self.agent_focus_target_id, focus=False)
                    await cdp_session.cdp_client.send.Fetch.enable(params={'handleAuthRequests': True, 'patterns': [{'urlPattern': '*'}]}, session_id=cdp_session.session_id)
            except Exception as e:
                self.logger.debug(f'Fetch.enable on focused session failed: {type(e).__name__}: {e}')
        except Exception as e:
            self.logger.debug(f'Skipping proxy auth setup: {type(e).__name__}: {e}')

    async def reconnect(self) -> None:
        assert self.cdp_url, 'Cannot reconnect without a CDP URL'
        old_focus_target_id = self.agent_focus_target_id
        if self._cdp_client_root:
            try:
                await self._cdp_client_root.stop()
            except Exception as e:
                self.logger.debug(f'Error stopping old CDP client during reconnect: {e}')
            self._cdp_client_root = None
        if self.session_manager:
            try:
                await self.session_manager.clear()
            except Exception as e:
                self.logger.debug(f'Error clearing SessionManager during reconnect: {e}')
            self.session_manager = None
        self.agent_focus_target_id = None
        headers = getattr(self.browser_profile, 'headers', None)
        self._cdp_client_root = CDPClient(self.cdp_url, additional_headers=headers, max_ws_frame_size=200 * 1024 * 1024)
        await self._cdp_client_root.start()
        from system.browser.session_manager import SessionManager
        self.session_manager = SessionManager(self)
        await self.session_manager.start_monitoring()
        await self._cdp_client_root.send.Target.setAutoAttach(params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True})
        page_targets = self.session_manager.get_all_page_targets()
        restored = False
        if old_focus_target_id:
            for target in page_targets:
                if target.target_id == old_focus_target_id:
                    await self.get_or_create_cdp_session(old_focus_target_id, focus=True)
                    restored = True
                    self.logger.debug(f'🔄 Restored agent focus to previous target {old_focus_target_id[:8]}...')
                    break
        if not restored:
            if page_targets:
                fallback_id = page_targets[0].target_id
                await self.get_or_create_cdp_session(fallback_id, focus=True)
                self.logger.debug(f'🔄 Agent focus set to fallback target {fallback_id[:8]}...')
            else:
                new_target = await self._cdp_client_root.send.Target.createTarget(params={'url': 'about:blank'})
                target_id = new_target['targetId']
                await self.get_or_create_cdp_session(target_id, focus=True)
                self.logger.debug(f'🔄 Created new blank page during reconnect: {target_id[:8]}...')
        await self._setup_proxy_auth()
        self._attach_ws_drop_callback()

    async def _auto_reconnect(self, max_attempts: int=3) -> None:
        async with self._reconnect_lock:
            if self._reconnecting:
                return
            self._reconnecting = True
            self._reconnect_event.clear()
        start_time = time.time()
        delays = [1.0, 2.0, 4.0]
        try:
            for attempt in range(1, max_attempts + 1):
                self.event_bus.dispatch(BrowserReconnectingEvent(cdp_url=self.cdp_url or '', attempt=attempt, max_attempts=max_attempts))
                self.logger.warning(f'🔄 WebSocket reconnection attempt {attempt}/{max_attempts}...')
                try:
                    await asyncio.wait_for(self.reconnect(), timeout=15.0)
                    downtime = time.time() - start_time
                    self.event_bus.dispatch(BrowserReconnectedEvent(cdp_url=self.cdp_url or '', attempt=attempt, downtime_seconds=downtime))
                    self.logger.info(f'🔄 WebSocket reconnected after {downtime:.1f}s (attempt {attempt})')
                    return
                except Exception as e:
                    self.logger.warning(f'🔄 Reconnection attempt {attempt} failed: {type(e).__name__}: {e}')
                    if attempt < max_attempts:
                        delay = delays[attempt - 1] if attempt - 1 < len(delays) else delays[-1]
                        await asyncio.sleep(delay)
            self.logger.error(f'🔄 All {max_attempts} reconnection attempts failed')
            self.event_bus.dispatch(BrowserErrorEvent(error_type='ReconnectionFailed', message=f'Failed to reconnect after {max_attempts} attempts ({time.time() - start_time:.1f}s)', details={'cdp_url': self.cdp_url or '', 'max_attempts': max_attempts}))
        finally:
            self._reconnecting = False
            self._reconnect_event.set()

    def _attach_ws_drop_callback(self) -> None:
        if not self._cdp_client_root or not hasattr(self._cdp_client_root, '_message_handler_task'):
            return
        task = self._cdp_client_root._message_handler_task
        if task is None or task.done():
            return

        def _on_message_handler_done(fut: asyncio.Future) -> None:
            if self._intentional_stop or self._reconnecting or (not self.cdp_url):
                return
            exc = fut.exception() if not fut.cancelled() else None
            self.logger.warning(f"🔌 CDP WebSocket message handler exited unexpectedly{(f': {type(exc).__name__}: {exc}' if exc else ' (connection closed)')}")
            try:
                loop = asyncio.get_running_loop()
                self._reconnect_task = loop.create_task(self._auto_reconnect())
            except RuntimeError:
                self.logger.error('🔌 No event loop available for auto-reconnect')
        task.add_done_callback(_on_message_handler_done)

    async def get_tabs(self) -> list[TabInfo]:
        tabs = []
        if not self.session_manager:
            return tabs
        page_targets = self.session_manager.get_all_page_targets()
        for i, target in enumerate(page_targets):
            target_id = target.target_id
            url = target.url
            title = target.title
            try:
                if is_new_tab_page(url) or url.startswith('chrome://'):
                    if is_new_tab_page(url):
                        title = ''
                    elif not title:
                        title = url
                if (not title or title == '') and (url.endswith('.pdf') or 'pdf' in url):
                    try:
                        from urllib.parse import urlparse
                        filename = urlparse(url).path.split('/')[-1]
                        if filename:
                            title = filename
                    except Exception:
                        pass
            except Exception as e:
                self.logger.debug(f'⚠️ Failed to get target info for tab #{i}: {_log_pretty_url(url)} - {type(e).__name__}')
                if is_new_tab_page(url):
                    title = ''
                elif url.startswith('chrome://'):
                    title = url
                else:
                    title = ''
            tab_info = TabInfo(target_id=target_id, url=url, title=title, parent_target_id=None)
            tabs.append(tab_info)
        return tabs

    async def get_current_target_info(self) -> TargetInfo | None:
        if not self.agent_focus_target_id:
            return None
        target = self.session_manager.get_target(self.agent_focus_target_id)
        return {'targetId': target.target_id, 'url': target.url, 'title': target.title, 'type': target.target_type, 'attached': True, 'canAccessOpener': False}

    async def get_current_page_url(self) -> str:
        if self.agent_focus_target_id:
            target = self.session_manager.get_target(self.agent_focus_target_id)
            return target.url
        return 'about:blank'

    async def get_current_page_title(self) -> str:
        if self.agent_focus_target_id:
            target = self.session_manager.get_target(self.agent_focus_target_id)
            return target.title
        return 'Unknown page title'

    async def navigate_to(self, url: str, new_tab: bool=False) -> None:
        from system.browser.events import NavigateToUrlEvent
        event = self.event_bus.dispatch(NavigateToUrlEvent(url=url, new_tab=new_tab))
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)

    async def get_dom_element_by_index(self, index: int) -> EnhancedDOMTreeNode | None:
        if self._cached_selector_map and index in self._cached_selector_map:
            return self._cached_selector_map[index]
        return None

    def update_cached_selector_map(self, selector_map: dict[int, EnhancedDOMTreeNode]) -> None:
        self._cached_selector_map = selector_map

    async def get_element_by_index(self, index: int) -> EnhancedDOMTreeNode | None:
        return await self.get_dom_element_by_index(index)

    async def get_dom_element_at_coordinates(self, x: int, y: int) -> EnhancedDOMTreeNode | None:
        from system.dom.views import NodeType
        page = await self.get_current_page()
        if page is None:
            raise RuntimeError('No active page found')
        session_id = await page._ensure_session()
        try:
            result = await self.cdp_client.send.DOM.getNodeForLocation(params={'x': x, 'y': y, 'includeUserAgentShadowDOM': False, 'ignorePointerEventsNone': False}, session_id=session_id)
            backend_node_id = result.get('backendNodeId')
            if backend_node_id is None:
                self.logger.debug(f'No element found at coordinates ({x}, {y})')
                return None
            if self._cached_selector_map:
                for node in self._cached_selector_map.values():
                    if node.backend_node_id == backend_node_id:
                        self.logger.debug(f'Found element at ({x}, {y}) in cached selector_map')
                        return node
            try:
                describe_result = await self.cdp_client.send.DOM.describeNode(params={'backendNodeId': backend_node_id}, session_id=session_id)
                node_info = describe_result.get('node', {})
                node_name = node_info.get('nodeName', '')
                attrs_list = node_info.get('attributes', [])
                attributes = {attrs_list[i]: attrs_list[i + 1] for i in range(0, len(attrs_list), 2)}
                return EnhancedDOMTreeNode(node_id=result.get('nodeId', 0), backend_node_id=backend_node_id, node_type=NodeType(node_info.get('nodeType', NodeType.ELEMENT_NODE.value)), node_name=node_name, node_value=node_info.get('nodeValue', '') or '', attributes=attributes, is_scrollable=None, frame_id=result.get('frameId'), session_id=session_id, target_id=self.agent_focus_target_id or '', content_document=None, shadow_root_type=None, shadow_roots=None, parent_node=None, children_nodes=None, ax_node=None, snapshot_node=None, is_visible=None, absolute_position=None)
            except Exception as e:
                self.logger.debug(f'DOM.describeNode failed for backend_node_id={backend_node_id}: {e}')
                return EnhancedDOMTreeNode(node_id=result.get('nodeId', 0), backend_node_id=backend_node_id, node_type=NodeType.ELEMENT_NODE, node_name='', node_value='', attributes={}, is_scrollable=None, frame_id=result.get('frameId'), session_id=session_id, target_id=self.agent_focus_target_id or '', content_document=None, shadow_root_type=None, shadow_roots=None, parent_node=None, children_nodes=None, ax_node=None, snapshot_node=None, is_visible=None, absolute_position=None)
        except Exception as e:
            self.logger.warning(f'Failed to get DOM element at coordinates ({x}, {y}): {e}')
            return None

    async def get_target_id_from_tab_id(self, tab_id: str) -> TargetID:
        if not self.session_manager:
            raise RuntimeError('SessionManager not initialized')
        for full_target_id in self.session_manager.get_all_target_ids():
            if full_target_id.endswith(tab_id):
                if await self.session_manager.is_target_valid(full_target_id):
                    return full_target_id
                self.logger.debug(f'Found stale target {full_target_id}, skipping')
        raise ValueError(f'No TargetID found ending in tab_id=...{tab_id}')

    async def get_target_id_from_url(self, url: str) -> TargetID:
        if not self.session_manager:
            raise RuntimeError('SessionManager not initialized')
        for target_id, target in self.session_manager.get_all_targets().items():
            if target.target_type in ('page', 'tab') and target.url == url:
                return target_id
        for target_id, target in self.session_manager.get_all_targets().items():
            if target.target_type in ('page', 'tab') and url in target.url:
                return target_id
        raise ValueError(f'No TargetID found for url={url}')

    async def get_most_recently_opened_target_id(self) -> TargetID:
        page_targets = self.session_manager.get_all_page_targets()
        if not page_targets:
            raise RuntimeError('No page targets available')
        return page_targets[-1].target_id

    def is_file_input(self, element: Any) -> bool:
        if self._dom_watchdog:
            return self._dom_watchdog.is_file_input(element)
        return hasattr(element, 'node_name') and element.node_name.upper() == 'INPUT' and hasattr(element, 'attributes') and (element.attributes.get('type', '').lower() == 'file')

    async def get_selector_map(self) -> dict[int, EnhancedDOMTreeNode]:
        if self._cached_selector_map:
            return self._cached_selector_map
        if self._dom_watchdog and hasattr(self._dom_watchdog, 'selector_map'):
            return self._dom_watchdog.selector_map or {}
        return {}

    async def get_index_by_id(self, element_id: str) -> int | None:
        selector_map = await self.get_selector_map()
        for idx, element in selector_map.items():
            if element.attributes and element.attributes.get('id') == element_id:
                return idx
        return None

    async def get_index_by_class(self, class_name: str) -> int | None:
        selector_map = await self.get_selector_map()
        for idx, element in selector_map.items():
            if element.attributes:
                element_class = element.attributes.get('class', '')
                if class_name in element_class.split():
                    return idx
        return None

    async def remove_highlights(self) -> None:
        if not self.browser_profile.highlight_elements:
            return
        try:
            cdp_session = await self.get_or_create_cdp_session()
            script = '\n\t\t\t(function() {\n\t\t\t\t// Remove all browser-use highlight elements\n\t\t\t\tconst highlights = document.querySelectorAll(\'[data-browser-use-highlight]\');\n\t\t\t\tconsole.log(\'Removing\', highlights.length, \'browser-use highlight elements\');\n\t\t\t\thighlights.forEach(el => el.remove());\n\n\t\t\t\t// Also remove by ID in case selector missed anything\n\t\t\t\tconst highlightContainer = document.getElementById(\'browser-use-debug-highlights\');\n\t\t\t\tif (highlightContainer) {\n\t\t\t\t\tconsole.log(\'Removing highlight container by ID\');\n\t\t\t\t\thighlightContainer.remove();\n\t\t\t\t}\n\n\t\t\t\t// Final cleanup - remove any orphaned tooltips\n\t\t\t\tconst orphanedTooltips = document.querySelectorAll(\'[data-browser-use-highlight="tooltip"]\');\n\t\t\t\torphanedTooltips.forEach(el => el.remove());\n\n\t\t\t\treturn { removed: highlights.length };\n\t\t\t})();\n\t\t\t'
            result = await cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': script, 'returnByValue': True}, session_id=cdp_session.session_id)
            if result and 'result' in result and ('value' in result['result']):
                removed_count = result['result']['value'].get('removed', 0)
                self.logger.debug(f'Successfully removed {removed_count} highlight elements')
            else:
                self.logger.debug('Highlight removal completed')
        except Exception as e:
            self.logger.warning(f'Failed to remove highlights: {e}')

    @observe_debug(ignore_input=True, ignore_output=True, name='get_element_coordinates')
    async def get_element_coordinates(self, backend_node_id: int, cdp_session: CDPSession) -> DOMRect | None:
        session_id = cdp_session.session_id
        quads = []
        try:
            content_quads_result = await cdp_session.cdp_client.send.DOM.getContentQuads(params={'backendNodeId': backend_node_id}, session_id=session_id)
            if 'quads' in content_quads_result and content_quads_result['quads']:
                quads = content_quads_result['quads']
                self.logger.debug(f'Got {len(quads)} quads from DOM.getContentQuads')
            else:
                self.logger.debug(f'No quads found from DOM.getContentQuads {content_quads_result}')
        except Exception as e:
            self.logger.debug(f'DOM.getContentQuads failed: {e}')
        if not quads:
            try:
                box_model = await cdp_session.cdp_client.send.DOM.getBoxModel(params={'backendNodeId': backend_node_id}, session_id=session_id)
                if 'model' in box_model and 'content' in box_model['model']:
                    content_quad = box_model['model']['content']
                    if len(content_quad) >= 8:
                        quads = [[content_quad[0], content_quad[1], content_quad[2], content_quad[3], content_quad[4], content_quad[5], content_quad[6], content_quad[7]]]
                        self.logger.debug('Got quad from DOM.getBoxModel')
            except Exception as e:
                self.logger.debug(f'DOM.getBoxModel failed: {e}')
        if not quads:
            try:
                result = await cdp_session.cdp_client.send.DOM.resolveNode(params={'backendNodeId': backend_node_id}, session_id=session_id)
                if 'object' in result and 'objectId' in result['object']:
                    object_id = result['object']['objectId']
                    js_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(params={'objectId': object_id, 'functionDeclaration': '\n\t\t\t\t\t\t\tfunction() {\n\t\t\t\t\t\t\t\tconst rect = this.getBoundingClientRect();\n\t\t\t\t\t\t\t\treturn {\n\t\t\t\t\t\t\t\t\tx: rect.x,\n\t\t\t\t\t\t\t\t\ty: rect.y,\n\t\t\t\t\t\t\t\t\twidth: rect.width,\n\t\t\t\t\t\t\t\t\theight: rect.height\n\t\t\t\t\t\t\t\t};\n\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t', 'returnByValue': True}, session_id=session_id)
                    if 'result' in js_result and 'value' in js_result['result']:
                        rect_data = js_result['result']['value']
                        if rect_data['width'] > 0 and rect_data['height'] > 0:
                            return DOMRect(x=rect_data['x'], y=rect_data['y'], width=rect_data['width'], height=rect_data['height'])
            except Exception as e:
                self.logger.debug(f'JavaScript getBoundingClientRect failed: {e}')
        if quads:
            quad = quads[0]
            if len(quad) >= 8:
                x_coords = [quad[i] for i in range(0, 8, 2)]
                y_coords = [quad[i] for i in range(1, 8, 2)]
                min_x = min(x_coords)
                min_y = min(y_coords)
                max_x = max(x_coords)
                max_y = max(y_coords)
                width = max_x - min_x
                height = max_y - min_y
                if width > 0 and height > 0:
                    return DOMRect(x=min_x, y=min_y, width=width, height=height)
        return None

    async def highlight_interaction_element(self, node: 'EnhancedDOMTreeNode') -> None:
        if not self.browser_profile.highlight_elements:
            return
        try:
            import json
            cdp_session = await self.get_or_create_cdp_session()
            rect = await self.get_element_coordinates(node.backend_node_id, cdp_session)
            color = self.browser_profile.interaction_highlight_color
            duration_ms = int(self.browser_profile.interaction_highlight_duration * 1000)
            if not rect:
                self.logger.debug(f'No coordinates found for backend node {node.backend_node_id}')
                return
            script = f"\n\t\t\t(function() {{\n\t\t\t\tconst rect = {json.dumps({'x': rect.x, 'y': rect.y, 'width': rect.width, 'height': rect.height})};\n\t\t\t\tconst color = {json.dumps(color)};\n\t\t\t\tconst duration = {duration_ms};\n\n\t\t\t\t// Scale corner size based on element dimensions to ensure gaps between corners\n\t\t\t\tconst maxCornerSize = 20;\n\t\t\t\tconst minCornerSize = 8;\n\t\t\t\tconst cornerSize = Math.max(\n\t\t\t\t\tminCornerSize,\n\t\t\t\t\tMath.min(maxCornerSize, Math.min(rect.width, rect.height) * 0.35)\n\t\t\t\t);\n\t\t\t\tconst borderWidth = 3;\n\t\t\t\tconst startOffset = 10; // Starting offset in pixels\n\t\t\t\tconst finalOffset = -3; // Final position slightly outside the element\n\n\t\t\t\t// Get current scroll position\n\t\t\t\tconst scrollX = window.pageXOffset || document.documentElement.scrollLeft || 0;\n\t\t\t\tconst scrollY = window.pageYOffset || document.documentElement.scrollTop || 0;\n\n\t\t\t\t// Create container for all corners\n\t\t\t\tconst container = document.createElement('div');\n\t\t\t\tcontainer.setAttribute('data-browser-use-interaction-highlight', 'true');\n\t\t\t\tcontainer.style.cssText = `\n\t\t\t\t\tposition: absolute;\n\t\t\t\t\tleft: ${{rect.x + scrollX}}px;\n\t\t\t\t\ttop: ${{rect.y + scrollY}}px;\n\t\t\t\t\twidth: ${{rect.width}}px;\n\t\t\t\t\theight: ${{rect.height}}px;\n\t\t\t\t\tpointer-events: none;\n\t\t\t\t\tz-index: 2147483647;\n\t\t\t\t`;\n\n\t\t\t\t// Create 4 corner brackets\n\t\t\t\tconst corners = [\n\t\t\t\t\t{{ pos: 'top-left', startX: -startOffset, startY: -startOffset, finalX: finalOffset, finalY: finalOffset }},\n\t\t\t\t\t{{ pos: 'top-right', startX: startOffset, startY: -startOffset, finalX: -finalOffset, finalY: finalOffset }},\n\t\t\t\t\t{{ pos: 'bottom-left', startX: -startOffset, startY: startOffset, finalX: finalOffset, finalY: -finalOffset }},\n\t\t\t\t\t{{ pos: 'bottom-right', startX: startOffset, startY: startOffset, finalX: -finalOffset, finalY: -finalOffset }}\n\t\t\t\t];\n\n\t\t\t\tcorners.forEach(corner => {{\n\t\t\t\t\tconst bracket = document.createElement('div');\n\t\t\t\t\tbracket.style.cssText = `\n\t\t\t\t\t\tposition: absolute;\n\t\t\t\t\t\twidth: ${{cornerSize}}px;\n\t\t\t\t\t\theight: ${{cornerSize}}px;\n\t\t\t\t\t\tpointer-events: none;\n\t\t\t\t\t\ttransition: all 0.15s ease-out;\n\t\t\t\t\t`;\n\n\t\t\t\t\t// Position corners\n\t\t\t\t\tif (corner.pos === 'top-left') {{\n\t\t\t\t\t\tbracket.style.top = '0';\n\t\t\t\t\t\tbracket.style.left = '0';\n\t\t\t\t\t\tbracket.style.borderTop = `${{borderWidth}}px solid ${{color}}`;\n\t\t\t\t\t\tbracket.style.borderLeft = `${{borderWidth}}px solid ${{color}}`;\n\t\t\t\t\t\tbracket.style.transform = `translate(${{corner.startX}}px, ${{corner.startY}}px)`;\n\t\t\t\t\t}} else if (corner.pos === 'top-right') {{\n\t\t\t\t\t\tbracket.style.top = '0';\n\t\t\t\t\t\tbracket.style.right = '0';\n\t\t\t\t\t\tbracket.style.borderTop = `${{borderWidth}}px solid ${{color}}`;\n\t\t\t\t\t\tbracket.style.borderRight = `${{borderWidth}}px solid ${{color}}`;\n\t\t\t\t\t\tbracket.style.transform = `translate(${{corner.startX}}px, ${{corner.startY}}px)`;\n\t\t\t\t\t}} else if (corner.pos === 'bottom-left') {{\n\t\t\t\t\t\tbracket.style.bottom = '0';\n\t\t\t\t\t\tbracket.style.left = '0';\n\t\t\t\t\t\tbracket.style.borderBottom = `${{borderWidth}}px solid ${{color}}`;\n\t\t\t\t\t\tbracket.style.borderLeft = `${{borderWidth}}px solid ${{color}}`;\n\t\t\t\t\t\tbracket.style.transform = `translate(${{corner.startX}}px, ${{corner.startY}}px)`;\n\t\t\t\t\t}} else if (corner.pos === 'bottom-right') {{\n\t\t\t\t\t\tbracket.style.bottom = '0';\n\t\t\t\t\t\tbracket.style.right = '0';\n\t\t\t\t\t\tbracket.style.borderBottom = `${{borderWidth}}px solid ${{color}}`;\n\t\t\t\t\t\tbracket.style.borderRight = `${{borderWidth}}px solid ${{color}}`;\n\t\t\t\t\t\tbracket.style.transform = `translate(${{corner.startX}}px, ${{corner.startY}}px)`;\n\t\t\t\t\t}}\n\n\t\t\t\t\tcontainer.appendChild(bracket);\n\n\t\t\t\t\t// Animate to final position slightly outside the element\n\t\t\t\t\tsetTimeout(() => {{\n\t\t\t\t\t\tbracket.style.transform = `translate(${{corner.finalX}}px, ${{corner.finalY}}px)`;\n\t\t\t\t\t}}, 10);\n\t\t\t\t}});\n\n\t\t\t\tdocument.body.appendChild(container);\n\n\t\t\t\t// Auto-remove after duration\n\t\t\t\tsetTimeout(() => {{\n\t\t\t\t\tcontainer.style.opacity = '0';\n\t\t\t\t\tcontainer.style.transition = 'opacity 0.3s ease-out';\n\t\t\t\t\tsetTimeout(() => container.remove(), 300);\n\t\t\t\t}}, duration);\n\n\t\t\t\treturn {{ created: true }};\n\t\t\t}})();\n\t\t\t"
            await cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': script, 'returnByValue': True}, session_id=cdp_session.session_id)
        except Exception as e:
            self.logger.debug(f'Failed to highlight interaction element: {e}')

    async def highlight_coordinate_click(self, x: int, y: int) -> None:
        if not self.browser_profile.highlight_elements:
            return
        try:
            import json
            cdp_session = await self.get_or_create_cdp_session()
            color = self.browser_profile.interaction_highlight_color
            duration_ms = int(self.browser_profile.interaction_highlight_duration * 1000)
            script = f"\n\t\t\t(function() {{\n\t\t\t\tconst x = {x};\n\t\t\t\tconst y = {y};\n\t\t\t\tconst color = {json.dumps(color)};\n\t\t\t\tconst duration = {duration_ms};\n\n\t\t\t\t// Get current scroll position\n\t\t\t\tconst scrollX = window.pageXOffset || document.documentElement.scrollLeft || 0;\n\t\t\t\tconst scrollY = window.pageYOffset || document.documentElement.scrollTop || 0;\n\n\t\t\t\t// Create container\n\t\t\t\tconst container = document.createElement('div');\n\t\t\t\tcontainer.setAttribute('data-browser-use-coordinate-highlight', 'true');\n\t\t\t\tcontainer.style.cssText = `\n\t\t\t\t\tposition: absolute;\n\t\t\t\t\tleft: ${{x + scrollX}}px;\n\t\t\t\t\ttop: ${{y + scrollY}}px;\n\t\t\t\t\twidth: 0;\n\t\t\t\t\theight: 0;\n\t\t\t\t\tpointer-events: none;\n\t\t\t\t\tz-index: 2147483647;\n\t\t\t\t`;\n\n\t\t\t\t// Create outer circle\n\t\t\t\tconst outerCircle = document.createElement('div');\n\t\t\t\touterCircle.style.cssText = `\n\t\t\t\t\tposition: absolute;\n\t\t\t\t\tleft: -15px;\n\t\t\t\t\ttop: -15px;\n\t\t\t\t\twidth: 30px;\n\t\t\t\t\theight: 30px;\n\t\t\t\t\tborder: 3px solid ${{color}};\n\t\t\t\t\tborder-radius: 50%;\n\t\t\t\t\topacity: 0;\n\t\t\t\t\ttransform: scale(0.3);\n\t\t\t\t\ttransition: all 0.2s ease-out;\n\t\t\t\t`;\n\t\t\t\tcontainer.appendChild(outerCircle);\n\n\t\t\t\t// Create center dot\n\t\t\t\tconst centerDot = document.createElement('div');\n\t\t\t\tcenterDot.style.cssText = `\n\t\t\t\t\tposition: absolute;\n\t\t\t\t\tleft: -4px;\n\t\t\t\t\ttop: -4px;\n\t\t\t\t\twidth: 8px;\n\t\t\t\t\theight: 8px;\n\t\t\t\t\tbackground: ${{color}};\n\t\t\t\t\tborder-radius: 50%;\n\t\t\t\t\topacity: 0;\n\t\t\t\t\ttransform: scale(0);\n\t\t\t\t\ttransition: all 0.15s ease-out;\n\t\t\t\t`;\n\t\t\t\tcontainer.appendChild(centerDot);\n\n\t\t\t\tdocument.body.appendChild(container);\n\n\t\t\t\t// Animate in\n\t\t\t\tsetTimeout(() => {{\n\t\t\t\t\touterCircle.style.opacity = '0.8';\n\t\t\t\t\touterCircle.style.transform = 'scale(1)';\n\t\t\t\t\tcenterDot.style.opacity = '1';\n\t\t\t\t\tcenterDot.style.transform = 'scale(1)';\n\t\t\t\t}}, 10);\n\n\t\t\t\t// Animate out and remove\n\t\t\t\tsetTimeout(() => {{\n\t\t\t\t\touterCircle.style.opacity = '0';\n\t\t\t\t\touterCircle.style.transform = 'scale(1.5)';\n\t\t\t\t\tcenterDot.style.opacity = '0';\n\t\t\t\t\tsetTimeout(() => container.remove(), 300);\n\t\t\t\t}}, duration);\n\n\t\t\t\treturn {{ created: true }};\n\t\t\t}})();\n\t\t\t"
            await cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': script, 'returnByValue': True}, session_id=cdp_session.session_id)
        except Exception as e:
            self.logger.debug(f'Failed to highlight coordinate click: {e}')

    async def add_highlights(self, selector_map: dict[int, 'EnhancedDOMTreeNode']) -> None:
        if not self.browser_profile.dom_highlight_elements or not selector_map:
            return
        try:
            import json
            elements_data = []
            for _, node in selector_map.items():
                if node.absolute_position:
                    rect = node.absolute_position
                    bbox = {'x': rect.x, 'y': rect.y, 'width': rect.width, 'height': rect.height}
                    if bbox and bbox.get('width', 0) > 0 and (bbox.get('height', 0) > 0):
                        element = {'x': bbox['x'], 'y': bbox['y'], 'width': bbox['width'], 'height': bbox['height'], 'element_name': node.node_name, 'is_clickable': node.snapshot_node.is_clickable if node.snapshot_node else True, 'is_scrollable': getattr(node, 'is_scrollable', False), 'attributes': node.attributes or {}, 'frame_id': getattr(node, 'frame_id', None), 'node_id': node.node_id, 'backend_node_id': node.backend_node_id, 'xpath': node.xpath, 'text_content': node.get_all_children_text()[:50] if hasattr(node, 'get_all_children_text') else node.node_value[:50]}
                        elements_data.append(element)
            if not elements_data:
                self.logger.debug('⚠️ No valid elements to highlight')
                return
            self.logger.debug(f'📍 Creating highlights for {len(elements_data)} elements')
            await self.remove_highlights()
            import asyncio
            await asyncio.sleep(0.05)
            cdp_session = await self.get_or_create_cdp_session()
            script = f"\n\t\t\t(function() {{\n\t\t\t\t// Interactive elements data\n\t\t\t\tconst interactiveElements = {json.dumps(elements_data)};\n\n\t\t\t\tconsole.log('=== BROWSER-USE HIGHLIGHTING ===');\n\t\t\t\tconsole.log('Highlighting', interactiveElements.length, 'interactive elements');\n\n\t\t\t\t// Double-check: Remove any existing highlight container first\n\t\t\t\tconst existingContainer = document.getElementById('browser-use-debug-highlights');\n\t\t\t\tif (existingContainer) {{\n\t\t\t\t\tconsole.log('⚠️ Found existing highlight container, removing it first');\n\t\t\t\t\texistingContainer.remove();\n\t\t\t\t}}\n\n\t\t\t\t// Also remove any stray highlight elements\n\t\t\t\tconst strayHighlights = document.querySelectorAll('[data-browser-use-highlight]');\n\t\t\t\tif (strayHighlights.length > 0) {{\n\t\t\t\t\tconsole.log('⚠️ Found', strayHighlights.length, 'stray highlight elements, removing them');\n\t\t\t\t\tstrayHighlights.forEach(el => el.remove());\n\t\t\t\t}}\n\n\t\t\t\t// Use maximum z-index for visibility\n\t\t\t\tconst HIGHLIGHT_Z_INDEX = 2147483647;\n\n\t\t\t\t// Create container for all highlights - use FIXED positioning (key insight from v0.6.0)\n\t\t\t\tconst container = document.createElement('div');\n\t\t\t\tcontainer.id = 'browser-use-debug-highlights';\n\t\t\t\tcontainer.setAttribute('data-browser-use-highlight', 'container');\n\n\t\t\t\tcontainer.style.cssText = `\n\t\t\t\t\tposition: absolute;\n\t\t\t\t\ttop: 0;\n\t\t\t\t\tleft: 0;\n\t\t\t\t\twidth: 100vw;\n\t\t\t\t\theight: 100vh;\n\t\t\t\t\tpointer-events: none;\n\t\t\t\t\tz-index: ${{HIGHLIGHT_Z_INDEX}};\n\t\t\t\t\toverflow: visible;\n\t\t\t\t\tmargin: 0;\n\t\t\t\t\tpadding: 0;\n\t\t\t\t\tborder: none;\n\t\t\t\t\toutline: none;\n\t\t\t\t\tbox-shadow: none;\n\t\t\t\t\tbackground: none;\n\t\t\t\t\tfont-family: inherit;\n\t\t\t\t`;\n\n\t\t\t\t// Helper function to create text elements safely\n\t\t\t\tfunction createTextElement(tag, text, styles) {{\n\t\t\t\t\tconst element = document.createElement(tag);\n\t\t\t\t\telement.textContent = text;\n\t\t\t\t\tif (styles) element.style.cssText = styles;\n\t\t\t\t\treturn element;\n\t\t\t\t}}\n\n\t\t\t\t// Add highlights for each element\n\t\t\t\tinteractiveElements.forEach((element, index) => {{\n\t\t\t\t\tconst highlight = document.createElement('div');\n\t\t\t\t\thighlight.setAttribute('data-browser-use-highlight', 'element');\n\t\t\t\t\thighlight.setAttribute('data-element-id', element.backend_node_id);\n\t\t\t\t\thighlight.style.cssText = `\n\t\t\t\t\t\tposition: absolute;\n\t\t\t\t\t\tleft: ${{element.x}}px;\n\t\t\t\t\t\ttop: ${{element.y}}px;\n\t\t\t\t\t\twidth: ${{element.width}}px;\n\t\t\t\t\t\theight: ${{element.height}}px;\n\t\t\t\t\t\toutline: 2px dashed #4a90e2;\n\t\t\t\t\t\toutline-offset: -2px;\n\t\t\t\t\t\tbackground: transparent;\n\t\t\t\t\t\tpointer-events: none;\n\t\t\t\t\t\tbox-sizing: content-box;\n\t\t\t\t\t\ttransition: outline 0.2s ease;\n\t\t\t\t\t\tmargin: 0;\n\t\t\t\t\t\tpadding: 0;\n\t\t\t\t\t\tborder: none;\n\t\t\t\t\t`;\n\n\t\t\t\t\t// Enhanced label with backend node ID\n\t\t\t\t\tconst label = createTextElement('div', element.backend_node_id, `\n\t\t\t\t\t\tposition: absolute;\n\t\t\t\t\t\ttop: -20px;\n\t\t\t\t\t\tleft: 0;\n\t\t\t\t\t\tbackground-color: #4a90e2;\n\t\t\t\t\t\tcolor: white;\n\t\t\t\t\t\tpadding: 2px 6px;\n\t\t\t\t\t\tfont-size: 11px;\n\t\t\t\t\t\tfont-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;\n\t\t\t\t\t\tfont-weight: bold;\n\t\t\t\t\t\tborder-radius: 3px;\n\t\t\t\t\t\twhite-space: nowrap;\n\t\t\t\t\t\tz-index: ${{HIGHLIGHT_Z_INDEX + 1}};\n\t\t\t\t\t\tbox-shadow: 0 2px 4px rgba(0,0,0,0.3);\n\t\t\t\t\t\tborder: none;\n\t\t\t\t\t\toutline: none;\n\t\t\t\t\t\tmargin: 0;\n\t\t\t\t\t\tline-height: 1.2;\n\t\t\t\t\t`);\n\n\t\t\t\t\thighlight.appendChild(label);\n\t\t\t\t\tcontainer.appendChild(highlight);\n\t\t\t\t}});\n\n\t\t\t\t// Add container to document\n\t\t\t\tdocument.body.appendChild(container);\n\n\t\t\t\tconsole.log('Highlighting complete - added', interactiveElements.length, 'highlights');\n\t\t\t\treturn {{ added: interactiveElements.length }};\n\t\t\t}})();\n\t\t\t"
            result = await cdp_session.cdp_client.send.Runtime.evaluate(params={'expression': script, 'returnByValue': True}, session_id=cdp_session.session_id)
            if result and 'result' in result and ('value' in result['result']):
                added_count = result['result']['value'].get('added', 0)
                self.logger.debug(f'Successfully added {added_count} highlight elements to browser DOM')
            else:
                self.logger.debug('Browser highlight injection completed')
        except Exception as e:
            self.logger.warning(f'Failed to add browser highlights: {e}')
            import traceback
            self.logger.debug(f'Browser highlight traceback: {traceback.format_exc()}')

    async def _close_extension_options_pages(self) -> None:
        try:
            page_targets = self.session_manager.get_all_page_targets()
            for target in page_targets:
                target_url = target.url
                target_id = target.target_id
                if 'chrome-extension://' in target_url and ('options.html' in target_url or 'welcome.html' in target_url or 'onboarding.html' in target_url):
                    self.logger.info(f'[BrowserSession] 🚫 Closing extension options page: {target_url}')
                    try:
                        await self._cdp_close_page(target_id)
                    except Exception as e:
                        self.logger.debug(f'[BrowserSession] Could not close extension page {target_id}: {e}')
        except Exception as e:
            self.logger.debug(f'[BrowserSession] Error closing extension options pages: {e}')

    async def send_demo_mode_log(self, message: str, level: str='info', metadata: dict[str, Any] | None=None) -> None:
        return

    @property
    def downloaded_files(self) -> list[str]:
        return self._downloaded_files.copy()

    async def _cdp_get_all_pages(self, include_http: bool=True, include_about: bool=True, include_pages: bool=True, include_iframes: bool=False, include_workers: bool=False, include_chrome: bool=False, include_chrome_extensions: bool=False, include_chrome_error: bool=False) -> list[TargetInfo]:
        if not self.session_manager:
            return []
        result = []
        for target_id, target in self.session_manager.get_all_targets().items():
            target_info: TargetInfo = {'targetId': target.target_id, 'type': target.target_type, 'title': target.title, 'url': target.url, 'attached': True, 'canAccessOpener': False}
            if self._is_valid_target(target_info, include_http=include_http, include_about=include_about, include_pages=include_pages, include_iframes=include_iframes, include_workers=include_workers, include_chrome=include_chrome, include_chrome_extensions=include_chrome_extensions, include_chrome_error=include_chrome_error):
                result.append(target_info)
        return result

    async def _cdp_create_new_page(self, url: str='about:blank', background: bool=False, new_window: bool=False) -> str:
        if self._cdp_client_root:
            result = await self._cdp_client_root.send.Target.createTarget(params={'url': url, 'newWindow': new_window, 'background': background})
        else:
            result = await self.cdp_client.send.Target.createTarget(params={'url': url, 'newWindow': new_window, 'background': background})
        return result['targetId']

    async def _cdp_close_page(self, target_id: TargetID) -> None:
        await self.cdp_client.send.Target.closeTarget(params={'targetId': target_id})

    async def _cdp_get_cookies(self) -> list[Cookie]:
        cdp_session = await self.get_or_create_cdp_session(target_id=None)
        result = await asyncio.wait_for(cdp_session.cdp_client.send.Storage.getCookies(session_id=cdp_session.session_id), timeout=8.0)
        return result.get('cookies', [])

    async def _cdp_set_cookies(self, cookies: list[Cookie]) -> None:
        if not self.agent_focus_target_id or not cookies:
            return
        cdp_session = await self.get_or_create_cdp_session(target_id=None)
        await cdp_session.cdp_client.send.Storage.setCookies(params={'cookies': cookies}, session_id=cdp_session.session_id)

    async def _cdp_clear_cookies(self) -> None:
        cdp_session = await self.get_or_create_cdp_session()
        await cdp_session.cdp_client.send.Storage.clearCookies(session_id=cdp_session.session_id)

    async def _cdp_grant_permissions(self, permissions: list[str], origin: str | None=None) -> None:
        params = {'permissions': permissions}
        cdp_session = await self.get_or_create_cdp_session()
        raise NotImplementedError('Not implemented yet')

    async def _cdp_set_geolocation(self, latitude: float, longitude: float, accuracy: float=100) -> None:
        await self.cdp_client.send.Emulation.setGeolocationOverride(params={'latitude': latitude, 'longitude': longitude, 'accuracy': accuracy})

    async def _cdp_clear_geolocation(self) -> None:
        await self.cdp_client.send.Emulation.clearGeolocationOverride()

    async def _cdp_add_init_script(self, script: str) -> str:
        assert self._cdp_client_root is not None
        cdp_session = await self.get_or_create_cdp_session()
        result = await cdp_session.cdp_client.send.Page.addScriptToEvaluateOnNewDocument(params={'source': script, 'runImmediately': True}, session_id=cdp_session.session_id)
        return result['identifier']

    async def _cdp_remove_init_script(self, identifier: str) -> None:
        cdp_session = await self.get_or_create_cdp_session(target_id=None)
        await cdp_session.cdp_client.send.Page.removeScriptToEvaluateOnNewDocument(params={'identifier': identifier}, session_id=cdp_session.session_id)

    async def _cdp_set_viewport(self, width: int, height: int, device_scale_factor: float=1.0, mobile: bool=False, target_id: str | None=None) -> None:
        if target_id:
            cdp_session = await self.get_or_create_cdp_session(target_id, focus=False)
        elif self.agent_focus_target_id:
            try:
                cdp_session = await self.get_or_create_cdp_session(self.agent_focus_target_id, focus=False)
            except ValueError:
                self.logger.warning('Cannot set viewport: focused target has no sessions')
                return
        else:
            self.logger.warning('Cannot set viewport: no target_id provided and agent_focus not initialized')
            return
        await cdp_session.cdp_client.send.Emulation.setDeviceMetricsOverride(params={'width': width, 'height': height, 'deviceScaleFactor': device_scale_factor, 'mobile': mobile}, session_id=cdp_session.session_id)

    async def _cdp_get_origins(self) -> list[dict[str, Any]]:
        origins = []
        cdp_session = await self.get_or_create_cdp_session(target_id=None)
        try:
            await cdp_session.cdp_client.send.DOMStorage.enable(session_id=cdp_session.session_id)
            try:
                frames_result = await cdp_session.cdp_client.send.Page.getFrameTree(session_id=cdp_session.session_id)
                unique_origins = set()

                def _extract_origins(frame_tree):
                    frame = frame_tree.get('frame', {})
                    origin = frame.get('securityOrigin')
                    if origin and origin != 'null':
                        unique_origins.add(origin)
                    for child in frame_tree.get('childFrames', []):
                        _extract_origins(child)

                async def _get_storage_items(origin: str, is_local_storage: bool) -> list[dict[str, str]] | None:
                    storage_type = 'localStorage' if is_local_storage else 'sessionStorage'
                    try:
                        result = await cdp_session.cdp_client.send.DOMStorage.getDOMStorageItems(params={'storageId': {'securityOrigin': origin, 'isLocalStorage': is_local_storage}}, session_id=cdp_session.session_id)
                        items = []
                        for item in result.get('entries', []):
                            if len(item) == 2:
                                items.append({'name': item[0], 'value': item[1]})
                        return items if items else None
                    except Exception as e:
                        self.logger.debug(f'Failed to get {storage_type} for {origin}: {e}')
                        return None
                _extract_origins(frames_result.get('frameTree', {}))
                for origin in unique_origins:
                    origin_data = {'origin': origin}
                    local_storage = await _get_storage_items(origin, is_local_storage=True)
                    if local_storage:
                        origin_data['localStorage'] = local_storage
                    session_storage = await _get_storage_items(origin, is_local_storage=False)
                    if session_storage:
                        origin_data['sessionStorage'] = session_storage
                    if 'localStorage' in origin_data or 'sessionStorage' in origin_data:
                        origins.append(origin_data)
            finally:
                await cdp_session.cdp_client.send.DOMStorage.disable(session_id=cdp_session.session_id)
        except Exception as e:
            self.logger.warning(f'Failed to get origins: {e}')
        return origins

    async def _cdp_get_storage_state(self) -> dict:
        cookies = await self._cdp_get_cookies()
        origins = await self._cdp_get_origins()
        return {'cookies': cookies, 'origins': origins}

    async def _cdp_navigate(self, url: str, target_id: TargetID | None=None) -> None:
        assert self._cdp_client_root is not None, 'CDP client not initialized - browser may not be connected yet'
        assert self.agent_focus_target_id is not None, 'Agent focus not initialized - browser may not be connected yet'
        target_id_to_use = target_id or self.agent_focus_target_id
        cdp_session = await self.get_or_create_cdp_session(target_id_to_use, focus=True)
        await cdp_session.cdp_client.send.Page.navigate(params={'url': url}, session_id=cdp_session.session_id)

    @staticmethod
    def _is_valid_target(target_info: TargetInfo, include_http: bool=True, include_chrome: bool=False, include_chrome_extensions: bool=False, include_chrome_error: bool=False, include_about: bool=True, include_iframes: bool=True, include_pages: bool=True, include_workers: bool=False) -> bool:
        target_type = target_info.get('type', '')
        url = target_info.get('url', '')
        url_allowed, type_allowed = (False, False)
        from system.utils import is_new_tab_page
        if is_new_tab_page(url):
            url_allowed = True
        if url.startswith('chrome-error://') and include_chrome_error:
            url_allowed = True
        if url.startswith('chrome://') and include_chrome:
            url_allowed = True
        if url.startswith('chrome-extension://') and include_chrome_extensions:
            url_allowed = True
        if url == 'about:blank' and include_about:
            url_allowed = True
        if (url.startswith('http://') or url.startswith('https://')) and include_http:
            url_allowed = True
        if target_type in ('service_worker', 'shared_worker', 'worker') and include_workers:
            type_allowed = True
        if target_type in ('page', 'tab') and include_pages:
            type_allowed = True
        if target_type in ('iframe', 'webview') and include_iframes:
            type_allowed = True
        return url_allowed and type_allowed

    async def get_all_frames(self) -> tuple[dict[str, dict], dict[str, str]]:
        all_frames = {}
        target_sessions = {}
        include_cross_origin = self.browser_profile.cross_origin_iframes
        targets = await self._cdp_get_all_pages(include_http=True, include_about=True, include_pages=True, include_iframes=include_cross_origin, include_workers=False, include_chrome=False, include_chrome_extensions=False, include_chrome_error=include_cross_origin)
        all_targets = targets
        for target in all_targets:
            target_id = target['targetId']
            if not include_cross_origin and target.get('type') == 'iframe':
                continue
            if not include_cross_origin:
                if self.agent_focus_target_id and target_id != self.agent_focus_target_id:
                    continue
                try:
                    cdp_session = await self.get_or_create_cdp_session(self.agent_focus_target_id, focus=False)
                except ValueError:
                    continue
            else:
                cdp_session = await self.get_or_create_cdp_session(target_id, focus=False)
            if cdp_session:
                target_sessions[target_id] = cdp_session.session_id
                try:
                    frame_tree_result = await cdp_session.cdp_client.send.Page.getFrameTree(session_id=cdp_session.session_id)

                    def process_frame_tree(node, parent_frame_id=None):
                        frame = node.get('frame', {})
                        current_frame_id = frame.get('id')
                        if current_frame_id:
                            actual_parent_id = frame.get('parentId') or parent_frame_id
                            frame_info = {**frame, 'frameTargetId': target_id, 'parentFrameId': actual_parent_id, 'childFrameIds': [], 'isCrossOrigin': False, 'isValidTarget': self._is_valid_target(target, include_http=True, include_about=True, include_pages=True, include_iframes=True, include_workers=False, include_chrome=False, include_chrome_extensions=False, include_chrome_error=False)}
                            cross_origin_type = frame.get('crossOriginIsolatedContextType')
                            if cross_origin_type and cross_origin_type != 'NotIsolated':
                                frame_info['isCrossOrigin'] = True
                            if target.get('type') == 'iframe':
                                frame_info['isCrossOrigin'] = True
                            if not include_cross_origin and frame_info.get('isCrossOrigin'):
                                return
                            child_frames = node.get('childFrames', [])
                            for child in child_frames:
                                child_frame = child.get('frame', {})
                                child_frame_id = child_frame.get('id')
                                if child_frame_id:
                                    frame_info['childFrameIds'].append(child_frame_id)
                            if current_frame_id in all_frames:
                                existing = all_frames[current_frame_id]
                                if target.get('type') == 'iframe':
                                    existing['frameTargetId'] = target_id
                                    existing['isCrossOrigin'] = True
                            else:
                                all_frames[current_frame_id] = frame_info
                            if include_cross_origin or not frame_info.get('isCrossOrigin'):
                                for child in child_frames:
                                    process_frame_tree(child, current_frame_id)
                    process_frame_tree(frame_tree_result.get('frameTree', {}))
                except Exception as e:
                    self.logger.debug(f'Failed to get frame tree for target {target_id}: {e}')
        if include_cross_origin:
            await self._populate_frame_metadata(all_frames, target_sessions)
        return (all_frames, target_sessions)

    async def _populate_frame_metadata(self, all_frames: dict[str, dict], target_sessions: dict[str, str]) -> None:
        for frame_id_iter, frame_info in all_frames.items():
            parent_frame_id = frame_info.get('parentFrameId')
            if parent_frame_id and parent_frame_id in all_frames:
                parent_frame_info = all_frames[parent_frame_id]
                parent_target_id = parent_frame_info.get('frameTargetId')
                frame_info['parentTargetId'] = parent_target_id
                if parent_target_id in target_sessions:
                    assert parent_target_id is not None
                    parent_session_id = target_sessions[parent_target_id]
                    try:
                        await self.cdp_client.send.DOM.enable(session_id=parent_session_id)
                        frame_owner = await self.cdp_client.send.DOM.getFrameOwner(params={'frameId': frame_id_iter}, session_id=parent_session_id)
                        if frame_owner:
                            frame_info['backendNodeId'] = frame_owner.get('backendNodeId')
                            frame_info['nodeId'] = frame_owner.get('nodeId')
                    except Exception:
                        pass

    async def find_frame_target(self, frame_id: str, all_frames: dict[str, dict] | None=None) -> dict | None:
        if all_frames is None:
            all_frames, _ = await self.get_all_frames()
        return all_frames.get(frame_id)

    async def cdp_client_for_target(self, target_id: TargetID) -> CDPSession:
        return await self.get_or_create_cdp_session(target_id, focus=False)

    async def cdp_client_for_frame(self, frame_id: str) -> CDPSession:
        if not self.browser_profile.cross_origin_iframes:
            return await self.get_or_create_cdp_session()
        all_frames, target_sessions = await self.get_all_frames()
        frame_info = await self.find_frame_target(frame_id, all_frames)
        if frame_info:
            target_id = frame_info.get('frameTargetId')
            if target_id in target_sessions:
                assert target_id is not None
                session_id = target_sessions[target_id]
                return await self.get_or_create_cdp_session(target_id, focus=False)
        raise ValueError(f"Frame with ID '{frame_id}' not found in any target")

    async def cdp_client_for_node(self, node: EnhancedDOMTreeNode) -> CDPSession:
        if node.session_id and self.session_manager:
            try:
                cdp_session = self.session_manager.get_session(node.session_id)
                if cdp_session:
                    target = self.session_manager.get_target(cdp_session.target_id)
                    self.logger.debug(f'✅ Using session from node.session_id for node {node.backend_node_id}: {target.url}')
                    return cdp_session
            except Exception as e:
                self.logger.debug(f'Failed to get session by session_id {node.session_id}: {e}')
        if node.frame_id:
            try:
                cdp_session = await self.cdp_client_for_frame(node.frame_id)
                target = self.session_manager.get_target(cdp_session.target_id)
                self.logger.debug(f'✅ Using session from node.frame_id for node {node.backend_node_id}: {target.url}')
                return cdp_session
            except Exception as e:
                self.logger.debug(f'Failed to get session for frame {node.frame_id}: {e}')
        if node.target_id:
            try:
                cdp_session = await self.get_or_create_cdp_session(target_id=node.target_id, focus=False)
                target = self.session_manager.get_target(cdp_session.target_id)
                self.logger.debug(f'✅ Using session from node.target_id for node {node.backend_node_id}: {target.url}')
                return cdp_session
            except Exception as e:
                self.logger.debug(f'Failed to get session for target {node.target_id}: {e}')
        if self.agent_focus_target_id:
            target = self.session_manager.get_target(self.agent_focus_target_id)
            try:
                cdp_session = await self.get_or_create_cdp_session(self.agent_focus_target_id, focus=False)
                if target:
                    self.logger.warning(f'⚠️ Node {node.backend_node_id} has no session/frame/target info. Using agent_focus session: {target.url}')
                return cdp_session
            except ValueError:
                pass
        self.logger.error(f'❌ No session info for node {node.backend_node_id} and no agent_focus available. Using main session.')
        return await self.get_or_create_cdp_session()

    @observe_debug(ignore_input=True, ignore_output=True, name='take_screenshot')
    async def take_screenshot(self, path: str | None=None, full_page: bool=False, format: str='png', quality: int | None=None, clip: dict | None=None) -> bytes:
        import base64
        from cdp_use.cdp.page import CaptureScreenshotParameters
        cdp_session = await self.get_or_create_cdp_session()
        params: CaptureScreenshotParameters = {'format': format, 'captureBeyondViewport': full_page}
        if quality is not None and format == 'jpeg':
            params['quality'] = quality
        if clip:
            params['clip'] = {'x': clip['x'], 'y': clip['y'], 'width': clip['width'], 'height': clip['height'], 'scale': 1}
        params = CaptureScreenshotParameters(**params)
        result = await cdp_session.cdp_client.send.Page.captureScreenshot(params=params, session_id=cdp_session.session_id)
        if not result or 'data' not in result:
            raise Exception('Screenshot failed - no data returned')
        screenshot_data = base64.b64decode(result['data'])
        if path:
            Path(path).write_bytes(screenshot_data)
        return screenshot_data

    async def screenshot_element(self, selector: str, path: str | None=None, format: str='png', quality: int | None=None) -> bytes:
        bounds = await self._get_element_bounds(selector)
        if not bounds:
            raise ValueError(f"Element '{selector}' not found or has no bounds")
        return await self.take_screenshot(path=path, format=format, quality=quality, clip=bounds)

    async def _get_element_bounds(self, selector: str) -> dict | None:
        cdp_session = await self.get_or_create_cdp_session()
        doc = await cdp_session.cdp_client.send.DOM.getDocument(params={'depth': 1}, session_id=cdp_session.session_id)
        node_result = await cdp_session.cdp_client.send.DOM.querySelector(params={'nodeId': doc['root']['nodeId'], 'selector': selector}, session_id=cdp_session.session_id)
        node_id = node_result.get('nodeId')
        if not node_id:
            return None
        box_result = await cdp_session.cdp_client.send.DOM.getBoxModel(params={'nodeId': node_id}, session_id=cdp_session.session_id)
        box_model = box_result.get('model')
        if not box_model:
            return None
        content = box_model['content']
        return {'x': min(content[0], content[2], content[4], content[6]), 'y': min(content[1], content[3], content[5], content[7]), 'width': max(content[0], content[2], content[4], content[6]) - min(content[0], content[2], content[4], content[6]), 'height': max(content[1], content[3], content[5], content[7]) - min(content[1], content[3], content[5], content[7])}