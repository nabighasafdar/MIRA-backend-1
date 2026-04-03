import asyncio
import pytest
from system.browser.profile import BrowserProfile
from system.browser.session import BrowserSession
from system.browser.events import NavigateToUrlEvent

@pytest.mark.asyncio
async def test_browser_launch_and_navigate():
    # Initialize a basic browser session (headless for testing)
    profile = BrowserProfile(headless=True, disable_security=True, user_data_dir='/tmp/mock_user_data_dir_test')
    session = BrowserSession(browser_profile=profile)
    
    try:
        await session.start()
        
        # Navigate using the event system
        # Dispatch returns an Event object (or coroutine), we can await it if needed
        event = NavigateToUrlEvent(url="https://example.com")
        await session.event_bus.dispatch(event)
        await asyncio.sleep(2) # Give it an extra second to settle
        
        # Request state via the session helper
        state = await session.get_browser_state_summary(include_screenshot=False)
        
        assert "example.com" in state.url
        assert len(state.tabs) > 0
        
    finally:
        # Cleanly stop the browser
        await session.stop()
