import asyncio
import importlib
import json
import sys


run_group = importlib.import_module(sys.argv[1] if len(sys.argv) > 1 else "app").run_group


async def normal():
    assert await run_group([]) == []
    started = [asyncio.Event() for _ in range(3)]
    completed = []
    values = [object(), None, ValueError("a legitimate result")]

    async def work(i):
        started[i].set()
        for event in started:
            await event.wait()
        await asyncio.sleep((2 - i) * 0.005)
        completed.append(i)
        return values[i]

    result = await run_group([lambda i=i: work(i) for i in range(3)])
    assert all(a is b for a, b in zip(result, values))
    assert len(result) == 3 and completed == [2, 1, 0]


async def factory_failure(cancelled=False):
    error = asyncio.CancelledError("factory") if cancelled else ValueError("factory")
    owned = []

    async def work():
        await asyncio.Event().wait()

    def first():
        return work()

    def fail():
        owned.extend(t for t in asyncio.all_tasks() if t is not asyncio.current_task())
        raise error

    try:
        await run_group([first, fail, lambda: (_ for _ in ()).throw(AssertionError("called after failure"))])
    except BaseException as caught:
        assert caught is error
    else:
        raise AssertionError("factory failure swallowed")
    assert owned and all(t.done() for t in owned)


async def lifecycle(mode):
    started = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    background_started = asyncio.Event()
    owned = []
    error = ValueError("original failure")

    async def background():
        background_started.set()
        await asyncio.Event().wait()

    async def worker():
        owned.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            finished.set()
            if mode.endswith("cleanup_error"):
                raise RuntimeError("secondary cleanup error")

    async def trigger():
        owned.append(asyncio.current_task())
        await started.wait()
        if mode == "child_cancel":
            raise asyncio.CancelledError("child")
        if mode in ("parent_cancel", "parent_cleanup_error"):
            await asyncio.Event().wait()
        raise error

    bg = asyncio.create_task(background())
    await background_started.wait()
    parent = asyncio.create_task(run_group([worker, trigger]))
    try:
        await started.wait()
        if mode in ("parent_cancel", "parent_cleanup_error"):
            parent.cancel("parent")
        await cleaning.wait()
        if mode == "cancel_during_cleanup":
            parent.cancel("during cleanup")
        # Allow cancellation to arrive before releasing the cleanup gate.
        for _ in range(4):
            await asyncio.sleep(0)
        exited_early = parent.done()
        release.set()
        try:
            await parent
        except BaseException as caught:
            if mode in ("failure", "cancel_during_cleanup", "failure_cleanup_error"):
                assert caught is error, f"original error replaced by {type(caught).__name__}"
            else:
                assert isinstance(caught, asyncio.CancelledError)
        else:
            raise AssertionError("failure/cancellation returned as success")
        assert not exited_early, "group exited before cleanup was released"
        assert finished.is_set(), "asynchronous cleanup was interrupted"
        assert all(t.done() for t in owned)
        assert not bg.done() and bg.cancelling() == 0
    finally:
        release.set()
        if not parent.done():
            parent.cancel()
        await asyncio.gather(parent, return_exceptions=True)
        bg.cancel()
        await asyncio.gather(bg, return_exceptions=True)


async def already_cancelling_child():
    cleaning = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    error = ValueError("peer failed during child cleanup")
    child = []

    async def work():
        child.append(asyncio.current_task())
        asyncio.current_task().cancel("self cancel")
        try:
            await asyncio.sleep(0)
        finally:
            cleaning.set()
            await release.wait()
            finished.set()

    async def fail():
        await cleaning.wait()
        raise error

    parent = asyncio.create_task(run_group([work, fail]))
    try:
        await cleaning.wait()
        for _ in range(8):
            await asyncio.sleep(0)
        assert child[0].cancelling() == 1, "cancel sent again during cleanup"
        release.set()
        try:
            await parent
        except ValueError as caught:
            assert caught is error
        else:
            raise AssertionError("peer failure swallowed")
        assert finished.is_set() and child[0].done()
    finally:
        release.set()
        await asyncio.gather(parent, return_exceptions=True)


async def main():
    loop = asyncio.get_running_loop()
    unhandled = []
    loop.set_exception_handler(lambda loop, context: unhandled.append(context))
    cases = [
        ("normal_empty_parallel_order_identity", normal),
        ("factory_error_identity", factory_failure),
        ("factory_cancel_identity", lambda: factory_failure(True)),
        *[(mode, lambda mode=mode: lifecycle(mode)) for mode in
          ("failure", "child_cancel", "parent_cancel", "cancel_during_cleanup",
           "failure_cleanup_error", "parent_cleanup_error")],
        ("already_cancelling_child", already_cancelling_child),
    ]
    results = []
    for name, case in cases:
        try:
            await asyncio.wait_for(case(), timeout=3)
        except BaseException as error:
            results.append({"test": name, "status": "fail", "error": repr(error)})
        else:
            results.append({"test": name, "status": "pass"})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    assert all(r["status"] == "pass" for r in results)
    await asyncio.sleep(0)
    assert not unhandled, unhandled


asyncio.run(main())
