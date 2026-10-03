import asyncio
import importlib
import sys
import unittest

MODULE = sys.argv.pop(1) if len(sys.argv) > 1 else 'app'
run_group = importlib.import_module(MODULE).run_group


class GroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owned = []
        self.loop_errors = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: self.loop_errors.append(context))

    async def asyncTearDown(self):
        for task in self.owned:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.owned, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertEqual(self.loop_errors, [], 'unobserved asynchronous exception')

    def track(self):
        self.owned.append(asyncio.current_task())

    async def test_empty(self):
        self.assertEqual(await run_group([]), [])

    async def test_parallel_and_input_order(self):
        started = [asyncio.Event() for _ in range(3)]
        finished = []
        async def work(i):
            for_event = started[i]
            for_event.set()
            await asyncio.gather(*(event.wait() for event in started))
            await asyncio.sleep((2 - i) * .01)
            finished.append(i)
            return i * 2
        result = await asyncio.wait_for(run_group([lambda i=i: work(i) for i in range(3)]), 1)
        self.assertEqual(result, [0, 2, 4])
        self.assertEqual(finished, [2, 1, 0])

    async def check_failure(self, error):
        ready, cleaned = asyncio.Event(), asyncio.Event()
        async def sibling():
            self.track()
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.01)
                cleaned.set()
        async def fail():
            await ready.wait()
            raise error
        with self.assertRaises(type(error)) as caught:
            await run_group([sibling, fail])
        self.assertIs(caught.exception, error)
        self.assertTrue(cleaned.is_set(), 'returned before sibling cleanup')

    async def test_exception_identity_and_cleanup(self):
        await self.check_failure(ValueError('original error'))

    async def test_base_exception_identity_and_cleanup(self):
        class Stop(BaseException):
            pass
        await self.check_failure(Stop('original base exception'))

    async def test_child_cancellation(self):
        await self.check_failure(asyncio.CancelledError('child cancelled'))

    async def test_synchronous_factory_failure(self):
        created = []
        error = LookupError('factory failed')
        async def work():
            await asyncio.sleep(0)
        def good():
            coro = work()
            created.append(coro)
            return coro
        def bad():
            raise error
        try:
            with self.assertRaises(LookupError) as caught:
                await run_group([good, bad])
            self.assertIs(caught.exception, error)
            self.assertIsNone(created[0].cr_frame, 'created coroutine was not closed')
        finally:
            for coro in created:
                coro.close()

    async def test_external_cancel_waits_for_every_cleanup(self):
        ready = [asyncio.Event(), asyncio.Event()]
        cleaned = []
        async def work(i):
            self.track()
            ready[i].set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(i * .03)
                cleaned.append(i)
        parent = asyncio.create_task(run_group([lambda: work(0), lambda: work(1)]))
        self.owned.append(parent)
        await asyncio.gather(*(event.wait() for event in ready))
        parent.cancel('external reason')
        with self.assertRaises(asyncio.CancelledError) as caught:
            await parent
        self.assertEqual(caught.exception.args, ('external reason',))
        self.assertCountEqual(cleaned, [0, 1])

    async def test_cancel_during_failure_cleanup(self):
        ready, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))
        async def sibling():
            self.track()
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()
        async def fail():
            await ready.wait()
            raise ValueError('failure before external cancellation')
        parent = asyncio.create_task(run_group([sibling, fail]))
        self.owned.append(parent)
        try:
            await asyncio.wait_for(cleaning.wait(), .3)
            parent.cancel('during cleanup')
            await asyncio.sleep(0)
            self.assertFalse(parent.done(), 'parent did not wait for cleanup')
            release.set()
            with self.assertRaises(asyncio.CancelledError) as caught:
                await parent
            self.assertEqual(caught.exception.args, ('during cleanup',))
            self.assertTrue(cleaned.is_set())
        finally:
            release.set()

    async def test_already_cancelling_child_is_not_cancelled_twice(self):
        ready, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))
        async def self_cancel():
            self.track()
            ready.set()
            asyncio.current_task().cancel()
            try:
                await asyncio.sleep(0)
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()
        async def fail():
            await cleaning.wait()
            raise ValueError('other child failed')
        parent = asyncio.create_task(run_group([self_cancel, fail]))
        self.owned.append(parent)
        await ready.wait()
        await cleaning.wait()
        await asyncio.sleep(.01)
        release.set()
        with self.assertRaises(ValueError):
            await parent
        self.assertTrue(cleaned.is_set())

    async def test_unrelated_background_task_survives(self):
        background = asyncio.create_task(asyncio.Event().wait())
        self.owned.append(background)
        async def fail():
            raise RuntimeError('failure')
        with self.assertRaises(RuntimeError):
            await run_group([fail])
        self.assertFalse(background.done())
        self.assertEqual(background.cancelling(), 0)

    async def test_cleanup_error_does_not_replace_original(self):
        ready = asyncio.Event()
        original = ValueError('first failure')
        async def sibling():
            self.track()
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                raise RuntimeError('cleanup failed')
        async def fail():
            await ready.wait()
            raise original
        with self.assertRaises(ValueError) as caught:
            await run_group([sibling, fail])
        self.assertIs(caught.exception, original)
        self.assertTrue(self.owned[0].done())

    async def test_factory_cancellation_identity(self):
        original = asyncio.CancelledError('factory cancelled')
        created = []
        async def work():
            await asyncio.sleep(0)
        def good():
            coro = work()
            created.append(coro)
            return coro
        def bad():
            raise original
        try:
            with self.assertRaises(asyncio.CancelledError) as caught:
                await run_group([good, bad])
            self.assertIs(caught.exception, original)
            self.assertIsNone(created[0].cr_frame)
        finally:
            for coro in created:
                coro.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
