import asyncio
from pathlib import Path
import unittest
from evals.code_repair.acceptance import evaluate
from .check_cleanup import check

ROOT=Path(__file__).resolve().parents[2]


class CleanupRegression(unittest.TestCase):
    def test_frozen_normal_is_a_counterexample_to_cleanup_guarantee(self):
        result=asyncio.run(check(ROOT/'evals/code_repair/tasks/async_group/healthy.py'))
        self.assertTrue(result['returned_before_cleanup_release'])
        self.assertFalse(result['cleanup_finished'])

    def test_revised_reference_preserves_existing_checks_and_finishes_cleanup(self):
        path=Path(__file__).with_name('async_healthy.py')
        self.assertTrue(evaluate('async_group',path)['passed'])
        self.assertTrue(asyncio.run(check(path))['passed'])


if __name__=='__main__':unittest.main()
