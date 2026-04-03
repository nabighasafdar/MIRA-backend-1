import asyncio
import json
import os
import shutil
import time
from datetime import datetime
import httpx
from pydantic import BaseModel
from uuid_extensions import uuid7str
from system.config import CONFIG
TEMP_USER_ID = '99999999-9999-9999-9999-999999999999'

def get_or_create_device_id() -> str:
    device_id_path = CONFIG.BROWSER_USE_CONFIG_DIR / 'device_id'
    if device_id_path.exists():
        try:
            device_id = device_id_path.read_text().strip()
            if device_id:
                return device_id
        except Exception:
            pass
    device_id = uuid7str()
    CONFIG.BROWSER_USE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    device_id_path.write_text(device_id)
    return device_id

class CloudAuthConfig(BaseModel):
    api_token: str | None = None
    user_id: str | None = None
    authorized_at: datetime | None = None

    @classmethod
    def load_from_file(cls) -> 'CloudAuthConfig':
        config_path = CONFIG.BROWSER_USE_CONFIG_DIR / 'cloud_auth.json'
        if config_path.exists():
            try:
                with open(config_path) as f:
                    data = json.load(f)
                return cls.model_validate(data)
            except Exception:
                pass
        return cls()

    def save_to_file(self) -> None:
        CONFIG.BROWSER_USE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config_path = CONFIG.BROWSER_USE_CONFIG_DIR / 'cloud_auth.json'
        with open(config_path, 'w') as f:
            json.dump(self.model_dump(mode='json'), f, indent=2, default=str)
        try:
            os.chmod(config_path, 384)
        except Exception:
            pass

