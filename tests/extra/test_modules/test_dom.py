import pytest
import asyncio
from system.browser.profile import BrowserProfile
from system.browser.session import BrowserSession
from system.browser.events import NavigateToUrlEvent

@pytest.mark.asyncio
async def test_dom_interaction_and_extraction():
    """Verify the DOM module correctly parsing elements and returning standard formatting."""
    profile = BrowserProfile(headless=True, disable_security=True, user_data_dir='/tmp/mock_user_data_dir_test_dom')
    session = BrowserSession(browser_profile=profile)
    
    try:
        await session.start()
        
        # Navigate to a very simple website with guaranteed structure
        event = NavigateToUrlEvent(url="https://example.com")
        await session.event_bus.dispatch(event)
        await asyncio.sleep(2)  # Wait for page to settle fully
        
        # Request full state, demanding DOM inclusion
        state = await session.get_browser_state_summary(include_screenshot=False)
        
        # 1. Assert DOM State Object Exists
        assert state.dom_state is not None, "DOM state was completely empty"
        
        # 2. Extract string representation of the parsed DOM
        dom_markdown = state.dom_state.llm_representation()
        
        # 3. Assert HTML structure exists in the parsed output
        # Example Domain has an <h1> tag with "Example Domain" and an <a> tag linking to iana.org
        assert "Example Domain" in dom_markdown, "Could not find the main header text in DOM extraction"
        assert "Learn more" in dom_markdown, "Could not find the hyperlink text in DOM extraction"
        
        # 4. Assert Interactive Elements are Mapped
        # Is there at least one actionable element (the <a> link)?
        assert len(state.dom_state.selector_map) > 0, "No interactive elements were mapped by the dom_watchdog"
        
        # Check if any mapped element has an accessible name mentioning "Learn more"
        found_link = False
        for node_id, element_node in state.dom_state.selector_map.items():
            if "Learn more" in element_node.get_meaningful_text_for_llm():
                found_link = True
                break
        
        assert found_link, "The href link to IANA was parsed, but not correctly stored in the interactive selector_map"
        
    finally:
        await session.stop()
