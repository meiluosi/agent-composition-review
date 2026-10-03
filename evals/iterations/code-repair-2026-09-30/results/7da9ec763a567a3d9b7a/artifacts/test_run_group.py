"""Behavioral regression tests; select app or the read-only original module."""
import asyncio
import importlib
import sys
import unittest


MODULE = sys.argv.pop(1) if len(sys.argv) > 1 else "app"
run_group = importlib.import_module(MODULE).run_group


class GroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.background = asyncio.create_task(asyncio.Event().wait())
        self.owned = []

    async def asyncTearDown(self):
        isolated = not self.background.done() and not self.background.cancelling()
        self.background.cancel()
        await asyncio.gather(self.background, return_exceptions=True)
        pending = [t for t in self.owned if not t.done()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.assertTrue(isolated, "unrelated background task was cancelled")
        self.assertFalse(pending, "run_group left owned tasks running")

    async def test_empty(self):
        self.assertEqual(await run_group([]), [])

    async def test_parallel_and_input_order(self):
        ready = [asyncio.Event() for _ in range(3)]
        finished = []
        values = [object(), None, ValueError("ordinary return value")]

        async def worker(i):
            self.owned.append(asyncio.current_task())
            ready[i].set()
            await asyncio.gather(*(event.wait() for event in ready))
            if i != 2:
                while 2 not in finished:
                    await asyncio.sleep(0)
            finished.append(i)
            return values[i]

        result = await asyncio.wait_for(
            run_group([lambda i=i: worker(i) for i in range(3)]), 1
        )
        self.assertEqual(finished[0], 2)
        self.assertTrue(all(a is b for a, b in zip(result, values)))
        self.assertEqual(len(result), 3)

    async def test_sync_factory_failure(self):
        for error in (ValueError("factory"), asyncio.CancelledError("factory")):
            called = []
            created = []

            async def worker():
                self.owned.append(asyncio.current_task())
                await asyncio.sleep(0)

            def make():
                coro = worker()
                created.append(coro)
                return coro

            def fail():
                called.append("fail")
                raise error

            def forbidden():
                called.append("later")
                return worker()

            with self.assertRaises(type(error)) as caught:
                await run_group([make, fail, forbidden])
            self.assertIs(caught.exception, error)
            self.assertEqual(called, ["fail"])
            self.assertIsNone(created[0].cr_frame)

    async def failure_case(self, error, cleanup_error=None, cancel_parent=False):
        started = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        cleaned = []

        async def worker():
            self.owned.append(asyncio.current_task())
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.append(True)
                if cleanup_error is not None:
                    raise cleanup_error

        async def fail():
            self.owned.append(asyncio.current_task())
            await started.wait()
            raise error

        parent = asyncio.create_task(run_group([worker, fail]))
        await asyncio.wait_for(cleaning.wait(), 1)
        if cancel_parent:
            parent.cancel("during cleanup")
        for _ in range(5):
            await asyncio.sleep(0)
        premature = parent.done()
        release.set()
        try:
            await parent
        except BaseException as caught:
            if cancel_parent:
                self.assertIsInstance(caught, asyncio.CancelledError)
            else:
                self.assertIs(caught, error)
        else:
            self.fail("exception was returned or swallowed")
        self.assertFalse(premature, "parent exited before cleanup was released")
        self.assertEqual(cleaned, [True])

    async def test_child_exception_identity_and_cleanup(self):
        await self.failure_case(ValueError("child"))

    async def test_child_cancelled_error_identity_and_cleanup(self):
        await self.failure_case(asyncio.CancelledError("child"))

    async def test_cleanup_exception_preserves_original(self):
        await self.failure_case(ValueError("first"), RuntimeError("cleanup"))

    async def test_base_exception_identity(self):
        class StopWork(BaseException):
            pass
        await self.failure_case(StopWork("child"))

    async def test_parent_cancel_during_failure_cleanup(self):
        await self.failure_case(ValueError("first"), cancel_parent=True)

    async def test_parent_cancellation_survives_cleanup_outcomes(self):
        for cleanup_raises in (False, True):
            started = asyncio.Event()
            cleaned = []

            async def worker():
                self.owned.append(asyncio.current_task())
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await asyncio.sleep(0)
                    cleaned.append(True)
                    if cleanup_raises:
                        raise RuntimeError("cleanup failed")
                    return "cancellation suppressed by child"

            parent = asyncio.create_task(run_group([worker]))
            await started.wait()
            parent.cancel("external cancellation")
            with self.assertRaises(asyncio.CancelledError) as caught:
                await parent
            self.assertEqual(caught.exception.args, ("external cancellation",))
            self.assertEqual(cleaned, [True])

    async def test_eager_factory_failure_waits_for_started_child(self):
        loop = asyncio.get_running_loop()
        previous = loop.get_task_factory()
        cleaned = []
        error = ValueError("factory after child started")

        async def worker():
            self.owned.append(asyncio.current_task())
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.append(True)

        def fail():
            raise error

        loop.set_task_factory(asyncio.eager_task_factory)
        try:
            with self.assertRaises(ValueError) as caught:
                await run_group([worker, fail])
        finally:
            loop.set_task_factory(previous)
        self.assertIs(caught.exception, error)
        self.assertEqual(cleaned, [True])

    async def cancellation_case(self, cancel_parent):
        started = [asyncio.Event(), asyncio.Event()]
        cleaning = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()
        children = [None, None]
        cleaned = []

        async def worker(i):
            children[i] = asyncio.current_task()
            self.owned.append(children[i])
            started[i].set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning[i].set()
                if i == 1:
                    await release.wait()
                cleaned.append(i)

        parent = asyncio.create_task(run_group([lambda: worker(0), lambda: worker(1)]))
        await asyncio.gather(*(event.wait() for event in started))
        if cancel_parent:
            parent.cancel("external")
        else:
            # A child already cleaning must not receive a second cancellation
            # when the other child exposes cancellation to run_group.
            children[1].cancel("slow child")
            await cleaning[1].wait()
            children[0].cancel("fast child")
        await asyncio.gather(*(event.wait() for event in cleaning))
        for _ in range(5):
            await asyncio.sleep(0)
        premature = parent.done()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await parent
        self.assertFalse(premature, "parent exited while a child was cleaning")
        self.assertEqual(sorted(cleaned), [0, 1])

    async def test_parent_cancel_waits_for_all_cleanup(self):
        await self.cancellation_case(cancel_parent=True)

    async def test_already_cancelling_child_finishes_cleanup(self):
        await self.cancellation_case(cancel_parent=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
