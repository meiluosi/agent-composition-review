import asyncio
import unittest

from app import run_group


class RunGroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loop_errors = []
        self.loop = asyncio.get_running_loop()
        self.previous_handler = self.loop.get_exception_handler()
        self.loop.set_exception_handler(lambda loop, context: self.loop_errors.append(context))

    async def asyncTearDown(self):
        await asyncio.sleep(0)
        self.loop.set_exception_handler(self.previous_handler)
        self.assertEqual(self.loop_errors, [], 'unexpected event-loop errors')

    async def test_empty(self):
        self.assertEqual(await run_group([]), [])

    async def test_parallel_and_ordered(self):
        started = [asyncio.Event() for _ in range(3)]
        release = [asyncio.Event() for _ in range(3)]
        finished = [asyncio.Event() for _ in range(3)]
        values = [object() for _ in range(3)]

        async def work(i):
            started[i].set()
            await release[i].wait()
            finished[i].set()
            return values[i]

        group = asyncio.create_task(run_group([lambda i=i: work(i) for i in range(3)]))
        for event in started:
            await event.wait()
        for i in (2, 1, 0):
            release[i].set()
            await finished[i].wait()
        self.assertEqual(await group, values)

    async def test_child_failure_preserves_object_and_cleans_sibling(self):
        started = asyncio.Event()
        cleaned = asyncio.Event()
        error = ValueError('child failure')

        async def sibling():
            started.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0.01)
                cleaned.set()

        async def fail():
            await started.wait()
            raise error

        with self.assertRaises(ValueError) as caught:
            await run_group([sibling, fail])
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set())

    async def test_factory_failure_waits_for_created_task(self):
        for error in (LookupError('factory failure'), asyncio.CancelledError('factory cancel')):
            with self.subTest(error=type(error).__name__):
                created = []
                called_later = []

                async def work():
                    await asyncio.Future()

                def fail():
                    created.extend(t for t in asyncio.all_tasks() if t is not asyncio.current_task())
                    raise error

                def later():
                    called_later.append(True)
                    return work()

                with self.assertRaises(type(error)) as caught:
                    await run_group([work, fail, later])
                self.assertIs(caught.exception, error)
                self.assertEqual(len(created), 1)
                self.assertTrue(created[0].done())
                self.assertFalse(called_later)

    async def test_child_cancellation_preserves_object_and_cleans_sibling(self):
        started = asyncio.Event()
        cleaned = asyncio.Event()
        error = asyncio.CancelledError('child cancel')

        async def sibling():
            started.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0.01)
                cleaned.set()

        async def cancel():
            await started.wait()
            raise error

        with self.assertRaises(asyncio.CancelledError) as caught:
            await run_group([sibling, cancel])
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set())

    async def test_parent_cancel_waits_for_unequal_cleanup(self):
        started = [asyncio.Event(), asyncio.Event()]
        cleaned = []
        children = []

        async def work(i):
            children.append(asyncio.current_task())
            started[i].set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0 if i == 0 else 0.03)
                cleaned.append(i)

        group = asyncio.create_task(run_group([lambda: work(0), lambda: work(1)]))
        for event in started:
            await event.wait()
        group.cancel('parent cancel')
        with self.assertRaises(asyncio.CancelledError) as caught:
            await group
        self.assertEqual(caught.exception.args, ('parent cancel',))
        self.assertCountEqual(cleaned, [0, 1])
        self.assertTrue(all(t.done() for t in children))

    async def test_parent_cancel_during_failure_cleanup(self):
        started = asyncio.Event()
        cleaning = asyncio.Event()
        cleaned = asyncio.Event()
        error = RuntimeError('first failure')

        async def sibling():
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await asyncio.sleep(0.03)
                cleaned.set()

        async def fail():
            await started.wait()
            raise error

        group = asyncio.create_task(run_group([sibling, fail]))
        await cleaning.wait()
        group.cancel('during cleanup')
        try:
            await group
        except BaseException as caught:
            # Cleanup is mandatory regardless of which interruption wins.
            self.assertTrue(cleaned.is_set())
            self.assertTrue(caught is error or isinstance(caught, asyncio.CancelledError))
        else:
            self.fail('failure returned as a result')

    async def test_unrelated_task_survives(self):
        started = asyncio.Event()

        async def background():
            started.set()
            await asyncio.Future()

        async def fail():
            raise ValueError('failure')

        background_task = asyncio.create_task(background())
        try:
            await started.wait()
            with self.assertRaises(ValueError):
                await run_group([fail])
            self.assertFalse(background_task.done())
            self.assertEqual(background_task.cancelling(), 0)
        finally:
            background_task.cancel()
            await asyncio.gather(background_task, return_exceptions=True)

    async def test_already_cancelling_child_finishes_cleanup(self):
        cleaning = asyncio.Event()
        cleaned = asyncio.Event()
        error = ValueError('sibling failed during cancellation cleanup')

        async def self_cancel():
            asyncio.current_task().cancel()
            try:
                await asyncio.sleep(0)
            finally:
                cleaning.set()
                await asyncio.sleep(0.03)
                cleaned.set()

        async def fail():
            await cleaning.wait()
            raise error

        with self.assertRaises(ValueError) as caught:
            await run_group([self_cancel, fail])
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set())

    async def test_cleanup_failure_does_not_replace_first_error(self):
        started = asyncio.Event()
        error = ValueError('first error')

        async def sibling():
            started.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                raise RuntimeError('cleanup error')

        async def fail():
            await started.wait()
            raise error

        with self.assertRaises(ValueError) as caught:
            await run_group([sibling, fail])
        self.assertIs(caught.exception, error)


if __name__ == '__main__':
    unittest.main()
