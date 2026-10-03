"""Run async tool factories concurrently and return results in input order."""
import asyncio


async def run_group(factories):
    tasks = []
    group = None
    try:
        for factory in factories:
            tasks.append(asyncio.create_task(factory()))
        group = asyncio.gather(*tasks)
        # Own cancellation here, so gather cannot interrupt child cleanup.
        return await asyncio.shield(group)
    finally:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        drain = asyncio.gather(*tasks, return_exceptions=True)
        try:
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError:
                # One external cancellation may arrive during failure cleanup.
                # Finish that cleanup before propagating the same cancellation.
                await asyncio.shield(drain)
                raise
        finally:
            # A cancelled shield leaves group alive; retrieve its exception too.
            if group is not None and group.done() and not group.cancelled():
                group.exception()
