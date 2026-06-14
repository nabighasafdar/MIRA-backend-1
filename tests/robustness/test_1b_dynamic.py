import asyncio
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from base import run_test

if __name__ == "__main__":
    task = "Go to https://news.ycombinator.com/. Scroll to the bottom and click the 'More' link to load the second page. Then, extract the title of the very last post on that second page."
    asyncio.run(run_test(task))
