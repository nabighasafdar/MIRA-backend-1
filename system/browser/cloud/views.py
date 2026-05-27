from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
ProxyCountryCode = Literal['us', 'uk', 'fr', 'it', 'jp', 'au', 'de', 'fi', 'ca', 'in'] | str
MAX_FREE_USER_SESSION_TIMEOUT = 15
MAX_PAID_USER_SESSION_TIMEOUT = 240

class CreateBrowserRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    profile_id: UUID | str | None = Field(default=None, alias='cloud_profile_id', description='The ID of the profile to use for the session. Can be a UUID or a string of UUID.', title='Cloud Profile ID')
    proxy_country_code: ProxyCountryCode | None = Field(default=None, alias='cloud_proxy_country_code', description='Country code for proxy location.', title='Cloud Proxy Country Code')
    timeout: int | None = Field(ge=1, le=MAX_PAID_USER_SESSION_TIMEOUT, default=None, alias='cloud_timeout', description=f'The timeout for the session in minutes. Free users are limited to {MAX_FREE_USER_SESSION_TIMEOUT} minutes, paid users can use up to {MAX_PAID_USER_SESSION_TIMEOUT} minutes ({MAX_PAID_USER_SESSION_TIMEOUT // 60} hours).', title='Cloud Timeout')
CloudBrowserParams = CreateBrowserRequest

class CloudBrowserResponse(BaseModel):
    id: str
    status: str
    liveUrl: str = Field(alias='liveUrl')
    cdpUrl: str = Field(alias='cdpUrl')
    timeoutAt: str = Field(alias='timeoutAt')
    startedAt: str = Field(alias='startedAt')
    finishedAt: str | None = Field(alias='finishedAt', default=None)

class CloudBrowserError(Exception):
    pass

class CloudBrowserAuthError(CloudBrowserError):
    pass