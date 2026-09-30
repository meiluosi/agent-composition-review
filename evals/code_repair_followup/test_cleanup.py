import asyncio
from pathlib import Path
import unittest
import tempfile
from evals.code_repair.acceptance import evaluate
from .check_cleanup import check,check_all,check_self_cancel

ROOT=Path(__file__).resolve().parents[2]


class CleanupRegression(unittest.TestCase):
    def test_frozen_normal_is_a_counterexample_to_cleanup_guarantee(self):
        result=asyncio.run(check(ROOT/'evals/code_repair/tasks/async_group/healthy.py'))
        self.assertTrue(result['returned_before_cleanup_release'])
        self.assertFalse(result['cleanup_finished'])

    def test_revised_reference_preserves_existing_checks_and_finishes_cleanup(self):
        path=Path(__file__).with_name('async_healthy.py')
        self.assertTrue(evaluate('async_group',path)['passed'])
        self.assertTrue(asyncio.run(check_all(path))['passed'])

    def test_repeated_child_cancellation_can_interrupt_cleanup(self):
        source=Path(__file__).with_name('async_healthy.py').read_text().replace(' and not task.cancelling()','')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'app.py';path.write_text(source)
            self.assertFalse(asyncio.run(check_self_cancel(path))['cleanup_finished'])


if __name__=='__main__':unittest.main()
