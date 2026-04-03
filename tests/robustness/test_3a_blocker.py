import asyncio
from base import run_test

if __name__ == "__main__":
    task = "Go to https://nowsecure.nl/, which is protected by Cloudflare. Try to read the page content. If you are blocked by a CAPTCHA or 'Verify you are human' check, output 'Failed: CAPTCHA block detected'. Do not hallucinate content if the real page hasn't loaded."
    asyncio.run(run_test(task))
