import inspect
import os
from typing import Any, Literal
from bubus import BaseEvent
from bubus.models import T_EventResultType
from cdp_use.cdp.target import TargetID
from pydantic import BaseModel, Field, field_validator
from system.browser.views import BrowserStateSummary
from system.dom.views import EnhancedDOMTreeNode

def _get_timeout(env_var: str, default: float) -> float | None:
    env_value = os.getenv(env_var)
    if env_value:
        try:
            parsed = float(env_value)
            if parsed < 0:
                print(f'Warning: {env_var}={env_value} is negative, using default {default}')
                return default
            return parsed
        except (ValueError, TypeError):
            print(f'Warning: {env_var}={env_value} is not a valid number, using default {default}')
    return default

class ElementSelectedEvent(BaseEvent[T_EventResultType]):
    node: EnhancedDOMTreeNode

    @field_validator('node', mode='before')
    @classmethod
    def serialize_node(cls, data: EnhancedDOMTreeNode | None) -> EnhancedDOMTreeNode | None:
        if data is None:
            return None
        return EnhancedDOMTreeNode(node_id=data.node_id, backend_node_id=data.backend_node_id, session_id=data.session_id, frame_id=data.frame_id, target_id=data.target_id, node_type=data.node_type, node_name=data.node_name, node_value=data.node_value, attributes=data.attributes, is_scrollable=data.is_scrollable, is_visible=data.is_visible, absolute_position=data.absolute_position, content_document=None, shadow_root_type=None, shadow_roots=[], parent_node=None, children_nodes=[], ax_node=None, snapshot_node=None)

class NavigateToUrlEvent(BaseEvent[None]):
    url: str
    wait_until: Literal['load', 'domcontentloaded', 'networkidle', 'commit'] = 'load'
    timeout_ms: int | None = None
    new_tab: bool = Field(default=False, description='Set True to leave the current tab alone and open a new tab in the foreground for the new URL')
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_NavigateToUrlEvent', 30.0))

class ClickElementEvent(ElementSelectedEvent[dict[str, Any] | None]):
    node: 'EnhancedDOMTreeNode'
    button: Literal['left', 'right', 'middle'] = 'left'
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ClickElementEvent', 15.0))

class ClickCoordinateEvent(BaseEvent[dict]):
    coordinate_x: int
    coordinate_y: int
    button: Literal['left', 'right', 'middle'] = 'left'
    force: bool = False
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ClickCoordinateEvent', 15.0))

class TypeTextEvent(ElementSelectedEvent[dict | None]):
    node: 'EnhancedDOMTreeNode'
    text: str
    clear: bool = True
    is_sensitive: bool = False
    sensitive_key_name: str | None = None
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_TypeTextEvent', 60.0))

class ScrollEvent(ElementSelectedEvent[None]):
    direction: Literal['up', 'down', 'left', 'right']
    amount: int
    node: 'EnhancedDOMTreeNode | None' = None
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ScrollEvent', 8.0))

class SwitchTabEvent(BaseEvent[TargetID]):
    target_id: TargetID | None = Field(default=None, description='None means switch to the most recently opened tab')
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_SwitchTabEvent', 10.0))

class CloseTabEvent(BaseEvent[None]):
    target_id: TargetID
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_CloseTabEvent', 10.0))

class ScreenshotEvent(BaseEvent[str]):
    full_page: bool = False
    clip: dict[str, float] | None = None
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ScreenshotEvent', 15.0))

class BrowserStateRequestEvent(BaseEvent[BrowserStateSummary]):
    include_dom: bool = True
    include_screenshot: bool = True
    include_recent_events: bool = False
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserStateRequestEvent', 30.0))

class GoBackEvent(BaseEvent[None]):
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_GoBackEvent', 15.0))

class GoForwardEvent(BaseEvent[None]):
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_GoForwardEvent', 15.0))

class RefreshEvent(BaseEvent[None]):
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_RefreshEvent', 15.0))

class WaitEvent(BaseEvent[None]):
    seconds: float = 3.0
    max_seconds: float = 10.0
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_WaitEvent', 60.0))

class SendKeysEvent(BaseEvent[None]):
    keys: str
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_SendKeysEvent', 60.0))

class UploadFileEvent(ElementSelectedEvent[None]):
    node: 'EnhancedDOMTreeNode'
    file_path: str
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_UploadFileEvent', 30.0))

class GetDropdownOptionsEvent(ElementSelectedEvent[dict[str, str]]):
    node: 'EnhancedDOMTreeNode'
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_GetDropdownOptionsEvent', 15.0))

class SelectDropdownOptionEvent(ElementSelectedEvent[dict[str, str]]):
    node: 'EnhancedDOMTreeNode'
    text: str
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_SelectDropdownOptionEvent', 8.0))

