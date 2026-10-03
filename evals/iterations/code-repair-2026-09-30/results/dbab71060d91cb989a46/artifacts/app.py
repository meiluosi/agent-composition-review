"""Run async tool factories concurrently and return results in input order."""
import asyncio


async def run_group(factories):
    tasks = []
    group = None
    try:
        for factory in factories:
            tasks.append(asyncio.create_task(factory()))
        group = asyncio.gather(*tasks)
        # Keep parent cancellation from also cancelling children via gather.
        return await asyncio.shield(group)
    finally:
        for task in tasks:
            # A child already handling cancellation must finish its cleanup.
            if not task.done() and not task.cancelling():
                task.cancel()
        # Retrieve the group exception too, including after parent cancellation.
        owned = tasks if group is None else [group, *tasks]
        cleanup = asyncio.gather(*owned, return_exceptions=True)
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # The one external cancellation may arrive during failure cleanup.
            await cleanup
            raise
