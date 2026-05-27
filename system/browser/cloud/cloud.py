import logging
import os
import httpx
from system.browser.cloud.views import CloudBrowserAuthError, CloudBrowserError, CloudBrowserResponse, CreateBrowserRequest
from system.sync.auth import CloudAuthConfig
logger = logging.getLogger(__name__)

class CloudBrowserClient:

    def __init__(self, api_base_url: str='https://api.browser-use.com'):
        self.api_base_url = api_base_url
        self.client = httpx.AsyncClient(timeout=30.0)
        self.current_session_id: str | None = None

    async def create_browser(self, request: CreateBrowserRequest, extra_headers: dict[str, str] | None=None) -> CloudBrowserResponse:
        url = f'{self.api_base_url}/api/v2/browsers'
        api_token = os.getenv('BROWSER_USE_API_KEY')
        if not api_token:
            try:
                auth_config = CloudAuthConfig.load_from_file()
                api_token = auth_config.api_token
            except Exception:
                pass
        if not api_token:
            raise CloudBrowserAuthError('No authentication token found. Please set BROWSER_USE_API_KEY environment variable to authenticate with the cloud service. You can also create an API key at https://cloud.browser-use.com/new-api-key')
        headers = {'X-Browser-Use-API-Key': api_token, 'Content-Type': 'application/json', **(extra_headers or {})}
        request_body = request.model_dump(exclude_unset=True)
        try:
            logger.info('🌤️ Creating cloud browser instance...')
            response = await self.client.post(url, headers=headers, json=request_body)
            if response.status_code == 401:
                raise CloudBrowserAuthError('Authentication failed. Please make sure you have set BROWSER_USE_API_KEY environment variable to authenticate with the cloud service. You can also create an API key at https://cloud.browser-use.com/new-api-key')
            elif response.status_code == 403:
                raise CloudBrowserAuthError('Access forbidden. Please check your browser-use cloud subscription status.')
            elif not response.is_success:
                error_msg = f'Failed to create cloud browser: HTTP {response.status_code}'
                try:
                    error_data = response.json()
                    if 'detail' in error_data:
                        error_msg += f" - {error_data['detail']}"
                except Exception:
                    pass
                raise CloudBrowserError(error_msg)
            browser_data = response.json()
            browser_response = CloudBrowserResponse(**browser_data)
            self.current_session_id = browser_response.id
            logger.info(f'🌤️ Cloud browser created successfully: {browser_response.id}')
            logger.debug(f'🌤️ CDP URL: {browser_response.cdpUrl}')
            logger.info(f'\x1b[36m🔗 Live URL: {browser_response.liveUrl}\x1b[0m')
            return browser_response
        except httpx.TimeoutException:
            raise CloudBrowserError('Timeout while creating cloud browser. Please try again.')
        except httpx.ConnectError:
            raise CloudBrowserError('Failed to connect to cloud browser service. Please check your internet connection.')
        except Exception as e:
            if isinstance(e, (CloudBrowserError, CloudBrowserAuthError)):
                raise
            raise CloudBrowserError(f'Unexpected error creating cloud browser: {e}')

    async def stop_browser(self, session_id: str | None=None, extra_headers: dict[str, str] | None=None) -> CloudBrowserResponse:
        if session_id is None:
            session_id = self.current_session_id
        if not session_id:
            raise CloudBrowserError('No session ID provided and no current session available')
        url = f'{self.api_base_url}/api/v2/browsers/{session_id}'
        api_token = os.getenv('BROWSER_USE_API_KEY')
        if not api_token:
            try:
                auth_config = CloudAuthConfig.load_from_file()
                api_token = auth_config.api_token
            except Exception:
                pass
        if not api_token:
            raise CloudBrowserAuthError('No authentication token found. Please set BROWSER_USE_API_KEY environment variable to authenticate with the cloud service. You can also create an API key at https://cloud.browser-use.com/new-api-key')
        headers = {'X-Browser-Use-API-Key': api_token, 'Content-Type': 'application/json', **(extra_headers or {})}
        request_body = {'action': 'stop'}
        try:
            logger.info(f'🌤️ Stopping cloud browser session: {session_id}')
            response = await self.client.patch(url, headers=headers, json=request_body)
            if response.status_code == 401:
                raise CloudBrowserAuthError('Authentication failed. Please make sure you have set the BROWSER_USE_API_KEY environment variable to authenticate with the cloud service.')
            elif response.status_code == 404:
                logger.debug(f'🌤️ Cloud browser session {session_id} not found (already stopped)')
                if session_id == self.current_session_id:
                    self.current_session_id = None
                raise CloudBrowserError(f'Cloud browser session {session_id} not found')
            elif not response.is_success:
                error_msg = f'Failed to stop cloud browser: HTTP {response.status_code}'
                try:
                    error_data = response.json()
                    if 'detail' in error_data:
                        error_msg += f" - {error_data['detail']}"
                except Exception:
                    pass
                raise CloudBrowserError(error_msg)
            browser_data = response.json()
            browser_response = CloudBrowserResponse(**browser_data)
            if session_id == self.current_session_id:
                self.current_session_id = None
            logger.info(f'🌤️ Cloud browser session stopped: {browser_response.id}')
            logger.debug(f'🌤️ Status: {browser_response.status}')
            return browser_response
        except httpx.TimeoutException:
            raise CloudBrowserError('Timeout while stopping cloud browser. Please try again.')
        except httpx.ConnectError:
            raise CloudBrowserError('Failed to connect to cloud browser service. Please check your internet connection.')
        except Exception as e:
            if isinstance(e, (CloudBrowserError, CloudBrowserAuthError)):
                raise
            raise CloudBrowserError(f'Unexpected error stopping cloud browser: {e}')

    async def close(self):
        if self.current_session_id:
            try:
                await self.stop_browser()
            except Exception as e:
                logger.debug(f'Failed to stop cloud browser session during cleanup: {e}')
        await self.client.aclose()