class ScrollToTextEvent(BaseEvent[None]):
    text: str
    direction: Literal['up', 'down'] = 'down'
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ScrollToTextEvent', 15.0))

class BrowserStartEvent(BaseEvent):
    cdp_url: str | None = None
    launch_options: dict[str, Any] = Field(default_factory=dict)
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserStartEvent', 30.0))

class BrowserStopEvent(BaseEvent):
    force: bool = False
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserStopEvent', 45.0))

class BrowserLaunchResult(BaseModel):
    cdp_url: str

class BrowserLaunchEvent(BaseEvent[BrowserLaunchResult]):
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserLaunchEvent', 30.0))

class BrowserKillEvent(BaseEvent):
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserKillEvent', 30.0))

class BrowserConnectedEvent(BaseEvent):
    cdp_url: str
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserConnectedEvent', 30.0))

class BrowserStoppedEvent(BaseEvent):
    reason: str | None = None
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserStoppedEvent', 30.0))

class TabCreatedEvent(BaseEvent):
    target_id: TargetID
    url: str
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_TabCreatedEvent', 30.0))

class TabClosedEvent(BaseEvent):
    target_id: TargetID
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_TabClosedEvent', 3.0))

class AgentFocusChangedEvent(BaseEvent):
    target_id: TargetID
    url: str
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_AgentFocusChangedEvent', 10.0))

class TargetCrashedEvent(BaseEvent):
    target_id: TargetID
    error: str
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_TargetCrashedEvent', 10.0))

class NavigationStartedEvent(BaseEvent):
    target_id: TargetID
    url: str
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_NavigationStartedEvent', 30.0))

class NavigationCompleteEvent(BaseEvent):
    target_id: TargetID
    url: str
    status: int | None = None
    error_message: str | None = None
    loading_status: str | None = None
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_NavigationCompleteEvent', 30.0))

class BrowserErrorEvent(BaseEvent):
    error_type: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserErrorEvent', 30.0))

class BrowserReconnectingEvent(BaseEvent):
    cdp_url: str
    attempt: int
    max_attempts: int
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserReconnectingEvent', 30.0))

class BrowserReconnectedEvent(BaseEvent):
    cdp_url: str
    attempt: int
    downtime_seconds: float
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserReconnectedEvent', 30.0))

class SaveStorageStateEvent(BaseEvent):
    path: str | None = None
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_SaveStorageStateEvent', 45.0))

class StorageStateSavedEvent(BaseEvent):
    path: str
    cookies_count: int
    origins_count: int
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_StorageStateSavedEvent', 30.0))

class LoadStorageStateEvent(BaseEvent):
    path: str | None = None
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_LoadStorageStateEvent', 45.0))

class StorageStateLoadedEvent(BaseEvent):
    path: str
    cookies_count: int
    origins_count: int
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_StorageStateLoadedEvent', 30.0))

class DownloadStartedEvent(BaseEvent):
    guid: str
    url: str
    suggested_filename: str
    auto_download: bool = False
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_DownloadStartedEvent', 5.0))

class DownloadProgressEvent(BaseEvent):
    guid: str
    received_bytes: int
    total_bytes: int
    state: str
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_DownloadProgressEvent', 5.0))

class FileDownloadedEvent(BaseEvent):
    guid: str | None = None
    url: str
    path: str
    file_name: str
    file_size: int
    file_type: str | None = None
    mime_type: str | None = None
    from_cache: bool = False
    auto_download: bool = False
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_FileDownloadedEvent', 30.0))

class AboutBlankDVDScreensaverShownEvent(BaseEvent):
    target_id: TargetID
    error: str | None = None

class DialogOpenedEvent(BaseEvent):
    dialog_type: str
    message: str
    url: str
    frame_id: str | None = None

class CaptchaSolverStartedEvent(BaseEvent):
    target_id: TargetID
    vendor: str
    url: str
    started_at: int
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_CaptchaSolverStartedEvent', 5.0))

class CaptchaSolverFinishedEvent(BaseEvent):
    target_id: TargetID
    vendor: str
    url: str
    duration_ms: int
    finished_at: int
    success: bool
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_CaptchaSolverFinishedEvent', 5.0))

def _check_event_names_dont_overlap():
    event_names = {name.split('[')[0] for name in globals().keys() if not name.startswith('_') and inspect.isclass(globals()[name]) and issubclass(globals()[name], BaseEvent) and (name != 'BaseEvent')}
    for name_a in event_names:
        assert name_a.endswith('Event'), f'Event with name {name_a} does not end with "Event"'
        for name_b in event_names:
            if name_a != name_b:
                assert name_a not in name_b, f'Event with name {name_a} is a substring of {name_b}, all events must be completely unique to avoid find-and-replace accidents'
_check_event_names_dont_overlap()