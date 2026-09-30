"""Run async tool factories concurrently and return results in input order."""
import asyncio


async def run_group(factories):
    tasks=[]
    try:
        for factory in factories:
            tasks.append(asyncio.create_task(factory()))
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
