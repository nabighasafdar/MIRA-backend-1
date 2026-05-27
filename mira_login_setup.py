import asyncio
import logging
from dotenv import load_dotenv

# Load API keys
load_dotenv()

from system.browser.session import BrowserSession
from system.browser.profile import BrowserProfile

logging.basicConfig(level=logging.INFO)

async def main():
    print("=========================================================")
    print("                MIRA: 1-Time Setup Tool                  ")
    print("=========================================================")
    print("1. A completely fresh, blank window will open.")
    print("2. Navigate to Google/YouTube and log in with your account.")
    print("3. When you are done logging in and see your YouTube homepage, ")
    print("   come back to this terminal and press Ctrl+C.")
    print("4. MIRA will instantly take a snapshot of your login cookies ")
    print("   and save them to 'mira_google_cookies.json'!")
    print("=========================================================\n")

    # The file where we will save the cookies.
    # Note: If it doesn't exist yet, MIRA knows to start fresh and create it!
    cookies_file = "mira_google_cookies.json"

    profile = BrowserProfile(
        headless=False,
        # This parameter is the magic key for bypassing Chrome App-Bound Encryption
        storage_state=cookies_file
    )
    
    session = BrowserSession(browser_profile=profile)

    try:
        # We start the browser but purposely do NOT start the AI Agent.
        # We just want the blank browser for the human!
        await session.start()
        
        # Keep the session alive infinitely so you have ample time to log in
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\n\nGrabbing your cookies now...")
        
    finally:
        print(f"Saving your logins securely to: {cookies_file}")
        print("Cleaning up browser...")
        await session.stop()
        print("\nAll done! You never have to do this again.")
        print("MIRA can now use these cookies forever without typing passwords!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
