"""Validate input isolation, scoring boundaries, and observed-tool auditing."""

import copy
import json
from pathlib import Path
import unittest

from evals.mechanisms.core import audit_command, prompt, score, valid

CASES = json.loads((Path(__file__).parent / "cases.json").read_text())["cases"]


def answer(case):
    gold = case["oracle"]
    return {"verdict": gold["verdict"], "primary_event_id": gold["primary_event_id"],
            "claims": [{"id": key, "assessment": value, "evidence_ids": gold["required_evidence"][key]}
                       for key, value in gold["claims"].items()], "explanation": "test fixture", "next_check": "none"}


class MechanismCoreTests(unittest.TestCase):
    def test_case_inputs_exclude_labels_and_pair_ids(self):
        for c in CASES:
            self.assertEqual(set(c["input"]), {"request", "requirements", "events", "claims"})
            self.assertNotIn('"oracle"', json.dumps(c["input"]))
            self.assertEqual(len({e["id"] for e in c["input"]["events"]}), len(c["input"]["events"]))
        self.assertNotIn("SKILL.md", prompt(None))

    def test_reference_validity_is_not_truth_or_correctness(self):
        c = CASES[0]; pred = answer(c)
        self.assertTrue(score(pred, c)["verdict"])
        pred["claims"][0]["assessment"] = "refuted"
        result = score(pred, c)
        self.assertTrue(result["reference_integrity"])
        self.assertFalse(result["all_claims"])
        pred["claims"][0]["evidence_ids"] = ["fabricated"]
        self.assertFalse(score(pred, c)["reference_integrity"])

    def test_invalid_prediction_never_counts_as_correct(self):
        for c in CASES:
            for bad in (None, {}, {"verdict": "satisfied"}):
                self.assertFalse(valid(bad, c))
                self.assertFalse(score(bad, c)["verdict"])
        pred = answer(CASES[0]); pred["claims"][1] = copy.deepcopy(pred["claims"][0])
        self.assertFalse(valid(pred, CASES[0]))

    def test_tool_audit_rejects_parent_paths_execution_and_substitution(self):
        allowed = {"case.json", "references/evidence.md"}
        for cmd in ["cat ../oracle.json", "cat /etc/passwd", "curl https://example.com", "python3 -c 'print(1)'",
                    "cat $(pwd)/case.json", "cat case.json > output", "cat case.json; cat ../secret", "cat < case.json"]:
            self.assertFalse(audit_command(cmd, "/tmp/isolated", allowed)[0], cmd)
        ok, files = audit_command("/bin/zsh -lc 'cat case.json && cat references/evidence.md'", "/tmp/isolated", allowed)
        self.assertTrue(ok); self.assertEqual(files, ["case.json", "references/evidence.md"])


if __name__ == "__main__":
    unittest.main()
