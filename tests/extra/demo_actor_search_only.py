import asyncio
import logging
from system import Browser
from system.browser.profile import BrowserProfile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    print("=== MIRA Actor Search Test (No LLM) ===")
    
    # 1. Initialize the Browser Profile and Session
    print("1. Initializing Browser Profile...")
    profile = BrowserProfile(headless=False)  # Set to True if you don't want to see the window
    browser = Browser(browser_profile=profile)
    
    try:
        print("2. Starting browser session...")
        await browser.start()
        
        # 2. Navigate to Google
        print("3. Navigating to Google.com...")
        page = await browser.new_page("https://www.google.com")
        
        print("   Waiting 2 seconds for page load...")
        await asyncio.sleep(2)
        
        # 3. Find the Search Input Box
        print("4. Locating the search bar...")
        # We can use multiple CSS selectors to ensure we catch the search bar geometry
        search_bars = await page.get_elements_by_css_selector('textarea[name="q"], input[name="q"], input[type="text"]')
        
        if not search_bars:
            print("ERROR: Could not find the Google search bar on the page.")
            return
            
        search_bar = search_bars[0]
        print(f"   Found search element! (Node ID: {await search_bar.get_attribute('name')})")
        
        # 4. Fill the input and press Enter
        print("5. Filling in search term: 'artificial intelligence'")
        await search_bar.fill("artificial intelligence")
        
        print("   Pressing Enter...")
        await page.press("Enter")
        
        # Wait a moment to visually confirm results loaded
        print("6. Waiting for results to manifest...")
        await asyncio.sleep(4)
        print("   Test Complete!")
        
    except Exception as e:
        logger.error(f"Test failed with exception: {e}")
    finally:
        # 5. Clean up gracefully
        print("7. Closing browser session...")
        await browser.kill()
        print("=== Test Finished ===")

if __name__ == "__main__":
    asyncio.run(main())
