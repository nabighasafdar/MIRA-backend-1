import asyncio
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from base import run_test

if __name__ == "__main__":
    task = "Go to https://en.wikipedia.org/wiki/Python_(programming_language) and extract the summary paragraph describing the language."
    asyncio.run(run_test(task))
