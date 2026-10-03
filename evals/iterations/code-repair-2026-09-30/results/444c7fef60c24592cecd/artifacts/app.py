"""Run async tool factories concurrently and return results in input order."""
import asyncio


async def run_group(factories):
    tasks = []
    group = None
    try:
        for factory in factories:
            tasks.append(asyncio.create_task(factory()))
        # Own cancellation here so gather cannot cancel children a second time.
        group = asyncio.gather(*tasks)
        return await asyncio.shield(group)
    except BaseException:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        # A cancellation arriving during cleanup must not interrupt that cleanup.
        cleanup = asyncio.gather(*tasks, return_exceptions=True)
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                pass
        if group is not None:
            group.exception()
        # Preserve the exception (including cancellation) that initiated cleanup.
        raise
