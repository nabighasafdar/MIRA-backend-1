import asyncio
from base import run_test

if __name__ == "__main__":
    task = "Go to https://en.wikipedia.org/wiki/Python_(programming_language) and extract the summary paragraph describing the language."
    asyncio.run(run_test(task))
