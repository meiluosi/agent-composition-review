from pathlib import Path
import unittest
from evals.code_repair.acceptance import evaluate
from .check_transaction import check

ROOT=Path(__file__).resolve().parents[2]


class CommitRegression(unittest.TestCase):
    def test_frozen_normal_leaves_failed_batch_pending(self):
        result=check(ROOT/'evals/code_repair/tasks/transaction/healthy.py')
        self.assertFalse(result['passed']);self.assertEqual(result['failed_batch_remaining'],[('batch',1)])

    def test_revised_normal_passes_existing_checks_and_commit_failure_reuse(self):
        path=Path(__file__).with_name('transaction_healthy.py')
        self.assertTrue(evaluate('transaction',path)['passed']);self.assertTrue(check(path)['passed'])


if __name__=='__main__':unittest.main()
