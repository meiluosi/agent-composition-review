"""Offline checks for pairing, answer isolation, fail-closed grading and scheduling."""
import copy
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_model_interaction as runner
from .grading import validate_result, behavioral_signature


class ProtocolTests(unittest.TestCase):
    def test_schedule_is_complete_paired_and_reproducible(self):
        cases = [{'case_id': f'm{i:02d}'} for i in range(1, 13)]
        jobs = runner.schedule(cases)
        self.assertEqual(len(jobs), 144)
        self.assertEqual(len({runner.digest(j) for j in jobs}), 144)
        self.assertEqual(jobs, runner.schedule(cases))
        for trial in (1, 2):
            for case in cases:
                pair = [j for j in jobs if j['case_id'] == case['case_id'] and j['trial'] == trial]
                self.assertEqual({(j['model_requested'], j['condition']) for j in pair},
                                 {(m, a) for m in runner.MODELS for a in runner.ARMS})

    def test_workspace_hides_labels_acceptance_and_prescribed_model(self):
        case = {'source': 'x=1\n', 'request': 'Keep correct behavior.', 'smoke': 'assert True\n',
                'family': 'secret_family', 'variant': 'incident', 'test_names': ['hidden_name']}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            protected = runner.stage_task(path, case)
            self.assertEqual({p.name for p in path.iterdir()},
                             {'app.py', 'original.py', 'task.md', 'smoke.py', 'run_python'})
            self.assertNotIn('app.py', protected)
            all_text = '\n'.join(p.read_text() for p in path.iterdir())
            self.assertNotIn('hidden_name', all_text)
            self.assertNotIn('secret_family', all_text)
            self.assertNotIn('MODEL_FORMAT', all_text)
            self.assertNotIn('model.json', all_text)

    def test_python_helper_excludes_site_packages(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            runner.stage_task(stage, {'source': 'pass', 'request': 'fix', 'smoke': 'pass'})
            result = subprocess.run([str(stage / 'run_python'), '-c',
                "import sys; assert 'site' not in sys.modules; import json, dataclasses; print('stdlib-ready')"],
                cwd=folder, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'stdlib-ready')

    def test_all_arms_share_identical_tools_and_only_requested_guidance(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / 'run'
            for arm in runner.ARMS:
                snap = run / 'snapshots' / arm
                (snap / 'scripts').mkdir(parents=True)
                (snap / 'scripts/composition.py').write_text('same tool')
                (snap / 'TOOLS.md').write_text('same explanation')
                if arm == 'B_checklist':
                    (snap / 'CHECKLIST.md').write_text('ordinary checklist')
                if arm == 'C_skill':
                    (snap / 'SKILL.md').write_text('special guidance')
            with patch.object(runner, 'RUN', run):
                hashes = []
                for arm in runner.ARMS:
                    stage = Path(folder) / arm
                    stage.mkdir()
                    protected = runner.stage_task(stage, {'source': 'pass', 'request': 'fix', 'smoke': 'pass'}, arm)
                    hashes.append(protected['scripts/composition.py'])
                    self.assertEqual((stage / 'CHECKLIST.md').exists(), arm == 'B_checklist')
                    self.assertEqual((stage / 'SKILL.md').exists(), arm == 'C_skill')
                self.assertEqual(len(set(hashes)), 1)

    def test_grader_rejects_missing_duplicate_skipped_and_process_failure(self):
        valid = {'passed': True, 'tests_run': 2, 'checks': [
            {'test': 'a', 'passed': True}, {'test': 'b', 'passed': True}]}
        self.assertTrue(validate_result(copy.deepcopy(valid), 2, ['a', 'b'], 0, False)['passed'])
        mutations = []
        missing = copy.deepcopy(valid); missing['checks'].pop(); mutations.append(missing)
        duplicate = copy.deepcopy(valid); duplicate['checks'][1]['test'] = 'a'; mutations.append(duplicate)
        skipped = copy.deepcopy(valid); skipped['checks'][0]['skipped'] = True; mutations.append(skipped)
        malformed = copy.deepcopy(valid); malformed['tests_run'] = True; mutations.append(malformed)
        for value in mutations:
            self.assertFalse(validate_result(value, 2, ['a', 'b'], 0, False)['passed'])
        for code, timeout in [(1, False), (0, True)]:
            self.assertFalse(validate_result(copy.deepcopy(valid), 2, ['a', 'b'], code, timeout)['passed'])
        self.assertFalse(validate_result([], 2, ['a', 'b'], 0, False)['passed'])

    def test_artifact_escape_and_protected_change_are_invalid(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); stage = root / 'stage'; stage.mkdir(); out = root / 'out'
            protected = runner.stage_task(stage, {'source': 'pass', 'request': 'fix', 'smoke': 'pass'})
            (stage / 'task.md').write_text('changed')
            secret = root / 'outside.py'; secret.write_text('private')
            (stage / 'leak.py').symlink_to(secret)
            changed, artifacts, errors = runner.collect_artifacts(stage, out, protected)
            self.assertIn('task.md', changed)
            self.assertNotIn('leak.py', artifacts)
            self.assertTrue(any('leak.py' in error for error in errors))
            self.assertIn('app.py', artifacts)

    def test_infrastructure_failure_is_distinct_from_task_timeout(self):
        self.assertEqual(runner.infrastructure_kind({'events': [{'type': 'turn.failed', 'error': 'usage limit reached'}]}, ''), 'quota')
        self.assertIsNone(runner.infrastructure_kind({'events': [], 'timed_out': True, 'completed': False}, ''))
        self.assertIsNone(runner.infrastructure_kind({'events': [], 'timed_out': False, 'completed': True}, ''))
        self.assertEqual(runner.infrastructure_kind({'events': [], 'completed': False}, 'connection refused'), 'transport')
        self.assertIsNone(runner.infrastructure_kind({'events': [], 'completed': True, 'exit_code': 0}, 'Warning: optional feature not supported'))

    def test_existing_marker_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'attempt'; out.mkdir(); (out / 'started.json').write_text('original')
            with self.assertRaises(RuntimeError):
                runner.execute({}, {}, {}, out, '', Path(folder) / 'schema')
            self.assertEqual((out / 'started.json').read_text(), 'original')

    def test_runner_exception_retains_uniform_failure_record(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runner, 'invoke', side_effect=OSError('synthetic launch error')):
            result = runner.execute(
                {'case_id': 'm01', 'model_requested': 'gpt-6-astra', 'condition': 'A_tools', 'trial': 1},
                {'source': 'pass', 'request': 'fix', 'smoke': 'pass'},
                {'timeout_seconds': 1, 'cli_version': 'test'}, Path(folder) / 'attempt',
                'prompt', Path(folder) / 'schema')
        self.assertEqual(result['infra_kind'], 'runner_error')
        self.assertEqual(result['status'], 'infrastructure_failure')
        for name in ('artifacts', 'source_changed', 'usage', 'completed', 'exit_code',
                     'case_sha256', 'prompt_sha256', 'tool_audit', 'prediction', 'acceptance'):
            self.assertIn(name, result)
        self.assertFalse(result['acceptance']['passed'])

    def test_restart_cannot_bypass_infrastructure_circuit_breaker(self):
        job = {'case_id': 'm01', 'condition': 'A_tools', 'trial': 1, 'model_requested': 'gpt-6-astra'}
        with tempfile.TemporaryDirectory() as folder, patch.object(runner, 'RUN', Path(folder)), \
             patch.object(runner, 'verify', return_value=({'schedule': [job], 'concurrency': 3}, {'m01': {}})), \
             patch.object(runner, 'preflight_ready'), patch.object(runner, 'run_one') as execute:
            runner.write(Path(folder) / 'raw-runs' / runner.digest(job)[:20] / 'record.json', {'infra_kind': 'quota'})
            with self.assertRaisesRegex(RuntimeError, 'Existing infrastructure failure'):
                runner.run()
            execute.assert_not_called()

    def test_public_attempt_without_raw_logs_is_not_retried(self):
        job = {'case_id': 'm01', 'condition': 'A_tools', 'trial': 1, 'model_requested': 'gpt-6-astra'}
        with tempfile.TemporaryDirectory() as folder, patch.object(runner, 'RUN', Path(folder)), \
             patch.object(runner, 'verify', return_value=({'schedule': [job], 'concurrency': 3}, {'m01': {}})), \
             patch.object(runner, 'preflight_ready'), patch.object(runner, 'run_one') as execute:
            runner.write(Path(folder) / 'results' / runner.digest(job)[:20] / 'record.json', {'infra_kind': None, 'status': 'completed'})
            runner.run()
            execute.assert_not_called()

    def test_scheduler_stops_new_work_after_infrastructure_failure(self):
        jobs = [{'case_id': f'm{i}', 'condition': 'A_tools', 'trial': 1, 'model_requested': 'gpt-6-astra'} for i in range(9)]
        seen = []
        def fake_run(job, case, manifest):
            seen.append(job)
            return {**job, 'status': 'infrastructure_failure', 'infra_kind': 'quota', 'acceptance': {'passed': False}}
        with tempfile.TemporaryDirectory() as folder, patch.object(runner, 'RUN', Path(folder)), \
             patch.object(runner, 'verify', return_value=({'schedule': jobs, 'concurrency': 3}, {j['case_id']: {} for j in jobs})), \
             patch.object(runner, 'preflight_ready'), patch.object(runner, 'run_one', side_effect=fake_run):
            runner.run()
        self.assertGreaterEqual(len(seen), 1)
        self.assertLessEqual(len(seen), 3)


if __name__ == '__main__':
    unittest.main()
