"""Run async tool factories concurrently and return results in input order."""
import asyncio


async def run_group(factories):
    tasks = []
    group = None
    try:
        for factory in factories:
            tasks.append(asyncio.create_task(factory()))
        group = asyncio.gather(*tasks)
        # Own cancellation here so gather cannot interrupt child cleanup.
        return await asyncio.shield(group)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        # Retrieve the original gather's exception as well as every task's.
        waiters = tasks if group is None else [*tasks, group]
        cleanup = asyncio.gather(*waiters, return_exceptions=True)
        while True:
            try:
                await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                # Finish cleanup even if the parent is cancelled during it.
                continue
        raise
