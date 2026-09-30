"""Post-hoc cancellation-during-cleanup probe, discovered from first c05 repairs."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys


async def check(path):
    spec=importlib.util.spec_from_file_location('cleanup_candidate',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    started=asyncio.Event();cleaning=asyncio.Event();release=asyncio.Event();cleaned=asyncio.Event()
    original_error=ValueError('initial failure')
    async def worker():
        started.set()
        try:await asyncio.Event().wait()
        finally:
            cleaning.set();await release.wait();cleaned.set()
    async def fail():
        await started.wait();raise original_error
    group=asyncio.create_task(module.run_group([worker,fail]))
    try:
        await asyncio.wait_for(cleaning.wait(),1)
        group.cancel()  # Exactly one external cancellation, after failure starts cleanup.
        for _ in range(5):await asyncio.sleep(0)
        early_exit=group.done();release.set()
        try:
            await asyncio.wait_for(group,1);outcome='normal_return';same_error=False
        except BaseException as error:
            outcome=type(error).__name__;same_error=error is original_error
        passed=not early_exit and cleaned.is_set() and (same_error or outcome=='CancelledError')
        return {'passed':passed,'returned_before_cleanup_release':early_exit,
                'cleanup_finished':cleaned.is_set(),'outcome':outcome,'original_error_identity':same_error}
    finally:
        release.set()
        if not group.done():group.cancel()
        await asyncio.gather(group,return_exceptions=True)


if __name__=='__main__':
    print(json.dumps(asyncio.run(check(Path(sys.argv[1]))),indent=2))
