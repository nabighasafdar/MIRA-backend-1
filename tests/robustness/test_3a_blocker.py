import asyncio
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from base import run_test

if __name__ == "__main__":
    task = "Go to https://nowsecure.nl/, which is protected by Cloudflare. Try to read the page content. If you are blocked by a CAPTCHA or 'Verify you are human' check, output 'Failed: CAPTCHA block detected'. Do not hallucinate content if the real page hasn't loaded."
    asyncio.run(run_test(task))
