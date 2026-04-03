import asyncio
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from system.llm import ChatGoogle, UserMessage
from system.agent.views import ActionModel
from pydantic import BaseModel, ConfigDict
from dotenv import load_dotenv

# Load environment variables from the MIRA/.env file
load_dotenv()

# We need a small mock action since we haven't built Controller yet.
class MockClickAction(ActionModel):
    model_config = ConfigDict(extra='forbid')
    click_element_id: int

class MockActionOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra='forbid')
    action: list[MockClickAction]

async def test_llm_abstraction():
    print("Testing LLM Abstraction Layer...")
    
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("⚠️ GOOGLE_API_KEY not found in environment. Skipping real API call.")
        return

    llm = ChatGoogle(model="gemini-2.5-flash", api_key=api_key)
    
    messages = [
        UserMessage(content="You are a web agent. Please click on element ID 42.")
    ]

    print("Sending prompt to Gemini expecting structured 'MockActionOutput'...")
    try:
        response = await llm.ainvoke(messages, output_format=MockActionOutput)
        print("✅ Success!")
        print(f"Output: {response.completion.action}")
    except Exception as e:
        print(f"❌ Failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_llm_abstraction())
