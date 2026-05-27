import asyncio
from base import run_test

if __name__ == "__main__":
    task = "Go to https://www.w3schools.com/tags/tryit.asp?filename=tryhtml_iframe. There is an iframe on the right side of the screen displaying another page. Extract the main heading (H1) text from inside that iframe."
    asyncio.run(run_test(task))
