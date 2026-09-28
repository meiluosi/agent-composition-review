import unittest

from scripts.analyze_ablation import aggregate, intervals
from scripts.run_ablation import CONDITIONS


class AblationAnalysisTests(unittest.TestCase):
    def test_contrasts_use_named_conditions_and_keep_task_pairing(self):
        result = intervals([[0, 0, .5, 1], [0, 0, .5, 1]])
        self.assertEqual(result["comparisons"]["C_minus_B"], {"mean": .5, "ci95": [.5, .5]})
        self.assertEqual(result["comparisons"]["D_minus_C"], {"mean": .5, "ci95": [.5, .5]})
        self.assertEqual(result["comparisons"]["D_minus_A"], {"mean": 1, "ci95": [1, 1]})
        identical = intervals([[0, 0, 0, 0], [1, 1, 1, 1]])
        self.assertEqual(identical["comparisons"]["D_minus_A"]["ci95"], [0, 0])
        self.assertNotEqual(identical["conditions"]["D_v2_core"]["ci95"], [0, 0])

    def test_failed_runs_and_missing_agent_labels_have_different_denominators(self):
        manifest = {"trials": 3, "schedule": list(range(96)),
                    "cases": [{"case_id": str(i), "dataset": "test"} for i in range(8)]}
        rows = []
        for i in range(8):
            for arm in CONDITIONS:
                for trial in range(3):
                    ok = i == 0 and trial == 0
                    rows.append({"case_id": str(i), "condition": arm, "scores": {
                        "location": ok, "agent": None if i == 7 else ok,
                        "joint": ok, "error_mode": None},
                        "status": "completed" if ok else "invalid_output", "elapsed_seconds": 1,
                        "usage": None})
        result = aggregate(manifest, rows)
        stats = result["statistics"]["fixed_development_set"]
        self.assertEqual(stats["location"]["tasks"], 8)
        self.assertEqual(stats["agent"]["tasks"], 7)
        self.assertAlmostEqual(stats["location"]["conditions"]["A_no_skill"]["mean"], 1/24)
        self.assertAlmostEqual(stats["agent"]["conditions"]["A_no_skill"]["mean"], 1/21)
        self.assertEqual(result["resources"]["D_v2_core"]["status_counts"]["invalid_output"], 23)


if __name__ == "__main__":
    unittest.main()