class DeviceAuthClient:

    def __init__(self, base_url: str | None=None, http_client: httpx.AsyncClient | None=None):
        self.base_url = base_url or CONFIG.BROWSER_USE_CLOUD_API_URL
        self.client_id = 'library'
        self.scope = 'read write'
        self.http_client = http_client
        self.temp_user_id = TEMP_USER_ID
        self.device_id = get_or_create_device_id()
        self.auth_config = CloudAuthConfig.load_from_file()

    @property
    def is_authenticated(self) -> bool:
        return bool(self.auth_config.api_token and self.auth_config.user_id)

    @property
    def api_token(self) -> str | None:
        return self.auth_config.api_token

    @property
    def user_id(self) -> str:
        return self.auth_config.user_id or self.temp_user_id

    async def start_device_authorization(self, agent_session_id: str | None=None) -> dict:
        if self.http_client:
            response = await self.http_client.post(f"{self.base_url.rstrip('/')}/api/v1/oauth/device/authorize", data={'client_id': self.client_id, 'scope': self.scope, 'agent_session_id': agent_session_id or '', 'device_id': self.device_id})
            response.raise_for_status()
            return response.json()
        else:
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{self.base_url.rstrip('/')}/api/v1/oauth/device/authorize", data={'client_id': self.client_id, 'scope': self.scope, 'agent_session_id': agent_session_id or '', 'device_id': self.device_id})
                response.raise_for_status()
                return response.json()

    async def poll_for_token(self, device_code: str, interval: float=3.0, timeout: float=1800.0) -> dict | None:
        start_time = time.time()
        if self.http_client:
            while time.time() - start_time < timeout:
                try:
                    response = await self.http_client.post(f"{self.base_url.rstrip('/')}/api/v1/oauth/device/token", data={'grant_type': 'urn:ietf:params:oauth:grant-type:device_code', 'device_code': device_code, 'client_id': self.client_id})
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('error') == 'authorization_pending':
                            await asyncio.sleep(interval)
                            continue
                        if data.get('error') == 'slow_down':
                            interval = data.get('interval', interval * 2)
                            await asyncio.sleep(interval)
                            continue
                        if 'error' in data:
                            print(f"Error: {data.get('error_description', data['error'])}")
                            return None
                        if 'access_token' in data:
                            return data
                    elif response.status_code == 400:
                        data = response.json()
                        if data.get('error') not in ['authorization_pending', 'slow_down']:
                            print(f"Error: {data.get('error_description', 'Unknown error')}")
                            return None
                    else:
                        print(f'Unexpected status code: {response.status_code}')
                        return None
                except Exception as e:
                    print(f'Error polling for token: {e}')
                await asyncio.sleep(interval)
        else:
            async with httpx.AsyncClient() as client:
                while time.time() - start_time < timeout:
                    try:
                        response = await client.post(f"{self.base_url.rstrip('/')}/api/v1/oauth/device/token", data={'grant_type': 'urn:ietf:params:oauth:grant-type:device_code', 'device_code': device_code, 'client_id': self.client_id})
                        if response.status_code == 200:
                            data = response.json()
                            if data.get('error') == 'authorization_pending':
                                await asyncio.sleep(interval)
                                continue
                            if data.get('error') == 'slow_down':
                                interval = data.get('interval', interval * 2)
                                await asyncio.sleep(interval)
                                continue
                            if 'error' in data:
                                print(f"Error: {data.get('error_description', data['error'])}")
                                return None
                            if 'access_token' in data:
                                return data
                        elif response.status_code == 400:
                            data = response.json()
                            if data.get('error') not in ['authorization_pending', 'slow_down']:
                                print(f"Error: {data.get('error_description', 'Unknown error')}")
                                return None
                        else:
                            print(f'Unexpected status code: {response.status_code}')
                            return None
                    except Exception as e:
                        print(f'Error polling for token: {e}')
                    await asyncio.sleep(interval)
        return None

    async def authenticate(self, agent_session_id: str | None=None, show_instructions: bool=True) -> bool:
        import logging
        logger = logging.getLogger(__name__)
        try:
            device_auth = await self.start_device_authorization(agent_session_id)
            frontend_url = CONFIG.BROWSER_USE_CLOUD_UI_URL or self.base_url.replace('//api.', '//cloud.')
            verification_uri = device_auth['verification_uri'].replace(self.base_url, frontend_url)
            verification_uri_complete = device_auth['verification_uri_complete'].replace(self.base_url, frontend_url)
            terminal_width, _terminal_height = shutil.get_terminal_size((80, 20))
            if show_instructions and CONFIG.BROWSER_USE_CLOUD_SYNC:
                logger.info('─' * max(terminal_width - 40, 20))
                logger.info('🌐  View the details of this run in Browser Use Cloud:')
                logger.info(f'    👉  {verification_uri_complete}')
                logger.info('─' * max(terminal_width - 40, 20) + '\n')
            token_data = await self.poll_for_token(device_code=device_auth['device_code'], interval=device_auth.get('interval', 5))
            if token_data and token_data.get('access_token'):
                self.auth_config.api_token = token_data['access_token']
                self.auth_config.user_id = token_data.get('user_id', self.temp_user_id)
                self.auth_config.authorized_at = datetime.now()
                self.auth_config.save_to_file()
                if show_instructions:
                    logger.debug('✅  Authentication successful! Cloud sync is now enabled with your browser-use account.')
                return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning('Cloud sync authentication endpoint not found (404). Check your BROWSER_USE_CLOUD_API_URL setting.')
            else:
                logger.warning(f'Failed to authenticate with cloud service: HTTP {e.response.status_code} - {e.response.text}')
        except httpx.RequestError as e:
            pass
        except Exception as e:
            logger.warning(f'❌ Unexpected error during cloud sync authentication: {type(e).__name__}: {e}')
        if show_instructions:
            logger.debug(f'❌ Sync authentication failed or timed out with {CONFIG.BROWSER_USE_CLOUD_API_URL}')
        return False

    def get_headers(self) -> dict:
        if self.api_token:
            return {'Authorization': f'Bearer {self.api_token}'}
        return {}

    def clear_auth(self) -> None:
        self.auth_config = CloudAuthConfig()
        config_path = CONFIG.BROWSER_USE_CONFIG_DIR / 'cloud_auth.json'
        config_path.unlink(missing_ok=True)