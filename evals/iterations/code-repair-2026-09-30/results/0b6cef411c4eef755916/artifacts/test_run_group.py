import asyncio
import gc
import unittest

from app import run_group


class GroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loop_errors = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: self.loop_errors.append(context)
        )

    async def asyncTearDown(self):
        gc.collect()
        await asyncio.sleep(0)
        self.assertEqual(self.loop_errors, [])
        self.assertEqual(asyncio.all_tasks(), {asyncio.current_task()})

    async def test_empty(self):
        self.assertEqual(await run_group([]), [])

    async def test_parallel_and_input_order(self):
        started = [asyncio.Event() for _ in range(3)]
        finished = []

        async def work(index):
            started[index].set()
            await asyncio.gather(*(event.wait() for event in started))
            await asyncio.sleep((2 - index) * 0.01)
            finished.append(index)
            return index * 2

        result = await asyncio.wait_for(
            run_group([lambda i=i: work(i) for i in range(3)]), 1
        )
        self.assertEqual(result, [0, 2, 4])
        self.assertEqual(finished, [2, 1, 0])

    async def test_factory_failure_identity_and_owned_tasks(self):
        error = ValueError('factory failed')
        created = []

        async def work():
            await asyncio.sleep(10)

        def good():
            coroutine = work()
            created.append(coroutine)
            return coroutine

        def bad():
            raise error

        background = asyncio.create_task(asyncio.sleep(10))
        try:
            with self.assertRaises(ValueError) as caught:
                await run_group([good, bad, good])
            self.assertIs(caught.exception, error)
            self.assertEqual(len(created), 1)
            self.assertIsNone(created[0].cr_frame)
            self.assertFalse(background.done())
        finally:
            background.cancel()
            await asyncio.gather(background, return_exceptions=True)

    async def test_child_failure_waits_for_cleanup(self):
        await self.check_child_exit(ValueError('child failed'))

    async def test_child_cancellation_waits_for_cleanup(self):
        await self.check_child_exit(asyncio.CancelledError('child cancelled'))

    async def check_child_exit(self, error):
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def sibling():
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                await asyncio.sleep(0.02)
                cleaned.set()

        async def fail():
            await started.wait()
            raise error

        with self.assertRaises(type(error)) as caught:
            await run_group([sibling, fail])
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set())

    async def test_parent_cancellation_does_not_interrupt_cleanup(self):
        started = [asyncio.Event(), asyncio.Event()]
        cleaned = [False, False]

        async def work(index):
            started[index].set()
            try:
                await asyncio.sleep(10)
            finally:
                await asyncio.sleep(index * 0.03)
                cleaned[index] = True

        group = asyncio.create_task(run_group([lambda: work(0), lambda: work(1)]))
        await asyncio.gather(*(event.wait() for event in started))
        group.cancel('parent cancelled')
        with self.assertRaises(asyncio.CancelledError) as caught:
            await group
        self.assertEqual(caught.exception.args, ('parent cancelled',))
        self.assertEqual(cleaned, [True, True])

    async def test_parent_cancellation_during_failure_cleanup(self):
        started = asyncio.Event()
        cleaning = asyncio.Event()
        cleaned = asyncio.Event()

        async def sibling():
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                cleaning.set()
                await asyncio.sleep(0.03)
                cleaned.set()

        async def fail():
            await started.wait()
            raise ValueError('original failure')

        group = asyncio.create_task(run_group([sibling, fail]))
        await cleaning.wait()
        group.cancel('cancel during cleanup')
        with self.assertRaises(asyncio.CancelledError) as caught:
            await group
        self.assertEqual(caught.exception.args, ('cancel during cleanup',))
        self.assertTrue(cleaned.is_set())

    async def test_factory_cancellation_identity(self):
        error = asyncio.CancelledError('factory cancelled')

        def factory():
            raise error

        with self.assertRaises(asyncio.CancelledError) as caught:
            await run_group([factory])
        self.assertIs(caught.exception, error)

    async def test_already_cancelling_child_finishes_cleanup(self):
        cleaning = asyncio.Event()
        cleaned = asyncio.Event()
        error = RuntimeError('another child failed')

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

        with self.assertRaises(RuntimeError) as caught:
            await run_group([self_cancel, fail])
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set())

    async def test_cleanup_exception_does_not_replace_primary_failure(self):
        started = asyncio.Event()
        error = ValueError('primary')

        async def sibling():
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                await asyncio.sleep(0)
                raise RuntimeError('cleanup')

        async def fail():
            await started.wait()
            raise error

        with self.assertRaises(ValueError) as caught:
            await run_group([sibling, fail])
        self.assertIs(caught.exception, error)


if __name__ == '__main__':
    unittest.main()
