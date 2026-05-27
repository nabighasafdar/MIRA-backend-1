from typing import TYPE_CHECKING, ClassVar
from bubus import BaseEvent
from system.browser.events import BrowserErrorEvent, NavigateToUrlEvent, NavigationCompleteEvent, TabCreatedEvent
from system.browser.watchdog_base import BaseWatchdog
if TYPE_CHECKING:
    pass
_GLOB_WARNING_SHOWN = False

class SecurityWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [NavigateToUrlEvent, NavigationCompleteEvent, TabCreatedEvent]
    EMITS: ClassVar[list[type[BaseEvent]]] = [BrowserErrorEvent]

    async def on_NavigateToUrlEvent(self, event: NavigateToUrlEvent) -> None:
        if not self._is_url_allowed(event.url):
            self.logger.warning(f'⛔️ Blocking navigation to disallowed URL: {event.url}')
            self.event_bus.dispatch(BrowserErrorEvent(error_type='NavigationBlocked', message=f'Navigation blocked to disallowed URL: {event.url}', details={'url': event.url, 'reason': 'not_in_allowed_domains'}))
            raise ValueError(f'Navigation to {event.url} blocked by security policy')

    async def on_NavigationCompleteEvent(self, event: NavigationCompleteEvent) -> None:
        if not self._is_url_allowed(event.url):
            self.logger.warning(f'⛔️ Navigation to non-allowed URL detected: {event.url}')
            self.event_bus.dispatch(BrowserErrorEvent(error_type='NavigationBlocked', message=f'Navigation blocked to non-allowed URL: {event.url} - redirecting to about:blank', details={'url': event.url, 'target_id': event.target_id}))
            try:
                session = await self.browser_session.get_or_create_cdp_session(target_id=event.target_id)
                await session.cdp_client.send.Page.navigate(params={'url': 'about:blank'}, session_id=session.session_id)
                self.logger.info(f'⛔️ Navigated to about:blank after blocked URL: {event.url}')
            except Exception as e:
                pass
                self.logger.error(f'⛔️ Failed to navigate to about:blank: {type(e).__name__} {e}')

    async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
        if not self._is_url_allowed(event.url):
            self.logger.warning(f'⛔️ New tab created with disallowed URL: {event.url}')
            self.event_bus.dispatch(BrowserErrorEvent(error_type='TabCreationBlocked', message=f'Tab created with non-allowed URL: {event.url}', details={'url': event.url, 'target_id': event.target_id}))
            try:
                await self.browser_session._cdp_close_page(event.target_id)
                self.logger.info(f'⛔️ Closed new tab with non-allowed URL: {event.url}')
            except Exception as e:
                self.logger.error(f'⛔️ Failed to close new tab with non-allowed URL: {type(e).__name__} {e}')

    def _is_root_domain(self, domain: str) -> bool:
        if '*' in domain or '://' in domain:
            return False
        return domain.count('.') == 1

    def _log_glob_warning(self) -> None:
        global _GLOB_WARNING_SHOWN
        if not _GLOB_WARNING_SHOWN:
            _GLOB_WARNING_SHOWN = True
            self.logger.warning('⚠️ Using glob patterns in allowed_domains. Note: Patterns like "*.example.com" will match both subdomains AND the main domain.')

    def _get_domain_variants(self, host: str) -> tuple[str, str]:
        if host.startswith('www.'):
            return (host, host[4:])
        else:
            return (host, f'www.{host}')

    def _is_ip_address(self, host: str) -> bool:
        import ipaddress
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False
        except Exception:
            return False

    def _is_url_allowed(self, url: str) -> bool:
        if url in ['about:blank', 'chrome://new-tab-page/', 'chrome://new-tab-page', 'chrome://newtab/']:
            return True
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme in ['data', 'blob']:
            return True
        host = parsed.hostname
        if not host:
            return False
        if self.browser_session.browser_profile.block_ip_addresses:
            if self._is_ip_address(host):
                return False
        if not self.browser_session.browser_profile.allowed_domains and (not self.browser_session.browser_profile.prohibited_domains):
            return True
        if self.browser_session.browser_profile.allowed_domains:
            allowed_domains = self.browser_session.browser_profile.allowed_domains
            if isinstance(allowed_domains, set):
                host_variant, host_alt = self._get_domain_variants(host)
                return host_variant in allowed_domains or host_alt in allowed_domains
            else:
                for pattern in allowed_domains:
                    if self._is_url_match(url, host, parsed.scheme, pattern):
                        return True
                return False
        if self.browser_session.browser_profile.prohibited_domains:
            prohibited_domains = self.browser_session.browser_profile.prohibited_domains
            if isinstance(prohibited_domains, set):
                host_variant, host_alt = self._get_domain_variants(host)
                return host_variant not in prohibited_domains and host_alt not in prohibited_domains
            else:
                for pattern in prohibited_domains:
                    if self._is_url_match(url, host, parsed.scheme, pattern):
                        return False
                return True
        return True

    def _is_url_match(self, url: str, host: str, scheme: str, pattern: str) -> bool:
        full_url_pattern = f'{scheme}://{host}'
        if '*' in pattern:
            self._log_glob_warning()
            import fnmatch
            if pattern.startswith('*.'):
                domain_part = pattern[2:]
                if host == domain_part or host.endswith('.' + domain_part):
                    if scheme in ['http', 'https']:
                        return True
            elif pattern.endswith('/*'):
                if fnmatch.fnmatch(url, pattern):
                    return True
            elif fnmatch.fnmatch(full_url_pattern if '://' in pattern else host, pattern):
                return True
        elif '://' in pattern:
            if url.startswith(pattern):
                return True
        else:
            if host.lower() == pattern.lower():
                return True
            if self._is_root_domain(pattern) and host.lower() == f'www.{pattern.lower()}':
                return True
        return False