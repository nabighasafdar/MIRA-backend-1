import asyncio
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from base import run_test

if __name__ == "__main__":
    task = "Go to https://example.com and click the 'Sign Up' or 'Login' button. When you realize it demonstrably does not exist on the page, output 'Failed: Element not found' instead of guessing or endless scrolling."
    asyncio.run(run_test(task))
