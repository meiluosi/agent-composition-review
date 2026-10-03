"""Run async tool factories concurrently and return results in input order."""
import asyncio


async def run_group(factories):
    tasks = []
    group = None
    try:
        for factory in factories:
            tasks.append(asyncio.create_task(factory()))
        # Keep parent cancellation from cancelling children behind our back.
        group = asyncio.gather(*tasks)
        return await asyncio.shield(group)
    except BaseException:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        # Drain only this invocation's children, including asynchronous cleanup.
        cleanup = asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # An external cancellation can arrive while handling a child error.
            # Finish cleanup before propagating that cancellation unchanged.
            await cleanup
            raise
        finally:
            # A cancelled shield can leave the original gather unobserved.
            if group is not None and group.done() and not group.cancelled():
                group.exception()
        raise
