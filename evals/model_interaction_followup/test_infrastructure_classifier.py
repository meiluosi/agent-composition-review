"""Offline classifier and no-retry/circuit-breaker regressions; no model calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import resume_model_interaction as recovery


class ClassifierTests(unittest.TestCase):
    def meta(self, **values):
        return {'events': [], 'exit_code': -9, 'timed_out': True, 'completed': True, **values}

    def test_numeric_filenames_timestamps_and_counts_are_not_http_errors(self):
        for token in ('1791096884019646000.sh', '/tmp/429.py', 'snapshot-503.json', 'tokens=1401503',
                      '2026-10-04T07:04:29.503Z', 'HTTP snapshots/401.sh', 'status_file_503.py'):
            self.assertIsNone(recovery.infrastructure_kind(self.meta(), 'WARN failed to delete ' + token), token)

    def test_http_and_status_contexts_still_stop_for_real_service_errors(self):
        for text, kind in [('HTTP 401 Unauthorized', 'auth'), ('HTTP/1.1 403 Forbidden', 'auth'),
                           ('status code: 429', 'quota'), ('"status_code": 503', 'service'),
                           ('status=500', 'service'), ('HTTPStatusError: 502', 'service'),
                           ('HTTP status code 504', 'service'), ('{"status": "401"}', 'auth')]:
            self.assertEqual(recovery.infrastructure_kind(self.meta(), text), kind, text)

    def test_real_error_words_without_numeric_codes_still_stop(self):
        for text, kind in [('usage limit reached', 'quota'), ('rate limit exceeded', 'quota'),
                           ('quota_exceeded', 'quota'), ('authentication failed', 'auth'),
                           ('login required', 'auth'), ('stream disconnected', 'transport'),
                           ('error sending request', 'transport'), ('service unavailable', 'service'),
                           ('requested model abc is not supported', 'service')]:
            self.assertEqual(recovery.infrastructure_kind(self.meta(), text), kind, text)

    def test_structured_unknown_terminal_errors_remain_fail_closed(self):
        meta = self.meta(events=[{'type': 'turn.failed', 'error': {'message': 'Unclassified backend problem'}}])
        self.assertEqual(recovery.infrastructure_kind(meta, ''), 'service')
        meta = self.meta(events=[{'type': 'error', 'status_code': 429}])
        self.assertEqual(recovery.infrastructure_kind(meta, ''), 'quota')
        self.assertEqual(recovery.infrastructure_kind(self.meta(completed=False, timed_out=False), ''), 'service')

    def test_successful_turn_ignores_nonterminal_warnings(self):
        self.assertIsNone(recovery.infrastructure_kind(self.meta(exit_code=0, timed_out=False), 'WARN HTTP 401 cache refresh warning'))

    def test_real_timeout_has_no_infrastructure_diagnostic(self):
        self.assertIsNone(recovery.infrastructure_kind(self.meta(completed=False), 'WARN local cleanup failed'))
        self.assertIsNone(recovery.infrastructure_kind(self.meta(), 'failed to refresh available models: request timed out'))

    def test_original_exception_public_bytes_are_unchanged(self):
        # Portable test uses committed public provenance; full local raw matching
        # is mandatory in recovery.freeze()/run(), but not available in CI clones.
        row = recovery.check_known_exception(require_raw=False)
        self.assertEqual(row['status'], 'infrastructure_failure')
        self.assertEqual(row['infra_kind'], 'auth')
        self.assertTrue(row['timed_out'])
        self.assertIsNone(row['prediction'])

    def test_already_started_attempt_cannot_be_retried(self):
        job = recovery.EXCEPTION_JOB
        key = recovery.base.digest(job)[:20]
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery, 'RUN', Path(directory)), \
             patch.object(recovery.base, 'execute') as execute:
            (Path(directory) / 'raw-runs' / key).mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'already started'):
                recovery.run_one(job, {}, {}, 'unused')
            execute.assert_not_called()

    def test_unpublished_marker_blocks_continuation(self):
        job = recovery.EXCEPTION_JOB
        key = recovery.base.digest(job)[:20]
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery, 'RUN', Path(directory)):
            (Path(directory) / 'raw-runs' / key).mkdir(parents=True)
            with self.assertRaises(ValueError):
                recovery.pending_jobs({'schedule': [job]}, 'manifest-sha')

    def test_new_real_infrastructure_failure_cannot_be_waived(self):
        job = recovery.EXCEPTION_JOB
        row = {**job, 'canary': False, 'execution_phase': recovery.PHASE,
               'recovery_manifest_sha256': 'manifest-sha', 'infra_kind': 'quota', 'status': 'infrastructure_failure'}
        key = recovery.base.digest(job)[:20]
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery, 'RUN', Path(directory)), \
             patch.object(recovery, 'record_pair', return_value=row):
            (Path(directory) / 'raw-runs' / key).mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'genuine recovery-phase'):
                recovery.pending_jobs({'schedule': [job]}, 'manifest-sha')

    def phase_fixture(self, root, *, infra=None):
        job = recovery.EXCEPTION_JOB
        key = recovery.base.digest(job)[:20]
        folder = root / 'results' / key
        folder.mkdir(parents=True)
        row = {**job, 'canary': False, 'execution_phase': recovery.PHASE,
               'recovery_manifest_sha256': 'manifest-sha', 'infra_kind': infra,
               'status': 'infrastructure_failure' if infra else 'completed'}
        marker = {**job, 'canary': False, 'time': '2026-10-04T08:00:00+00:00'}
        (folder / 'record.json').write_text(json.dumps(row))
        (folder / 'started.json').write_text(json.dumps(marker))
        return job, folder, row, marker

    def test_offline_verification_requires_correct_phase_and_manifest(self):
        for field, value in [('execution_phase', None), ('execution_phase', 'wrong'),
                             ('recovery_manifest_sha256', None), ('recovery_manifest_sha256', 'wrong')]:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as directory, \
                 patch.object(recovery, 'RUN', Path(directory)), patch.object(recovery, 'load_rows') as reader:
                job, folder, row, _ = self.phase_fixture(Path(directory))
                if value is None:
                    row.pop(field)
                else:
                    row[field] = value
                (folder / 'record.json').write_text(json.dumps(row))
                with self.assertRaisesRegex(ValueError, 'phase provenance'):
                    recovery.verify_recovery_records({'schedule': [job]}, {}, {}, 'manifest-sha', check_raw=False)
                reader.assert_not_called()

    def test_offline_verifies_genuine_infra_without_raw_but_does_not_resume_it(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery, 'RUN', Path(directory)):
            job, folder, row, _ = self.phase_fixture(Path(directory), infra='quota')
            with patch.object(recovery, 'load_rows', return_value=[row]) as reader:
                count = recovery.verify_recovery_records({'schedule': [job]}, {}, {}, 'manifest-sha', check_raw=False)
            self.assertEqual(count, 1)
            self.assertFalse((Path(directory) / 'raw-runs').exists())
            self.assertFalse(reader.call_args.kwargs['regrade'])
            with patch.object(recovery, 'record_pair', return_value=row):
                with self.assertRaisesRegex(ValueError, 'genuine recovery-phase'):
                    recovery.pending_jobs({'schedule': [job]}, 'manifest-sha')

    def test_offline_checks_public_start_identity(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery, 'RUN', Path(directory)), \
             patch.object(recovery, 'load_rows') as reader:
            job, folder, row, marker = self.phase_fixture(Path(directory))
            marker['trial'] = 999
            (folder / 'started.json').write_text(json.dumps(marker))
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                recovery.verify_recovery_records({'schedule': [job]}, {}, {}, 'manifest-sha', check_raw=False)
            reader.assert_not_called()

    def test_local_verification_rejects_unpublished_missing_raw_and_mismatch(self):
        job = recovery.EXCEPTION_JOB
        key = recovery.base.digest(job)[:20]
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery, 'RUN', Path(directory)):
            root = Path(directory)
            raw = root / 'raw-runs' / key
            raw.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'publication is incomplete'):
                recovery.verify_recovery_records({'schedule': [job]}, {}, {}, 'manifest-sha', check_raw=True)
            job, public, row, marker = self.phase_fixture(root)
            with self.assertRaisesRegex(ValueError, 'Missing or invalid evidence'):
                recovery.verify_recovery_records({'schedule': [job]}, {}, {}, 'manifest-sha', check_raw=True)
            (raw / 'record.json').write_text(json.dumps({**row, 'status': 'changed'}))
            (raw / 'started.json').write_text(json.dumps(marker))
            with self.assertRaisesRegex(ValueError, 'Raw/public evidence mismatch'):
                recovery.verify_recovery_records({'schedule': [job]}, {}, {}, 'manifest-sha', check_raw=True)

    def test_phase_provenance_is_written_before_publication(self):
        job = recovery.EXCEPTION_JOB
        key = recovery.base.digest(job)[:20]
        def fake_execute(job, case, original, raw, *args):
            raw.mkdir(parents=True)
            return {**job, 'status': 'task_timeout', 'infra_kind': None}
        def fake_publish(raw, public):
            value = json.loads((raw / 'record.json').read_text())
            self.assertEqual(value['execution_phase'], recovery.PHASE)
            self.assertEqual(value['recovery_manifest_sha256'], 'manifest-sha')
            self.assertEqual(value['status'], 'task_timeout')
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery, 'RUN', Path(directory)), \
             patch.object(recovery.base, 'execute', side_effect=fake_execute), \
             patch.object(recovery.base, 'guidance_prompt', return_value='unchanged prompt'), \
             patch.object(recovery.base, 'publish', side_effect=fake_publish) as publish:
            result = recovery.run_one(job, {}, {}, 'manifest-sha')
            publish.assert_called_once()
            self.assertEqual(result['status'], 'task_timeout')

    def test_scheduler_stops_after_real_infra_and_restores_classifier(self):
        jobs = [{**recovery.EXCEPTION_JOB, 'case_id': 'synthetic-' + str(i)} for i in range(8)]
        started = []
        original_classifier = recovery.base.infrastructure_kind
        def fake_job(job, case, original, manifest_sha):
            self.assertIs(recovery.base.infrastructure_kind, recovery.infrastructure_kind)
            started.append(job)
            return {**job, 'status': 'infrastructure_failure', 'infra_kind': 'quota', 'acceptance': {'passed': False}}
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text('{}')
            with patch.object(recovery, 'MANIFEST', manifest), \
                 patch.object(recovery, 'verify', return_value=({'schedule': jobs}, {'schedule': jobs, 'concurrency': 3}, {j['case_id']: {} for j in jobs})), \
                 patch.object(recovery, 'pending_jobs', return_value=jobs), \
                 patch.object(recovery, 'run_one', side_effect=fake_job):
                recovery.run()
        self.assertGreaterEqual(len(started), 1)
        self.assertLessEqual(len(started), 3)
        self.assertIs(recovery.base.infrastructure_kind, original_classifier)

    def test_recovery_keeps_existing_record_and_skips_it(self):
        job = recovery.EXCEPTION_JOB
        row = {**job, 'canary': False, 'execution_phase': recovery.PHASE,
               'recovery_manifest_sha256': 'manifest-sha', 'infra_kind': None, 'status': 'task_timeout'}
        key = recovery.base.digest(job)[:20]
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery, 'RUN', Path(directory)), \
             patch.object(recovery, 'record_pair', return_value=row):
            (Path(directory) / 'raw-runs' / key).mkdir(parents=True)
            self.assertEqual(recovery.pending_jobs({'schedule': [job]}, 'manifest-sha'), [])
            self.assertEqual(row['status'], 'task_timeout')


if __name__ == '__main__':
    unittest.main()
