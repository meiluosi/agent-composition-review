"""Run async tool factories concurrently and return results in input order."""
import asyncio


async def run_group(factories):
    tasks = []
    group = None
    try:
        for factory in factories:
            tasks.append(asyncio.create_task(factory()))
        group = asyncio.gather(*tasks)
        # Handle parent cancellation here, without gather cancelling children
        # before we take ownership of their cleanup.
        return await asyncio.shield(group)
    except BaseException:
        for task in tasks:
            # A child may already be handling its own cancellation.
            if not task.done() and not task.cancelling():
                task.cancel()
        # Also retrieve the group's exception if the shield was cancelled.
        pending = tasks + ([group] if group is not None else [])
        cleanup = asyncio.gather(*pending, return_exceptions=True)
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # Cancellation during cleanup must not interrupt children or
                # replace the exception that initiated this exit.
                continue
        raise
