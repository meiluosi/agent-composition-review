import asyncio
import unittest

from app import run_group


class RunGroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty(self):
        self.assertEqual(await run_group([]), [])

    async def test_parallel_and_input_order(self):
        started = [asyncio.Event() for _ in range(3)]
        completed = []

        async def work(index):
            started[index].set()
            await asyncio.gather(*(event.wait() for event in started))
            while len(completed) < 2 - index:
                await asyncio.sleep(0)
            completed.append(index)
            return index * 2

        result = await asyncio.wait_for(
            run_group([lambda i=i: work(i) for i in range(3)]), 2
        )
        self.assertEqual(result, [0, 2, 4])
        self.assertEqual(completed, [2, 1, 0])

    async def test_factory_failure_identity_and_owned_tasks(self):
        error = ValueError('factory')
        owned = []
        later_called = []

        async def work():
            await asyncio.sleep(10)

        def first():
            return work()

        def fail():
            owned.extend(asyncio.all_tasks() - {asyncio.current_task()})
            raise error

        with self.assertRaises(ValueError) as caught:
            await run_group([first, fail, lambda: later_called.append(True)])
        self.assertIs(caught.exception, error)
        self.assertTrue(owned)
        self.assertTrue(all(task.done() for task in owned))
        self.assertEqual(later_called, [])

    async def test_failure_waits_for_cleanup_and_keeps_background(self):
        await self.failure_scenario(cancel_parent=False)

    async def test_failure_cleanup_survives_parent_cancel(self):
        await self.failure_scenario(cancel_parent=True)

    async def failure_scenario(self, cancel_parent):
        ready = asyncio.Event()
        cleaning = asyncio.Event()
        release_cleanup = asyncio.Event()
        cleaned = asyncio.Event()
        error = RuntimeError('original child failure')

        async def sibling():
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release_cleanup.wait()
                cleaned.set()

        async def fail():
            await ready.wait()
            raise error

        background = asyncio.create_task(asyncio.Event().wait())
        parent = asyncio.create_task(run_group([sibling, fail]))
        try:
            await asyncio.wait_for(cleaning.wait(), 2)
            self.assertFalse(parent.done())
            if cancel_parent:
                parent.cancel('external cancellation during cleanup')
                for _ in range(5):
                    await asyncio.sleep(0)
            release_cleanup.set()
            try:
                await asyncio.wait_for(parent, 2)
            except BaseException as actual:
                self.assertTrue(cleaned.is_set(), 'sibling cleanup was interrupted')
                self.assertIs(actual, error)
            else:
                self.fail('failure became a normal result')
            self.assertFalse(background.done())
        finally:
            release_cleanup.set()
            if not parent.done():
                parent.cancel()
            background.cancel()
            await asyncio.gather(parent, background, return_exceptions=True)

    async def test_parent_cancel_waits_for_cleanup(self):
        ready = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        cleaned = []

        async def work():
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.append(True)

        parent = asyncio.create_task(run_group([work]))
        await asyncio.wait_for(ready.wait(), 2)
        parent.cancel('parent stop')
        await asyncio.wait_for(cleaning.wait(), 2)
        self.assertFalse(parent.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(parent, 2)
        self.assertEqual(caught.exception.args, ('parent stop',))
        self.assertEqual(cleaned, [True])

    async def test_child_cancellation_waits_for_sibling(self):
        ready = asyncio.Event()
        cleaned = []
        cancellation = asyncio.CancelledError('child stop')

        async def sibling():
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.append(True)

        async def cancelled():
            await ready.wait()
            raise cancellation

        with self.assertRaises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(run_group([sibling, cancelled]), 2)
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(cleaned, [True])

    async def test_factory_cancellation_identity(self):
        cancellation = asyncio.CancelledError('factory stop')

        def factory():
            raise cancellation

        with self.assertRaises(asyncio.CancelledError) as caught:
            await run_group([factory])
        self.assertIs(caught.exception, cancellation)

    async def test_cleanup_error_does_not_replace_original_failure(self):
        ready = asyncio.Event()
        error = LookupError('original')

        async def sibling():
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                raise RuntimeError('cleanup failure')

        async def fail():
            await ready.wait()
            raise error

        with self.assertRaises(LookupError) as caught:
            await run_group([sibling, fail])
        self.assertIs(caught.exception, error)

    async def test_parent_cancel_with_child_cleanup_error(self):
        started = [asyncio.Event(), asyncio.Event()]
        cleaning = asyncio.Event()
        release = asyncio.Event()
        cleaned = []
        loop_errors = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: loop_errors.append(context))

        async def bad_cleanup():
            started[0].set()
            try:
                await asyncio.Event().wait()
            finally:
                raise RuntimeError('cleanup failure')

        async def slow_cleanup():
            started[1].set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.append(True)

        parent = asyncio.create_task(run_group([bad_cleanup, slow_cleanup]))
        try:
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in started)), 2)
            parent.cancel('keep cancellation')
            await asyncio.wait_for(cleaning.wait(), 2)
            for _ in range(5):
                await asyncio.sleep(0)
            self.assertFalse(parent.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError) as caught:
                await asyncio.wait_for(parent, 2)
            self.assertEqual(caught.exception.args, ('keep cancellation',))
            self.assertEqual(cleaned, [True])
            await asyncio.sleep(0)
            self.assertEqual(loop_errors, [])
        finally:
            release.set()
            if not parent.done():
                parent.cancel()
            await asyncio.gather(parent, return_exceptions=True)
            loop.set_exception_handler(previous_handler)


if __name__ == '__main__':
    unittest.main()
