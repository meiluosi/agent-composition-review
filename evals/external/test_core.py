import copy
import unittest
from evals.external.core import normalize_pro, normalize_who_when, sanitize, score


class AttributionTests(unittest.TestCase):
    def prediction(self, **changes):
        return dict(agents=["agent_1"],location="history:1",error_mode=None,confidence=.6,reason="Evidence",**changes)

    def test_step_and_agent_substrings_do_not_count(self):
        gold={"agents":["agent_1"],"agent_applicable":True,"location":"history:1","error_mode":None}
        pred=self.prediction();pred.update(agents=["agent_10"],location="history:10")
        result=score(pred,gold)
        self.assertFalse(result["agent"]);self.assertFalse(result["location"])

    def test_missing_prediction_is_failure_not_removed(self):
        gold={"agents":[],"agent_applicable":False,"location":"step:1","error_mode":"R.1"}
        self.assertEqual(score(None,gold),dict(valid=False,location=False,agent=None,joint=False,error_mode=False))

    def test_single_agent_metric_is_not_applicable(self):
        gold={"agents":[],"agent_applicable":False,"location":"history:1","error_mode":None}
        result=score(self.prediction(),gold)
        self.assertIsNone(result["agent"]);self.assertTrue(result["joint"])

    def test_nested_injection_markers_removed_observed_answer_preserved(self):
        value={"turns":[{"agent_id":"a","is_injected":True,"is_replayed":False,
                         "observation":{"answer":42},"injection_via":"prompt"}]}
        clean=sanitize(value)
        self.assertEqual(clean,{"turns":[{"agent_id":"a","observation":{"answer":42}}]})
        self.assertTrue(value["turns"][0]["is_injected"])

    def test_who_when_input_excludes_labels_and_reference_answer(self):
        row={"question":"q","question_ID":"x","ground_truth":"SECRET", "mistake_agent":"a",
             "mistake_step":"0","mistake_reason":"SECRET", "history":[{"name":"a","role":"assistant","content":"trace"}]}
        case=normalize_who_when(row,"x","s")
        self.assertNotIn("SECRET",str(case["input"]))
        self.assertEqual(case["input"]["trajectory"][0]["location"],"history:0")

    def test_pro_nested_round_mapping_and_label_isolation(self):
        row={"id":"x","framework":"debate","benchmark":"b","task":{"query":"q","answer":"SECRET"},
             "ground_truth":{"round":1,"agents":["a"],"mode":"R.1"},
             "trajectory":[{"kind":"round","round":1,"turns":[{"agent_id":"a","is_injected":True,"output":"trace"}]}]}
        case=normalize_pro(row)
        self.assertEqual(case["gold"]["location"],"round:1")
        self.assertNotIn("SECRET",str(case["input"]));self.assertNotIn("is_injected",str(case["input"]))

    def test_gold_location_missing_from_trace_rejected(self):
        row={"id":"x","framework":"smolagents","benchmark":"b","task":{"query":"q"},
             "ground_truth":{"step":2,"agent":None,"mode":"R.1"},"trajectory":[{"step_number":1}]}
        with self.assertRaises(ValueError): normalize_pro(row)

    def test_multiple_responsible_agents_require_exact_set(self):
        gold={"agents":["agent_1","agent_2"],"agent_applicable":True,"location":"history:1","error_mode":None}
        self.assertFalse(score(self.prediction(),gold)["agent"])
        pred=self.prediction();pred["agents"]=["agent_2","agent_1"]
        self.assertTrue(score(pred,gold)["joint"])


if __name__=="__main__": unittest.main()
