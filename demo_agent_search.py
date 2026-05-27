import asyncio
import logging
import os
from dotenv import load_dotenv

# Load API keys
load_dotenv()

from system.agent.service import Agent
from system.browser.session import BrowserSession
from system.browser.profile import BrowserProfile

try:
    from system.llm import ChatGoogle
except ImportError:
    print("Error: Could not import ChatGoogle from system.llm")
    exit(1)

logging.basicConfig(level=logging.INFO)

async def main():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not found in .env. Please set it to run the Agent.")
        return

    print("Initializing Browser Profile...")
    profile = BrowserProfile(headless=False)
    session = BrowserSession(browser_profile=profile)
    
    llm = ChatGoogle(model="gemini-2.5-flash", api_key=api_key)

    task_instruction = (
        "Go to Google.com, search for 'artificial intelligence', wait for the results to load so you can assure me they loaded, "
        "and then complete the task."
    )

    agent = Agent(
        task=task_instruction,
        llm=llm,
        browser_session=session
    )

    try:
        print("Starting the autonomous agent to search Google...")
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
