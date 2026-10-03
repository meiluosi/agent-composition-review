import argparse
import asyncio
import importlib
import inspect
import unittest


parser = argparse.ArgumentParser()
parser.add_argument('--module', default='app')
args, remaining = parser.parse_known_args()
run_group = importlib.import_module(args.module).run_group


class GroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.before = asyncio.all_tasks()
        self.coroutines = []

    async def asyncTearDown(self):
        pending = asyncio.all_tasks() - self.before - {asyncio.current_task()}
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for coroutine in self.coroutines:
            coroutine.close()

    async def test_empty(self):
        self.assertEqual(await run_group([]), [])

    async def test_parallel_order_and_identity(self):
        started = [asyncio.Event() for _ in range(3)]
        values = [object(), None, ValueError('ordinary result')]
        finished = []

        async def work(i):
            started[i].set()
            await asyncio.gather(*(event.wait() for event in started))
            await asyncio.sleep((2 - i) * 0.005)
            finished.append(i)
            return values[i]

        result = await asyncio.wait_for(
            run_group([lambda i=i: work(i) for i in range(3)]), 1)
        self.assertEqual(finished, [2, 1, 0])
        self.assertTrue(all(a is b for a, b in zip(result, values)))

    async def failure_case(self, error):
        started = asyncio.Event()
        cleaned = asyncio.Event()
        background_started = asyncio.Event()

        async def background():
            background_started.set()
            await asyncio.Event().wait()

        unrelated = asyncio.create_task(background())
        await background_started.wait()

        async def sibling():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                cleaned.set()

        async def fail():
            await started.wait()
            raise error

        with self.assertRaises(type(error)) as caught:
            await run_group([sibling, fail])
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set(), 'returned before sibling cleanup')
        self.assertFalse(unrelated.done())
        self.assertEqual(unrelated.cancelling(), 0)

    async def test_child_failure(self):
        await self.failure_case(ValueError('child failure'))

    async def test_child_cancelled_error(self):
        await self.failure_case(asyncio.CancelledError('child cancellation'))

    async def test_base_exception(self):
        class StopGroup(BaseException):
            pass
        await self.failure_case(StopGroup('stop'))

    async def factory_case(self, error, position):
        calls = []

        async def work():
            await asyncio.sleep(0)

        def factory(i):
            def create():
                calls.append(i)
                if i == position:
                    raise error
                coroutine = work()
                self.coroutines.append(coroutine)
                return coroutine
            return create

        with self.assertRaises(type(error)) as caught:
            await run_group([factory(i) for i in range(3)])
        self.assertIs(caught.exception, error)
        self.assertEqual(calls, list(range(position + 1)))
        self.assertTrue(all(inspect.getcoroutinestate(c) == inspect.CORO_CLOSED
                            for c in self.coroutines), 'unclosed coroutine')

    async def test_factory_failure_first(self):
        await self.factory_case(RuntimeError('factory'), 0)

    async def test_factory_failure_middle(self):
        await self.factory_case(RuntimeError('factory'), 1)

    async def test_factory_cancellation_last(self):
        await self.factory_case(asyncio.CancelledError('factory'), 2)

    async def test_parent_cancellation(self):
        started = [asyncio.Event(), asyncio.Event()]
        cleaned = []

        async def work(i):
            started[i].set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                cleaned.append(i)

        parent = asyncio.create_task(run_group([lambda: work(0), lambda: work(1)]))
        await asyncio.gather(*(event.wait() for event in started))
        parent.cancel('external')
        with self.assertRaises(asyncio.CancelledError) as caught:
            await parent
        self.assertEqual(caught.exception.args, ('external',))
        self.assertEqual(sorted(cleaned), [0, 1])

    async def test_parent_cancel_during_failure_cleanup(self):
        started = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        cleaned = asyncio.Event()
        error = RuntimeError('primary')

        async def sibling():
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

        parent = asyncio.create_task(run_group([sibling, fail]))
        # Also finish promptly against implementations that never initiate cleanup.
        observer = asyncio.create_task(cleaning.wait())
        done, _ = await asyncio.wait([parent, observer], return_when=asyncio.FIRST_COMPLETED)
        if parent in done:
            with self.assertRaises(RuntimeError):
                await parent
            release.set()
            self.fail('parent exited without waiting for cleanup')
        parent.cancel('during cleanup')
        await asyncio.sleep(0)
        self.assertFalse(parent.done())
        release.set()
        with self.assertRaises(RuntimeError) as caught:
            await parent
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set())

    async def test_already_cancelling_sibling_finishes_cleanup(self):
        cleaning = asyncio.Event()
        cleaned = asyncio.Event()
        error = ValueError('primary')

        async def sibling():
            asyncio.current_task().cancel()
            try:
                await asyncio.sleep(0)
            finally:
                cleaning.set()
                await asyncio.sleep(0.02)
                cleaned.set()

        async def fail():
            await cleaning.wait()
            raise error

        with self.assertRaises(ValueError) as caught:
            await run_group([sibling, fail])
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set())

    async def test_cleanup_error_does_not_replace_primary(self):
        started = asyncio.Event()
        cleaned = asyncio.Event()
        error = ValueError('primary')

        async def sibling():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()
                raise RuntimeError('cleanup')

        async def fail():
            await started.wait()
            raise error

        with self.assertRaises(ValueError) as caught:
            await run_group([sibling, fail])
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set())


if __name__ == '__main__':
    unittest.main(argv=['test_run_group.py', *remaining])
