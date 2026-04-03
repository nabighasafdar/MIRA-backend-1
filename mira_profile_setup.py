import asyncio
import logging
import os
from dotenv import load_dotenv

from system.browser.session import BrowserSession
from system.browser.profile import BrowserProfile

load_dotenv()
logging.basicConfig(level=logging.INFO)

async def main():
    print("=========================================================")
    print("            MIRA: Permanent Profile Setup Tool           ")
    print("=========================================================")
    print("1. A completely fresh Google Chrome window will open.")
    print("2. You are now inside MIRA's dedicated, permanent Chrome profile.")
    print("3. Log into your Google Chrome Profile (the top-right bubble)")
    print("   or log into YouTube/Gmail directly.")
    print("4. When you are done, close the browser or press Ctrl+C.")
    print("=========================================================\n")

    # Define the permanent, dedicated folder inside your MIRA codebase
    dedicated_profile_path = os.path.join(os.getcwd(), "mira_dedicated_profile")
    print(f"Permanent Profile Location: {dedicated_profile_path}")

    # 🔥 DANGER ZONE: Disable MIRA's disposable /tmp/ safety clone!
    # Because we WANT all logins to permanently save to this dedicated folder.
    BrowserProfile._copy_profile = lambda self: None

    profile = BrowserProfile(
        headless=False,
        user_data_dir=dedicated_profile_path
    )
    
    session = BrowserSession(browser_profile=profile)

    try:
        await session.start()
        
        # Keep the session alive infinitely so you have ample time to log in
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\n\nFinished setup!")
        
    finally:
        print("Cleaning up browser...")
        await session.stop()
        print("\nAll done! MIRA's permanent profile is now fully logged in.")
        print("You can now run test_mira_agent.py to autonomously browse with your accounts!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
