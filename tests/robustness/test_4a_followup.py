import asyncio
from system.agent.service import Agent
from system.browser.session import BrowserSession
from system.browser.profile import BrowserProfile
from base import get_llm
from system.logging_config import setup_logging

async def run_interactive_chat():
    """
    Tests Multi-turn Follow-ups interactively:
    Reuses the same BrowserSession and injects the previous AgentState 
    so the agent "remembers" what it was doing and can act on the current page 
    based on dynamic user input.
    """
    setup_logging()
    llm = get_llm()
    
    print("\n[TEST SUITE executing interactive chat session]")
    print("Type 'exit' or 'quit' to end the session.")
    print("=" * 50)

    # We use a single persistent session for all agent runs
    # keep_alive=True ensures the browser doesn't auto-close when an agent run finishes
    profile = BrowserProfile(headless=False, disable_security=True, user_data_dir='/tmp/mira_followup_test', keep_alive=True)
    session = BrowserSession(browser_profile=profile)
    
    previous_state = None
    turn_counter = 1
    
    try:
        await session.start()
        
        while True:
            # 1. Get user input (using run_in_executor to not block the async loop)
            task = await asyncio.get_event_loop().run_in_executor(None, input, "\n🤖 You: ")
            
            if task.lower().strip() in ['exit', 'quit']:
                print("\nEnding session...")
                break
                
            if not task.strip():
                continue
                
            print(f"\n[TURN {turn_counter}] Executing task...")
            
            # 2. Configure Agent state
            if previous_state:
                # Mark it as a follow-up task so it prefers the current page context
                previous_state.follow_up_task = True
                
            # 3. Create Agent with shared session and previous state
            agent = Agent(
                task=task,
                llm=llm,
                browser_session=session,
                injected_agent_state=previous_state
            )
            
            # 4. Run Agent
            history = await agent.run()
            
            print("\n" + "-" * 50)
            print(f"✅ Result: {history.final_result()}")
            print("-" * 50)
            
            # 5. Save state for next turn
            previous_state = agent.state
            turn_counter += 1
            
    except Exception as e:
        print(f"\n❌ Encountered an error: {e}")
    finally:
        # We must explicitly clean up the session
        print("\nCleaning up browser session...")
        try:
            await session.kill()
        except:
            pass

if __name__ == "__main__":
    asyncio.run(run_interactive_chat())
