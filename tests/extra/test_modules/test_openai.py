import asyncio
import os
from dotenv import load_dotenv
from system.llm import ChatOpenAI
from system.llm.messages import UserMessage

async def main():
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not found in environment.")
        return

    print("Initializing ChatOpenAI...")
    llm = ChatOpenAI(model="gpt-4o", api_key=api_key)
    
    print("Sending test message...")
    messages = [UserMessage(content="Hello! Please reply with exactly the word 'PONG'.")]
    
    try:
        response = await llm.ainvoke(messages)
        print(f"\nResponse received: {response.content}")
        if "PONG" in response.content.upper():
            print("✅ OpenAI LLM is working correctly!")
        else:
            print("⚠️ Response received, but not the expected 'PONG'.")
    except Exception as e:
        print(f"\n❌ Error connecting to OpenAI: {e}")

if __name__ == "__main__":
    asyncio.run(main())
