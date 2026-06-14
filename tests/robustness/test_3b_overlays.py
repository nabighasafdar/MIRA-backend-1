import asyncio
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from base import run_test

if __name__ == "__main__":
    task = "Go to https://www.forbes.com/. You will likely encounter an overlay, intrusive ad, or cookie banner. Find a way to close or dismiss it, then extract the title of the top headline article visible."
    asyncio.run(run_test(task))
