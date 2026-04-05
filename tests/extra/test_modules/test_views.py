import pytest
from pydantic import ValidationError
from system.browser.views import (
    TabInfo,
    BrowserError,
    BrowserStateHistory,
    NetworkRequest,
    PaginationButton
)

def test_tab_info_serialization():
    # Test that the target_id and parent_target_id serialize correctly (returns last 4 chars)
    tab = TabInfo(
        url="https://example.com",
        title="Example",
        target_id="1234567890abcdef",
        parent_target_id="abcdef1234567890"
    )
    
    dumped = tab.model_dump(by_alias=True)
    assert dumped["tab_id"] == "cdef"
    assert dumped["parent_tab_id"] == "7890"

def test_tab_info_validation():
    # Test strictness of fields forbidding extra
    with pytest.raises(ValidationError):
        TabInfo(
            url="https://example.com",
            title="Example",
            tab_id="123",
            extra_field="not allowed"
        )

def test_browser_error_formatting():
    # Test the string representation
    err = BrowserError("Something went wrong", details={"code": 500})
    assert "Something went wrong" in str(err)
    assert "500" in str(err)

def test_browser_state_history_to_dict():
    # Test the to_dict method
    history = BrowserStateHistory(
        url="https://example.com",
        title="Test Title",
        tabs=[
            TabInfo(url="https://example.com/1", title="Tab 1", target_id="1111"),
            TabInfo(url="https://example.com/2", title="Tab 2", target_id="2222")
        ],
        interacted_element=[None],
        screenshot_path="/tmp/fake_path.png"
    )
    
    data = history.to_dict()
    assert data["url"] == "https://example.com"
    assert data["title"] == "Test Title"
    assert len(data["tabs"]) == 2
    assert data["screenshot_path"] == "/tmp/fake_path.png"
    assert len(data["interacted_element"]) == 1
    assert data["interacted_element"][0] is None
