import pytest
import asyncio
import os
from dotenv import load_dotenv

from system.agent.service import Agent
from system.browser.session import BrowserSession
from system.browser.profile import BrowserProfile
from system.llm import ChatGoogle

import logging

# Load environment variables
load_dotenv()

# Set up logging so the user can see the agent's thoughts in the console
logging.basicConfig(level=logging.INFO)

@pytest.mark.asyncio
async def test_agent_integration():
    """Verify that the MIRA Agent can successfully initialize and execute a command."""
    
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY not found in environment. Skipping Agent integration test.")

    print("\nStarting Agent Integration Test...")
    
    # Initialize the specific LLM component from MIRA's abstraction layer
    llm = ChatGoogle(model="gemini-2.5-flash", api_key=api_key)
    
    # Initialize an isolated browser session (headless=False so you can see it work)
    profile = BrowserProfile(headless=False, disable_security=True, user_data_dir='/tmp/mock_agent_user_data')
    session = BrowserSession(browser_profile=profile)
    
    # Initialize the Agent
    agent = Agent(
        task="Go to Wikipedia, search for 'Artificial Intelligence', find the first paragraph, and summarize it. Start your summary with 'Success: '.",
        llm=llm,
        browser_session=session
    )
    
    try:
        await session.start()
        
        # Run the agent execution loop
        history = await agent.run()
        
        # Verify the history and final state
        assert history.is_successful(), "The agent did not successfully finish the task."
        
        final_result = history.final_result()
        assert final_result is not None, "Final result should not be None."
        assert "Success" in final_result, f"Agent failed to find the target. Final result was: {final_result}"
        
        # Format a summary for the user
        print("\n\n" + "="*50)
        print("AGENT EXECUTION SUMMARY")
        print("="*50)
        print(f"Total Steps Taken: {len(history.history)}")
        for i, step in enumerate(history.history):
            if set_action := step.model_output:
                print(f"Step {i+1}: Action -> {list(set_action.action[0].model_dump(exclude_unset=True).keys())[0]}")
                if set_action.current_state.thinking:
                    print(f"   Thought: {set_action.current_state.thinking}")
        
        print("-" * 50)
        print(f"✅ Final Result: {final_result}")
        print("=" * 50 + "\n")
    except Exception as e:
        pytest.fail(f"Agent run failed throwing an exception: {str(e)}")
    finally:
        await session.stop()
