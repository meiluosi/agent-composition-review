"""Guard pairing, missing-data semantics and predeclared decision thresholds."""
import copy
import unittest
from .analysis import summarize

ARMS = ['A_tools', 'B_checklist', 'C_skill']
MODELS = ['gpt-6-astra', 'gpt-6-luna']


def data():
    cases = {f'{f}-{v}': {'family': str(f), 'variant': v}
             for f in range(4) for v in ('incident', 'normal', 'local')}
    rows = []
    for case_id in cases:
        for trial in (1, 2):
            for model in MODELS:
                for arm in ARMS:
                    rows.append(dict(case_id=case_id, trial=trial, model_requested=model, condition=arm,
                                     status='completed', acceptance={'passed': True}, source_changed=False,
                                     prediction={'initial_verdict': 'satisfied' if cases[case_id]['variant'] == 'normal' else 'violation'},
                                     usage=None, elapsed_seconds=1, infra_kind=None))
    manifest = {'schedule': [{k: r[k] for k in ('case_id', 'trial', 'model_requested', 'condition')} for r in rows]}
    return rows, cases, manifest


class AnalysisTests(unittest.TestCase):
    def test_saturation_is_not_evidence_of_equivalence(self):
        rows, cases, manifest = data()
        result = summarize(rows, cases, manifest, ARMS, MODELS)
        self.assertEqual(result['decision'], 'stop_screen_saturated_no_effectiveness_conclusion')
        self.assertEqual(result['fully_paired_blocks'], 24)
        self.assertEqual(result['luna_minus_astra_skill_increment']['mean'], 0)

    def test_missing_attempts_are_pending_not_errors(self):
        rows, cases, manifest = data()
        rows.pop()
        result = summarize(rows, cases, manifest, ARMS, MODELS)
        self.assertEqual(result['pending'], 1)
        self.assertEqual(result['fully_paired_blocks'], 23)
        self.assertFalse(result['gate_eligible'])
        self.assertEqual(result['conditions']['gpt-6-luna']['C_skill']['pass_rate_attempted'], 1)

    def test_interaction_and_gate_require_multiple_families(self):
        rows, cases, manifest = data()
        for row in rows:
            if (row['model_requested'] == 'gpt-6-luna' and row['condition'] != 'C_skill'
                    and cases[row['case_id']]['variant'] == 'incident'
                    and cases[row['case_id']]['family'] in ('0', '1')):
                row['acceptance']['passed'] = False
        result = summarize(rows, cases, manifest, ARMS, MODELS)
        self.assertAlmostEqual(result['luna_minus_astra_skill_increment']['mean'], 4/24)
        self.assertTrue(result['development_gates']['gpt-6-luna']['skill_confirmatory_candidate'])
        # A single normal regression blocks this otherwise promising candidate.
        bad = copy.deepcopy(rows)
        next(r for r in bad if r['model_requested'] == 'gpt-6-luna' and r['condition'] == 'C_skill' and cases[r['case_id']]['variant'] == 'normal')['acceptance']['passed'] = False
        result = summarize(bad, cases, manifest, ARMS, MODELS)
        self.assertFalse(result['development_gates']['gpt-6-luna']['skill_confirmatory_candidate'])

    def test_normal_source_missing_blocks_otherwise_positive_skill_gate(self):
        rows, cases, manifest = data()
        for row in rows:
            if (row['model_requested'] == 'gpt-6-luna' and row['condition'] != 'C_skill'
                    and cases[row['case_id']]['variant'] == 'incident'
                    and cases[row['case_id']]['family'] in ('0', '1')):
                row['acceptance']['passed'] = False
        row = next(r for r in rows if r['model_requested'] == 'gpt-6-luna'
                   and r['condition'] == 'C_skill' and cases[r['case_id']]['variant'] == 'normal')
        row['source_changed'] = None
        row['acceptance']['passed'] = False
        result = summarize(rows, cases, manifest, ARMS, MODELS)
        self.assertEqual(result['conditions']['gpt-6-luna']['C_skill']['normal_behavior_unavailable'], 1)
        self.assertFalse(result['development_gates']['gpt-6-luna']['skill_confirmatory_candidate'])

    def test_non_object_prediction_counts_as_missing_verdict(self):
        for invalid in ([1], 'invalid', 7, True, None):
            with self.subTest(value=invalid):
                rows, cases, manifest = data()
                row = next(r for r in rows if cases[r['case_id']]['variant'] == 'normal')
                row.update(status='invalid_output', prediction=invalid)
                result = summarize(rows, cases, manifest, ARMS, MODELS)
                group = result['conditions'][row['model_requested']][row['condition']]
                self.assertEqual(group['normal_missing_verdict'], 1)
                self.assertEqual(group['passed'], 23)

    def test_source_passing_after_failed_run_is_not_success(self):
        rows, cases, manifest = data()
        rows[0]['status'] = 'infrastructure_failure'
        rows[0]['infra_kind'] = 'quota'
        result = summarize(rows, cases, manifest, ARMS, MODELS)
        group = result['conditions']['gpt-6-astra']['A_tools']
        self.assertEqual((group['passed'], group['source_behavior_passed']), (23, 24))
        self.assertFalse(result['gate_eligible'])

    def test_missing_normal_source_is_unknown(self):
        rows, cases, manifest = data()
        row = next(r for r in rows if cases[r['case_id']]['variant'] == 'normal')
        row.update(status='infrastructure_failure', infra_kind='runner_error', source_changed=None)
        row['acceptance']['passed'] = False
        result = summarize(rows, cases, manifest, ARMS, MODELS)
        group = result['conditions'][row['model_requested']][row['condition']]
        self.assertEqual(group['normal_source_missing'], 1)
        self.assertEqual(group['normal_source_changes'], 0)
        self.assertEqual(group['normal_behavior_unavailable'], 1)
        self.assertEqual(group['normal_behavior_regressions'], 0)

    def test_partial_pairing_keeps_cases_equally_weighted(self):
        rows, cases, manifest = data()
        # Drop one cell: the entire repeat is excluded from paired estimates.
        rows = [r for r in rows if not (r['case_id'] == '0-incident' and r['trial'] == 2
                                       and r['model_requested'] == 'gpt-6-luna' and r['condition'] == 'C_skill')]
        for r in rows:
            if r['case_id'] == '0-incident' and r['condition'] == 'A_tools':
                r['acceptance']['passed'] = False
        result = summarize(rows, cases, manifest, ARMS, MODELS)
        self.assertAlmostEqual(result['paired_family_pass_rates']['0']['gpt-6-luna']['A_tools'], 2/3)


if __name__ == '__main__':
    unittest.main()
