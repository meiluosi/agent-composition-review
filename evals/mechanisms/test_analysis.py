"""Ensure paired families, repeated trials, and invalid runs have fixed denominators."""

import copy
import unittest

from evals.mechanisms.core import score
from evals.mechanisms.test_core import CASES, answer
from scripts.analyze_mechanisms import aggregate
from scripts.run_mechanism_eval import ARMS


class MechanismAnalysisTests(unittest.TestCase):
    def fixture(self):
        cases = {c["case_id"]: c for c in CASES}
        rows = []
        for cid, case in cases.items():
            for arm in ARMS:
                for trial in (1, 2):
                    rows.append({"case_id": cid, "condition": arm, "trial": trial,
                                 "status": "completed", "scores": score(answer(case), case),
                                 "elapsed_seconds": 1, "usage": None, "tool_item_count": 0, "read_files": []})
        manifest = {"schedule": [{k: r[k] for k in ("case_id", "condition", "trial")} for r in rows], "trials": 2}
        return cases, rows, manifest

    def test_pair_success_requires_both_variants_and_keeps_failure(self):
        cases, rows, manifest = self.fixture()
        row = next(r for r in rows if r["case_id"] == "m02" and r["condition"] == ARMS[2] and r["trial"] == 1)
        row.update(status="infrastructure_failure", scores=score(None, cases["m02"]))
        stats = aggregate(manifest, rows, cases)
        self.assertEqual(stats["by_pair"]["recovery"][ARMS[2]], .5)
        self.assertAlmostEqual(stats["metrics"][ARMS[2]]["pair_success"], 11/12)
        self.assertEqual(stats["metrics"][ARMS[2]]["runs"], 24)
        self.assertEqual(stats["metrics"][ARMS[2]]["healthy_invalid"], 1)
        self.assertEqual(stats["metrics"][ARMS[2]]["healthy_runs"], 10)

    def test_duplicate_or_missing_runs_are_rejected(self):
        cases, rows, manifest = self.fixture()
        with self.assertRaises(AssertionError): aggregate(manifest, rows[:-1], cases)
        rows[-1] = copy.deepcopy(rows[0])
        with self.assertRaises(AssertionError): aggregate(manifest, rows, cases)


if __name__ == "__main__":
    unittest.main()
