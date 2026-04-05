import asyncio
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from base import run_test

if __name__ == "__main__":
    task = "Go to https://www.amazon.com. Search for 'wireless mouse', apply the '4 Stars & Up' customer review filter on the left sidebar, and extract the title and price of the first non-sponsored result."
    asyncio.run(run_test(task))
