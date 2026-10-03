import asyncio
import importlib
import sys
import unittest


run_group = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else "app").run_group


class RunGroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty(self):
        self.assertEqual(await run_group([]), [])

    async def test_parallel_and_ordered(self):
        entered = [asyncio.Event() for _ in range(3)]
        release = [asyncio.Event() for _ in range(3)]
        finished = [asyncio.Event() for _ in range(3)]

        async def work(i):
            entered[i].set()
            await release[i].wait()
            finished[i].set()
            return i * 2

        parent = asyncio.create_task(run_group([lambda i=i: work(i) for i in range(3)]))
        for event in entered:
            await asyncio.wait_for(event.wait(), 1)
        for i in reversed(range(3)):
            release[i].set()
            await asyncio.wait_for(finished[i].wait(), 1)
        self.assertEqual(await parent, [0, 2, 4])

    async def test_factory_failure_identity_and_owned_tasks(self):
        error = ValueError("factory failure")
        children = []

        async def work():
            await asyncio.Event().wait()

        def first():
            return work()

        def fail():
            children.extend(task for task in asyncio.all_tasks()
                            if task is not asyncio.current_task())
            raise error

        with self.assertRaises(ValueError) as caught:
            await run_group([first, fail])
        self.assertIs(caught.exception, error)
        self.assertTrue(children)
        self.assertTrue(all(task.done() for task in children))

    async def test_failure_waits_for_cleanup_and_preserves_identity(self):
        entered = asyncio.Event()
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        cleaned = asyncio.Event()
        error = RuntimeError("original child failure")

        async def worker():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await release_cleanup.wait()
                cleaned.set()

        async def fail():
            await entered.wait()
            raise error

        background = asyncio.create_task(asyncio.Event().wait())
        parent = asyncio.create_task(run_group([worker, fail]))
        try:
            await asyncio.wait_for(cleanup_started.wait(), 1)
            self.assertFalse(parent.done())
            release_cleanup.set()
            with self.assertRaises(RuntimeError) as caught:
                await parent
            self.assertIs(caught.exception, error)
            self.assertTrue(cleaned.is_set())
            self.assertFalse(background.done())
            self.assertEqual(background.cancelling(), 0)
        finally:
            release_cleanup.set()
            background.cancel()
            await asyncio.gather(background, return_exceptions=True)

    async def test_external_cancel_does_not_interrupt_cleanup(self):
        entered = [asyncio.Event(), asyncio.Event()]
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        cleaned = asyncio.Event()
        interrupted = asyncio.Event()
        children = []

        async def fast():
            children.append(asyncio.current_task())
            entered[0].set()
            await asyncio.Event().wait()

        async def slow():
            children.append(asyncio.current_task())
            entered[1].set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                try:
                    await release_cleanup.wait()
                    cleaned.set()
                except asyncio.CancelledError:
                    interrupted.set()
                    raise

        parent = asyncio.create_task(run_group([fast, slow]))
        for event in entered:
            await asyncio.wait_for(event.wait(), 1)
        parent.cancel("parent cancellation")
        await asyncio.wait_for(cleanup_started.wait(), 1)
        # Let the completed fast child propagate cancellation to the parent.
        for _ in range(10):
            await asyncio.sleep(0)
        still_waiting = not parent.done()
        release_cleanup.set()
        with self.assertRaises(asyncio.CancelledError) as caught:
            await parent
        self.assertEqual(caught.exception.args, ("parent cancellation",))
        self.assertFalse(interrupted.is_set(), "cleanup received a second cancellation")
        self.assertTrue(still_waiting, "parent returned before cleanup was released")
        self.assertTrue(cleaned.is_set())
        self.assertTrue(all(task.done() for task in children))

    async def test_child_cancellation_propagates_and_drains(self):
        entered = asyncio.Event()
        cleaned = asyncio.Event()
        error = asyncio.CancelledError("child cancellation")

        async def worker():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        async def cancel():
            await entered.wait()
            raise error

        with self.assertRaises(asyncio.CancelledError) as caught:
            await run_group([worker, cancel])
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set())

    async def test_cleanup_failure_does_not_replace_original(self):
        entered = asyncio.Event()
        error = LookupError("primary failure")

        async def worker():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                raise ValueError("cleanup failure")

        async def fail():
            await entered.wait()
            raise error

        with self.assertRaises(LookupError) as caught:
            await run_group([worker, fail])
        self.assertIs(caught.exception, error)

    async def test_factory_cancelled_error_identity(self):
        error = asyncio.CancelledError("factory cancellation")

        def factory():
            raise error

        with self.assertRaises(asyncio.CancelledError) as caught:
            await run_group([factory])
        self.assertIs(caught.exception, error)

    async def test_external_cancel_during_failure_cleanup(self):
        entered = asyncio.Event()
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        cleaned = asyncio.Event()
        interrupted = asyncio.Event()

        async def worker():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                try:
                    await release_cleanup.wait()
                    cleaned.set()
                except asyncio.CancelledError:
                    interrupted.set()
                    raise

        async def fail():
            await entered.wait()
            raise RuntimeError("start failure cleanup")

        parent = asyncio.create_task(run_group([worker, fail]))
        await asyncio.wait_for(cleanup_started.wait(), 1)
        parent.cancel("cancel during cleanup")
        for _ in range(10):
            await asyncio.sleep(0)
        still_waiting = not parent.done()
        release_cleanup.set()
        with self.assertRaises(asyncio.CancelledError) as caught:
            await parent
        self.assertEqual(caught.exception.args, ("cancel during cleanup",))
        self.assertFalse(interrupted.is_set())
        self.assertTrue(still_waiting)
        self.assertTrue(cleaned.is_set())

    async def test_already_cancelling_child_is_not_cancelled_again(self):
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        cleaned = asyncio.Event()
        error = RuntimeError("other child failed")

        async def self_cancel():
            asyncio.current_task().cancel()
            try:
                await asyncio.sleep(0)
            finally:
                cleanup_started.set()
                await release_cleanup.wait()
                cleaned.set()

        async def fail():
            await cleanup_started.wait()
            raise error

        parent = asyncio.create_task(run_group([self_cancel, fail]))
        await asyncio.wait_for(cleanup_started.wait(), 1)
        for _ in range(10):
            await asyncio.sleep(0)
        still_waiting = not parent.done()
        release_cleanup.set()
        with self.assertRaises(RuntimeError) as caught:
            await parent
        self.assertIs(caught.exception, error)
        self.assertTrue(still_waiting)
        self.assertTrue(cleaned.is_set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
