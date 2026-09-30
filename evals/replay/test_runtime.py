import copy
import unittest
from .runtime import composition as c
from .make_model import model
from .system import run, OPTIONS
from .defaults import DEFAULT


class ReplayTests(unittest.TestCase):
    def test_correct_pipeline_satisfies_generated_probes(self):
        m = model(); scenarios = c.generate(m)['cases']
        self.assertEqual(len(scenarios),48)
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                result = c.check(m,run(DEFAULT,scenario))
                self.assertEqual(result['verdict'],'satisfied',result)

    def test_each_mutation_is_detected_on_executed_behavior(self):
        m=model(); cases=c.generate(m)['cases']
        for key, options in OPTIONS.items():
            bad = {**DEFAULT,key:next(x for x in options if x != DEFAULT[key])}
            with self.subTest(key=key):
                failures = [c.check(m,run(bad,case)) for case in cases]
                self.assertTrue(any(x['verdict']=='violation' for x in failures))
                self.assertTrue(any(x['verdict']=='satisfied' for x in failures))

    def test_plans_missing_state_and_incomplete_traces_are_not_execution(self):
        m=model(); trace=run(DEFAULT,c.generate(m)['cases'][0])
        only_plans=copy.deepcopy(trace)
        for e in only_plans['events']: e['kind']='plan'
        self.assertEqual(c.check(m,only_plans)['verdict'],'violation')
        missing=copy.deepcopy(trace); missing['events'][0].pop('after')
        self.assertEqual(c.check(m,missing)['verdict'],'insufficient')
        trace['complete']=False
        self.assertEqual(c.check(m,trace)['verdict'],'insufficient')

    def test_broken_dependency_and_state_are_detected(self):
        m=model(); original=run(DEFAULT,c.generate(m)['cases'][0])
        for mutate in [lambda t:t['events'][1].update(parents=[]),
                       lambda t:t['events'][1]['before'].update(charged=999),
                       lambda t:t['events'][1]['input'].update(tenant='another')]:
            trace=copy.deepcopy(original); mutate(trace)
            self.assertEqual(c.check(m,trace)['verdict'],'violation')

    def test_unknown_dsl_invalid_graph_and_duplicate_ids_rejected(self):
        m=model(); m['actions'][0]['post'][0]['must']['op']='python_eval'
        with self.assertRaises(c.Invalid): c.plan(m)
        m=model();m['edges'][0]['to']='missing'
        with self.assertRaises(c.Invalid):c.plan(m)
        m=model(); t=run(DEFAULT,c.generate(m)['cases'][0]);t['events'][1]['id']='e1'
        with self.assertRaises(c.Invalid): c.check(m,t)

    def test_generation_budget_and_semantic_scope(self):
        with self.assertRaises(c.Invalid):c.generate(model(),limit=47)
        m=model();m['probes']['axes']['sample_count']=[1]
        self.assertEqual(len(c.generate(m)['cases']),24)
        # Repeated values are allowed when reuse, not fresh sampling, is the declared contract.
        m['probes']['base']['sampling_requirement']='reuse_allowed'
        for scenario in c.generate(m)['cases']:
            self.assertEqual(c.check(m,run({**DEFAULT,'sample_mode':'reuse'},scenario))['verdict'],'satisfied')

    def test_boolean_is_not_a_numeric_field(self):
        self.assertFalse(c.type_ok({'n':True},{'n':{'type':'number'}}))


if __name__ == '__main__':unittest.main()

class AcceptanceTests(unittest.TestCase):
    def test_independent_acceptance_and_minimal_repairs(self):
        from .acceptance import score, min_edits
        from .experiment import cases, extra_scenarios
        for case in cases():
            scenarios=c.generate(case['model'])['cases']+extra_scenarios(case['model'])
            self.assertTrue(score(DEFAULT,scenarios)['all_pass'])
            before=score(case['initial'],scenarios)
            if case['id'] in ('r07','r08'):
                self.assertTrue(before['all_pass']);self.assertEqual(min_edits(case['initial'],scenarios),0)
            else:
                self.assertFalse(before['all_pass']);self.assertGreater(min_edits(case['initial'],scenarios),0)
