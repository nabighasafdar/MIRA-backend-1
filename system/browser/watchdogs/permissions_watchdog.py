from typing import TYPE_CHECKING, ClassVar
from bubus import BaseEvent
from system.browser.events import BrowserConnectedEvent
from system.browser.watchdog_base import BaseWatchdog
if TYPE_CHECKING:
    pass

class PermissionsWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [BrowserConnectedEvent]
    EMITS: ClassVar[list[type[BaseEvent]]] = []

    async def on_BrowserConnectedEvent(self, event: BrowserConnectedEvent) -> None:
        permissions = self.browser_session.browser_profile.permissions
        if not permissions:
            self.logger.debug('No permissions to grant')
            return
        self.logger.debug(f'🔓 Granting browser permissions: {permissions}')
        try:
            await self.browser_session.cdp_client.send.Browser.grantPermissions(params={'permissions': permissions})
            self.logger.debug(f'✅ Successfully granted permissions: {permissions}')
        except Exception as e:
            self.logger.error(f'❌ Failed to grant permissions: {str(e)}')