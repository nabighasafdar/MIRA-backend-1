import asyncio
from base import run_test

if __name__ == "__main__":
    task = "Go to https://www.google.com/travel/flights. Set the departure city to 'JFK' (New York) and the arrival city to 'LHR' (London). Set the departure date to roughly one week from today. Extract the price of the cheapest flight shown. Do NOT actually attempt to buy a ticket."
    asyncio.run(run_test(task))
