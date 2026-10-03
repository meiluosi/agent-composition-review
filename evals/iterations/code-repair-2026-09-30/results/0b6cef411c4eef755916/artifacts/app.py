"""Run async tool factories concurrently and return results in input order."""
import asyncio


async def run_group(factories):
    tasks = []
    group = None
    try:
        for factory in factories:
            tasks.append(asyncio.create_task(factory()))
        # Own cancellation here: gather must not cancel children before our
        # cleanup block and cause a second cancellation during their finally.
        group = asyncio.gather(*tasks)
        return await asyncio.shield(group)
    finally:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        # A child already cancelling may be awaiting its own async cleanup.
        # Parent cancellation during this drain must not interrupt that work.
        # Retrieve the original gather's outcome too if its shield was cancelled.
        waiters = tasks if group is None else [*tasks, group]
        cleanup = asyncio.gather(*waiters, return_exceptions=True)
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # The contract permits one external cancellation. Finish draining
            # before propagating this same cancellation to our caller.
            await asyncio.shield(cleanup)
            raise
