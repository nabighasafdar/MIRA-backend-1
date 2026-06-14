import asyncio
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from base import run_test

if __name__ == "__main__":
    task = "Go to https://www.calculator.net/. Using the on-screen buttons, calculate (45 * 12) + 300, and extract the final result displayed on the screen."
    asyncio.run(run_test(task))
