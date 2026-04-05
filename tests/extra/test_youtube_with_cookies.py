import asyncio
import logging
import os
from dotenv import load_dotenv

from system.agent.service import Agent
from system.browser.session import BrowserSession
from system.browser.profile import BrowserProfile
from system.llm import ChatGoogle

load_dotenv()
logging.basicConfig(level=logging.INFO)

async def main():
    api_key = os.getenv("GOOGLE_API_KEY")
    cookies_file = "mira_google_cookies.json"

    if not os.path.exists(cookies_file):
        print(f"ERROR: Could not find {cookies_file}!")
        print("Please run `python mira_login_setup.py` first to generate your automated cookies.")
        return

    print(f"Injecting cookies from {cookies_file} directly into a fresh browser...")
    
    profile = BrowserProfile(
        headless=False,
        storage_state=cookies_file
    )
    
    session = BrowserSession(browser_profile=profile)
    llm = ChatGoogle(model="gemini-2.5-flash", api_key=api_key)

    task_instruction = (
        "go to gmail and read the top mail"
    )

    agent = Agent(
        task=task_instruction,
        llm=llm,
        browser_session=session
    )

    try:
        print("\nStarting the autonomous agent with your injected Google cookies...")
        await session.start()
        
        result = await agent.run()
        print("\n=== Agent Run Completed ===")
        if result.is_successful():
            print("Final Result:", result.final_result())
        else:
            print("Agent did not complete the task successfully.")

        print("\n🟢 Agent finished! Leaving the browser open so you can watch the video...")
        print("Press Ctrl+C in this terminal when you are ready to shut down.")
        
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nExiting gracefully as requested...")
        
    finally:
        print("Cleaning up browser session...")
        await session.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
