import asyncio
import os
import logging
from dotenv import load_dotenv

from system.agent.service import Agent
from system.browser.session import BrowserSession
from system.browser.profile import BrowserProfile
from system.llm import ChatGoogle


def get_llm():
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY not found in environment")
    return ChatGoogle(model="gemini-2.5-flash", api_key=api_key)

async def run_test(task_description: str, **agent_kwargs):
    print(f"\n[TEST SUITE executing task]")
    print(f"Task: {task_description}")
    print("=" * 50)
    
    llm = get_llm()
    profile = BrowserProfile(headless=False, disable_security=True, user_data_dir='/tmp/mira_robustness_test')
    session = BrowserSession(browser_profile=profile)
    
    agent = Agent(task=task_description, llm=llm, browser_session=session, **agent_kwargs)
    try:
        await session.start()
        history = await agent.run()
        
        final_result = history.final_result()
        print("\n\n" + "="*50)
        print("AGENT EXECUTION SUMMARY")
        print("="*50)
        print(f"Total Steps Taken: {len(history.history)}")
        if history.usage:
            print(f"Total Tokens Used: {history.usage.total_tokens} (Prompt: {history.usage.total_prompt_tokens}, Completion: {history.usage.total_completion_tokens})")
        for i, step in enumerate(history.history):
            if set_action := step.model_output:
                print(f"Step {i+1}: Action -> {list(set_action.action[0].model_dump(exclude_unset=True).keys())[0]}")
                if set_action.current_state.thinking:
                    print(f"   Thought: {set_action.current_state.thinking}")
        print("-" * 50)
        print(f"✅ Final Result: {final_result}")
        print("=" * 50 + "\n")
    except Exception as e:
        print(f"❌ Test threw an exception: {e}")
    finally:
        await session.stop()
