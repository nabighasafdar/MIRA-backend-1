from dataclasses import dataclass, field
from typing import Any
from bubus import BaseEvent
from cdp_use.cdp.target import TargetID
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_serializer
from system.dom.views import DOMInteractedElement, SerializedDOMState
PLACEHOLDER_4PX_SCREENSHOT = 'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAFElEQVR4nGP8//8/AwwwMSAB3BwAlm4DBfIlvvkAAAAASUVORK5CYII='

class TabInfo(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_by_name=True, validate_by_alias=True, populate_by_name=True)
    url: str
    title: str
    target_id: TargetID = Field(serialization_alias='tab_id', validation_alias=AliasChoices('tab_id', 'target_id'))
    parent_target_id: TargetID | None = Field(default=None, serialization_alias='parent_tab_id', validation_alias=AliasChoices('parent_tab_id', 'parent_target_id'))

    @field_serializer('target_id')
    def serialize_target_id(self, target_id: TargetID, _info: Any) -> str:
        return target_id[-4:]

    @field_serializer('parent_target_id')
    def serialize_parent_target_id(self, parent_target_id: TargetID | None, _info: Any) -> str | None:
        return parent_target_id[-4:] if parent_target_id else None

class PageInfo(BaseModel):
    viewport_width: int
    viewport_height: int
    page_width: int
    page_height: int
    scroll_x: int
    scroll_y: int
    pixels_above: int
    pixels_below: int
    pixels_left: int
    pixels_right: int

@dataclass
class NetworkRequest:
    url: str
    method: str = 'GET'
    loading_duration_ms: float = 0.0
    resource_type: str | None = None

@dataclass
class PaginationButton:
    button_type: str
    backend_node_id: int
    text: str
    selector: str
    is_disabled: bool = False

@dataclass
class BrowserStateSummary:
    dom_state: SerializedDOMState
    url: str
    title: str
    tabs: list[TabInfo]
    screenshot: str | None = field(default=None, repr=False)
    page_info: PageInfo | None = None
    pixels_above: int = 0
    pixels_below: int = 0
    browser_errors: list[str] = field(default_factory=list)
    is_pdf_viewer: bool = False
    recent_events: str | None = None
    pending_network_requests: list[NetworkRequest] = field(default_factory=list)
    pagination_buttons: list[PaginationButton] = field(default_factory=list)
    closed_popup_messages: list[str] = field(default_factory=list)

@dataclass
class BrowserStateHistory:
    url: str
    title: str
    tabs: list[TabInfo]
    interacted_element: list[DOMInteractedElement | None] | list[None]
    screenshot_path: str | None = None

    def get_screenshot(self) -> str | None:
        if not self.screenshot_path:
            return None
        import base64
        from pathlib import Path
        path_obj = Path(self.screenshot_path)
        if not path_obj.exists():
            return None
        try:
            with open(path_obj, 'rb') as f:
                screenshot_data = f.read()
            return base64.b64encode(screenshot_data).decode('utf-8')
        except Exception:
            return None

    def to_dict(self) -> dict[str, Any]:
        data = {}
        data['tabs'] = [tab.model_dump() for tab in self.tabs]
        data['screenshot_path'] = self.screenshot_path
        data['interacted_element'] = [el.to_dict() if el else None for el in self.interacted_element]
        data['url'] = self.url
        data['title'] = self.title
        return data

class BrowserError(Exception):
    message: str
    short_term_memory: str | None = None
    long_term_memory: str | None = None
    details: dict[str, Any] | None = None
    while_handling_event: BaseEvent[Any] | None = None

    def __init__(self, message: str, short_term_memory: str | None=None, long_term_memory: str | None=None, details: dict[str, Any] | None=None, event: BaseEvent[Any] | None=None):
        self.message = message
        self.short_term_memory = short_term_memory
        self.long_term_memory = long_term_memory
        self.details = details
        self.while_handling_event = event
        super().__init__(message)

    def __str__(self) -> str:
        if self.details:
            return f'{self.message} ({self.details}) during: {self.while_handling_event}'
        elif self.while_handling_event:
            return f'{self.message} (while handling: {self.while_handling_event})'
        else:
            return self.message

class URLNotAllowedError(BrowserError):
    pass