from system import Agent
from system.browser import BrowserProfile, BrowserSession
from system.browser.profile import ViewportSize
from system.llm import ChatGoogle

# Initialize the Google OpenAI client
llm = ChatGoogle(model='gemini-2.5-flash')


TASK = """
Go to https://browser-use.github.io/stress-tests/challenges/react-native-web-form.html and complete the React Native Web form by filling in all required fields and submitting.
"""


async def main():
	browser = BrowserSession(
		browser_profile=BrowserProfile(
			window_size=ViewportSize(width=1100, height=1000),
		)
	)

	agent = Agent(task=TASK, llm=llm)

	await agent.run()


if __name__ == '__main__':
	import asyncio

	asyncio.run(main())
