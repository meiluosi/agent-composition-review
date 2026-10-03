"""Run async tool factories concurrently and return results in input order."""
import asyncio


async def run_group(factories):
    return await asyncio.gather(*(factory() for factory in factories))
