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
    # 🔥 DANGER ZONE: Monkeypatch BrowserProfile to disable the safety firewall
    # This prevents MIRA from copying your profile into a temporary folder. 
    # MIRA will now operate directly out of your actual, physical Google Chrome profile folder!
    # Any cookies it accepts, sites it visits, or logins it performs will be permanently saved to your real Chrome browser.
    
    BrowserProfile._copy_profile = lambda self: None
    
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
        print("\nStarting the autonomous agent to verify login status...")
        await session.start()
        
        result = await agent.run()
        print("\n=== Agent Run Completed ===")
        if result.is_successful():
            print("Final Result:", result.final_result())
        else:
            print("Agent did not complete the task successfully.")
        
    finally:
        print("Closing the browser session...")
        await session.stop()

if __name__ == "__main__":
    asyncio.run(main())
