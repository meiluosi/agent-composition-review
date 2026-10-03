import asyncio
import inspect
import unittest

from app import run_group


class GroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loop_errors = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: self.loop_errors.append(context))

    async def asyncTearDown(self):
        await asyncio.sleep(0)
        self.assertEqual(self.loop_errors, [], 'unretrieved asynchronous exception')

    async def test_empty(self):
        self.assertEqual(await run_group([]), [])

    async def test_concurrency_and_input_order(self):
        started = [asyncio.Event() for _ in range(3)]
        release = [asyncio.Event() for _ in range(3)]
        finished = [asyncio.Event() for _ in range(3)]
        values = [object(), None, ValueError('ordinary result')]

        async def work(i):
            started[i].set()
            await release[i].wait()
            finished[i].set()
            return values[i]

        parent = asyncio.create_task(run_group([lambda i=i: work(i) for i in range(3)]))
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in started)), 1)
        for i in (2, 1, 0):
            release[i].set()
            await finished[i].wait()
        result = await parent
        for actual, expected in zip(result, values):
            self.assertIs(actual, expected)

    async def test_failure_waits_for_cleanup_and_spares_background(self):
        started = asyncio.Event()
        cleaned = asyncio.Event()
        error = ValueError('child failure')
        background = asyncio.create_task(asyncio.Event().wait())

        async def worker():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                cleaned.set()

        async def fail():
            await started.wait()
            raise error

        try:
            with self.assertRaises(ValueError) as caught:
                await run_group([worker, fail])
            self.assertIs(caught.exception, error)
            self.assertTrue(cleaned.is_set())
            self.assertFalse(background.done())
        finally:
            background.cancel()
            await asyncio.gather(background, return_exceptions=True)

    async def test_synchronous_factory_failure(self):
        error = RuntimeError('factory failure')
        coroutines = []
        calls = []

        async def work():
            await asyncio.Event().wait()

        def first():
            calls.append('first')
            coro = work()
            coroutines.append(coro)
            return coro

        def fail():
            calls.append('fail')
            raise error

        try:
            with self.assertRaises(RuntimeError) as caught:
                await run_group([first, fail, first])
            self.assertIs(caught.exception, error)
            self.assertEqual(calls, ['first', 'fail'])
            self.assertTrue(all(inspect.getcoroutinestate(c) == inspect.CORO_CLOSED
                                for c in coroutines))
        finally:
            for coro in coroutines:
                coro.close()

    async def test_child_cancellation(self):
        started = asyncio.Event()
        cleaned = asyncio.Event()
        cancellation = asyncio.CancelledError('child cancelled')

        async def worker():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                cleaned.set()

        async def cancel():
            await started.wait()
            raise cancellation

        with self.assertRaises(asyncio.CancelledError) as caught:
            await run_group([worker, cancel])
        self.assertIs(caught.exception, cancellation)
        self.assertTrue(cleaned.is_set())

    async def test_parent_cancellation(self):
        started = [asyncio.Event(), asyncio.Event()]
        cleaned = [asyncio.Event(), asyncio.Event()]

        async def worker(i):
            started[i].set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                cleaned[i].set()

        parent = asyncio.create_task(run_group([lambda i=i: worker(i) for i in range(2)]))
        await asyncio.gather(*(e.wait() for e in started))
        parent.cancel('external cancellation')
        with self.assertRaises(asyncio.CancelledError) as caught:
            await parent
        self.assertEqual(caught.exception.args, ('external cancellation',))
        self.assertTrue(all(e.is_set() for e in cleaned))

    async def test_external_cancel_during_failure_cleanup(self):
        started = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        cleaned = asyncio.Event()
        error = LookupError('original failure')

        async def worker():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()

        async def fail():
            await started.wait()
            raise error

        parent = asyncio.create_task(run_group([worker, fail]))
        await asyncio.wait_for(cleaning.wait(), 1)
        parent.cancel('during cleanup')
        await asyncio.sleep(0)
        self.assertFalse(parent.done())
        release.set()
        with self.assertRaises(LookupError) as caught:
            await parent
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set())

    async def test_parent_cancel_does_not_interrupt_existing_child_cleanup(self):
        cleaning = asyncio.Event()
        release = asyncio.Event()
        cleaned = asyncio.Event()

        async def worker():
            try:
                asyncio.current_task().cancel('self cancellation')
                await asyncio.sleep(0)
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()

        parent = asyncio.create_task(run_group([worker]))
        await cleaning.wait()
        parent.cancel('external cancellation')
        await asyncio.sleep(0)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await parent
        self.assertTrue(cleaned.is_set())

    async def test_cleanup_failure_preserves_first_error(self):
        started = asyncio.Event()
        error = ValueError('initiating error')
        cleaned = asyncio.Event()

        async def worker():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()
                raise RuntimeError('cleanup error')

        async def fail():
            await started.wait()
            raise error

        with self.assertRaises(ValueError) as caught:
            await run_group([worker, fail])
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set())

    async def test_synchronous_factory_cancellation(self):
        cancellation = asyncio.CancelledError('factory cancellation')
        coroutines = []

        async def worker():
            await asyncio.Event().wait()

        def first():
            coro = worker()
            coroutines.append(coro)
            return coro

        def cancel():
            raise cancellation

        with self.assertRaises(asyncio.CancelledError) as caught:
            await run_group([first, cancel])
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(inspect.getcoroutinestate(coroutines[0]), inspect.CORO_CLOSED)

    async def test_parent_cancel_even_if_child_returns_from_cleanup(self):
        started = asyncio.Event()

        async def worker():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0)
                return 'cancel suppressed by child'

        parent = asyncio.create_task(run_group([worker]))
        await started.wait()
        parent.cancel('parent cancelled')
        with self.assertRaises(asyncio.CancelledError) as caught:
            await parent
        self.assertEqual(caught.exception.args, ('parent cancelled',))


if __name__ == '__main__':
    unittest.main()
