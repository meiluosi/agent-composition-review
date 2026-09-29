"""Exercise invalid references and impossible evidence transitions, not prompt wording."""

import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "iterations/v3-candidate/scripts/check_evidence.py"
spec = importlib.util.spec_from_file_location("evidence_checker", PATH)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def example():
    return {"target": "current_contract", "verdict": "violation",
            "requirements": [{"id": "r", "text": "required delivery path"}],
            "events": [{"id": "e", "kind": "observation", "source": "trace:7", "actor": "tool", "text": "wrong path"}],
            "findings": [{"id": "f", "requirement_id": "r", "event_ids": ["e"], "claim": "wrong path",
                          "status": "confirmed", "execution": "observed", "recovery": "unrecovered",
                          "recovery_event_ids": [], "causal": "untested", "intervention_event_ids": []}],
            "selected_ids": ["f"]}


class EvidenceTests(unittest.TestCase):
    def test_observation_can_confirm_contract_without_claiming_causality(self):
        self.assertEqual(checker.check(example()), [])

    def test_plan_cannot_be_upgraded_to_confirmed_execution(self):
        record = example()
        record["events"][0]["kind"] = "plan"
        record["findings"][0]["execution"] = "planned"
        self.assertTrue(checker.check(record))
        record["findings"][0]["status"] = "risk"
        record["verdict"] = "insufficient"
        self.assertEqual(checker.check(record), [])

    def test_recovery_and_intervention_require_references(self):
        for key, value in [("recovery", "recovered"), ("causal", "intervention_supported")]:
            r = example(); r["findings"][0][key] = value
            self.assertTrue(checker.check(r))
        r = example(); r["findings"][0].update(recovery="recovered", recovery_event_ids=["e"])
        r.update(verdict="satisfied", selected_ids=[])
        self.assertEqual(checker.check(r), [])  # history remains, current selection changes

    def test_invalid_references_duplicates_and_malformed_values(self):
        mutations = [lambda r: r["events"].append(copy.deepcopy(r["events"][0])),
                     lambda r: r["findings"][0].update(event_ids=["missing"]),
                     lambda r: r["findings"][0].update(requirement_id=[]),
                     lambda r: r.update(selected_ids=[{}]),
                     lambda r: r.update(target={}),
                     lambda r: r.update(events="not an array"),
                     lambda r: r.update(verdict="satisfied")]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                r = example(); mutate(r); self.assertTrue(checker.check(r))

    def test_checker_explicitly_cannot_detect_structurally_valid_lies(self):
        r = example(); r["events"][0]["text"] = "Fabricated observation, not in any real trace."
        self.assertEqual(checker.check(r), [])


if __name__ == "__main__":
    unittest.main()
