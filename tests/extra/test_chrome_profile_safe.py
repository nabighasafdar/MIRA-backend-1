import asyncio
import logging
import os
from dotenv import load_dotenv

# Load API keys
load_dotenv()

from system.agent.service import Agent
from system.browser.session import BrowserSession
from system.browser.profile import BrowserProfile
from system.llm import ChatGoogle

logging.basicConfig(level=logging.INFO)

async def main():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not found in .env. Please set it to run the Agent.")
        return

    # Point directly to the user's default Chrome profile path on Windows
    chrome_profile_path = r"C:\Users\maham\AppData\Local\Google\Chrome\User Data"
    
    print(f"Initializing Browser Profile using: {chrome_profile_path}")
    print("NOTE: MIRA will safely copy this to a /tmp/mira-user-data-dir-XXXX folder.")
    print("You will have all your saved logins, but anything you do here will NOT affect your real Chrome profile!")

    # Standard initialization: _copy_profile will automatically trigger, creating a disposable clone
    profile = BrowserProfile(
        headless=False,
        user_data_dir=chrome_profile_path
    )
    
    session = BrowserSession(browser_profile=profile)
    llm = ChatGoogle(model="gemini-2.5-flash", api_key=api_key)

    task_instruction = (
        "Go to youtube.com. Read the page to confirm you are logged in, and then click on the very first recommended video "
        "on the home page to start playing it."
    )

    agent = Agent(
        task=task_instruction,
        llm=llm,
        browser_session=session
    )

    try:
        print("\nStarting the autonomous agent with the SAFE cloned profile...")
        await session.start()
        
        result = await agent.run()
        print("\n=== Agent Run Completed ===")
        if result.is_successful():
            print("Final Result:", result.final_result())
        else:
            print("Agent did not complete the task successfully.")

        print("\n🟢 Agent finished! Leaving the browser open so you can continue browsing...")
        print("Everything you do here remains in the disposable clone. Have fun!")
        print("Press Ctrl+C in this terminal when you are ready to shut down and delete the clone.")
        
        # Keep the session alive infinitely so the user can continue using the browser manually
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nExiting gracefully as requested...")
        
    finally:
        print("Cleaning up and destroying the temporary cloned browser session...")
        await session.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass  # Suppress giant traceback dump when user presses Ctrl+C
