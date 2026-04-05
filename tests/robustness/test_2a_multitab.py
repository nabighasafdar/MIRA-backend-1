import asyncio
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from base import run_test

if __name__ == "__main__":
    task = "Open a tab to https://en.wikipedia.org/wiki/Cat. Then open a new tab to https://en.wikipedia.org/wiki/Dog. Switch back and forth if necessary to compare their average lifespans, and output the difference in years."
    asyncio.run(run_test(task))
