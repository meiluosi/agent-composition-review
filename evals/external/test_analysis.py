"""Check task-level uncertainty and denominators without model calls."""

import unittest

from scripts.summarize_external_eval import aggregate, cluster_interval


class AnalysisTests(unittest.TestCase):
    def test_paired_identical_conditions_have_zero_difference_interval(self):
        # Absolute performance is uncertain; the paired difference is exactly zero.
        stats = cluster_interval([[0, 0], [1, 1], [0, 0], [1, 1]])
        self.assertEqual(stats["paired_difference"], {"mean": 0.0, "ci95": [0.0, 0.0]})
        self.assertLess(stats["with_skill"]["ci95"][0], stats["with_skill"]["ci95"][1])

    def test_repeats_are_not_independent_tasks_and_failures_stay_in_denominator(self):
        manifest = {"trials": 3, "cases": [{"case_id": "a", "dataset": "test"},
                                         {"case_id": "b", "dataset": "test"}]}
        rows = []
        for cid in ("a", "b"):
            for condition in ("without_skill", "with_skill"):
                for trial in range(3):
                    passed = cid == "a" and trial == 0
                    rows.append({"case_id": cid, "dataset": "test", "condition": condition,
                                 "scores": {"location": passed, "agent": None,
                                            "joint": passed, "error_mode": None},
                                 "status": "completed" if passed else "invalid_output",
                                 "elapsed_seconds": 1, "usage": None})
        result = aggregate(manifest, rows)["test"]
        self.assertEqual(result["metrics"]["location"]["independent_tasks"], 2)
        self.assertAlmostEqual(result["metrics"]["location"]["with_skill"]["mean"], 1 / 6)
        self.assertNotIn("agent", result["metrics"])
        self.assertEqual(result["resources"]["with_skill"]["status_counts"]["invalid_output"], 5)


if __name__ == "__main__":
    unittest.main()
