import logging
import httpx
from bubus import BaseEvent
from system.config import CONFIG
from system.sync.auth import TEMP_USER_ID, DeviceAuthClient
logger = logging.getLogger(__name__)

class CloudSync:

    def __init__(self, base_url: str | None=None, allow_session_events_for_auth: bool=False):
        self.base_url = base_url or CONFIG.BROWSER_USE_CLOUD_API_URL
        self.auth_client = DeviceAuthClient(base_url=self.base_url)
        self.session_id: str | None = None
        self.allow_session_events_for_auth = allow_session_events_for_auth
        self.auth_flow_active = False
        self.enabled = CONFIG.BROWSER_USE_CLOUD_SYNC

    async def handle_event(self, event: BaseEvent) -> None:
        try:
            if not self.enabled:
                return
            if event.event_type == 'CreateAgentSessionEvent' and hasattr(event, 'id'):
                self.session_id = str(event.id)
            if self.auth_client.is_authenticated:
                await self._send_event(event)
            elif self.allow_session_events_for_auth:
                await self._send_event(event)
                if event.event_type == 'CreateAgentSessionEvent':
                    self.auth_flow_active = True
            else:
                logger.debug(f'Skipping event {event.event_type} - user not authenticated')
        except Exception as e:
            logger.error(f'Failed to handle {event.event_type} event: {type(e).__name__}: {e}', exc_info=True)

    async def _send_event(self, event: BaseEvent) -> None:
        try:
            headers = {}
            if self.auth_client and self.auth_client.is_authenticated:
                current_user_id = getattr(event, 'user_id', None)
                if current_user_id != TEMP_USER_ID:
                    setattr(event, 'user_id', str(self.auth_client.user_id))
            elif not hasattr(event, 'user_id') or not getattr(event, 'user_id', None):
                setattr(event, 'user_id', TEMP_USER_ID)
            if self.auth_client:
                headers.update(self.auth_client.get_headers())
            async with httpx.AsyncClient() as client:
                event_data = event.model_dump(mode='json')
                if self.auth_client and self.auth_client.device_id:
                    event_data['device_id'] = self.auth_client.device_id
                response = await client.post(f"{self.base_url.rstrip('/')}/api/v1/events", json={'events': [event_data]}, headers=headers, timeout=10.0)
                if response.status_code >= 400:
                    logger.debug(f'Failed to send sync event: POST {response.request.url} {response.status_code} - {response.text}')
        except httpx.TimeoutException:
            logger.debug(f'Event send timed out after 10 seconds: {event}')
        except httpx.ConnectError as e:
            pass
        except httpx.HTTPError as e:
            logger.debug(f'HTTP error sending event {event}: {type(e).__name__}: {e}')
        except Exception as e:
            logger.debug(f'Unexpected error sending event {event}: {type(e).__name__}: {e}')

    def set_auth_flow_active(self) -> None:
        self.auth_flow_active = True

    async def authenticate(self, show_instructions: bool=True) -> bool:
        if not self.enabled:
            return False
        if self.auth_client.is_authenticated:
            import logging
            logger = logging.getLogger(__name__)
            if show_instructions:
                logger.info('✅ Already authenticated! Skipping OAuth flow.')
            return True
        return await self.auth_client.authenticate(agent_session_id=self.session_id, show_instructions=show_instructions)