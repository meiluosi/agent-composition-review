#!/usr/bin/env python3
"""Read-only integrity snapshot of published jobs and optional local raw evidence.

No model calls, source execution, regrading, or effectiveness judgments. Prefer a
stopped run or a completed batch: publication is not atomic, so an incomplete
folder during execution is reported as deferred, not declared corrupt.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

# Importing the frozen readers should not create bytecode files during an audit.
sys.dont_write_bytecode = True

from run_model_interaction import ROOT, RUN, digest, verify
from analyze_model_interaction import load_rows

KIND = 'model_interaction_public_and_local_raw_integrity'
IDENTITY_KEYS = ('model_requested', 'case_id', 'condition', 'trial')


def relative(path):
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_json(path):
    data = path.read_bytes()
    return json.loads(data), digest(data)


def check_identity(value, job, label):
    if not isinstance(value, dict) or any(value.get(key) != job[key] for key in IDENTITY_KEYS):
        raise ValueError(label + ': scheduled identity mismatch')


def check_started(value, job, label):
    check_identity(value, job, label)
    if value.get('canary') is not False:
        raise ValueError(label + ': formal attempt must have canary=false')
    timestamp = value.get('time')
    if not isinstance(timestamp, str) or datetime.fromisoformat(timestamp).tzinfo is None:
        raise ValueError(label + ': missing or invalid timezone-aware start time')


def raw_audit(folder, row, record_sha, started_sha):
    if not folder.exists():
        return {'availability': 'absent', 'verified': False,
                'note': 'Local raw evidence unavailable; hashes were not independently checked.'}, []
    checks, errors = {}, []
    if folder.is_symlink() or not folder.is_dir():
        return {'availability': 'present_invalid_directory', 'verified': False}, ['raw directory is not a regular directory']
    for name, expected in [('record.json', record_sha), ('started.json', started_sha),
                           ('events.jsonl', row.get('event_sha256')), ('stderr.txt', row.get('stderr_sha256'))]:
        path = folder / name
        if expected is None and not path.exists():
            checks[name] = {'state': 'not_produced_as_recorded', 'verified': False}
            continue
        if not path.is_file() or path.is_symlink():
            checks[name] = {'state': 'missing_or_invalid', 'verified': False}
            errors.append('raw evidence missing or invalid: ' + name)
            continue
        actual = digest(path.read_bytes())
        matches = expected is not None and actual == expected
        checks[name] = {'state': 'matched' if matches else 'mismatch', 'sha256': actual, 'verified': matches}
        if not matches:
            errors.append('raw evidence hash mismatch: ' + name)
    return {'availability': 'present', 'verified': not errors, 'files': checks}, errors


def audit(require_complete=False):
    started_at = datetime.now(timezone.utc).isoformat()
    manifest, cases = verify(check_cli=False)
    scheduled = {digest(job)[:20]: job for job in manifest['schedule']}
    if len(scheduled) != len(manifest['schedule']):
        raise ValueError('Schedule digest collision or duplicate attempt')
    result_root = RUN / 'results'
    unexpected = []
    if result_root.exists():
        for path in sorted(result_root.iterdir()):
            if path.name not in scheduled or not path.is_dir() or path.is_symlink():
                unexpected.append(relative(path))
    errors = [{'scope': 'results_inventory', 'error': 'Unexpected or invalid results entry', 'path': path}
              for path in unexpected]
    entries, verified_rows, flags, violations = [], [], [], []
    deferred = []
    for key, job in scheduled.items():
        public = result_root / key
        raw = RUN / 'raw-runs' / key
        item = {**job, 'job_digest': key, 'record_path': relative(public / 'record.json'),
                'record_sha256': None, 'public_verified': False, 'raw': {'availability': 'not_checked', 'verified': False}}
        record_path = public / 'record.json'
        if not record_path.exists():
            if public.exists():
                item['snapshot_state'] = 'publication_incomplete_or_interrupted'
            elif (raw / 'record.json').exists():
                item['snapshot_state'] = 'raw_finished_awaiting_publication'
            elif raw.exists():
                item['snapshot_state'] = 'started_unpublished_or_interrupted'
            else:
                item['snapshot_state'] = 'pending'
            if (raw / 'started.json').is_file() and not (raw / 'started.json').is_symlink():
                try:
                    marker, marker_sha = load_json(raw / 'started.json')
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    item['deferred_reason'] = 'Start marker unreadable or incomplete: ' + str(exc)
                    deferred.append({**job, 'reason': item['deferred_reason']})
                else:
                    try:
                        check_started(marker, job, 'raw started.json')
                        item['raw_started_sha256'] = marker_sha
                        item['started_at'] = marker['time']
                    except (ValueError, TypeError, KeyError) as exc:
                        errors.append({**job, 'scope': 'raw_started', 'error': str(exc)})
            # In-flight raw files change while the model runs; do not hash them as final evidence.
            item['raw']['availability'] = 'present_unpublished' if raw.exists() else 'absent'
            if item['snapshot_state'] != 'pending':
                item['note'] = 'Start files do not prove a process is still alive. Check again after the batch stops.'
            entries.append(item)
            continue
        if record_path.is_symlink() or public.is_symlink():
            item['snapshot_state'] = 'verification_failed'
            errors.append({**job, 'error': 'Public record or job directory is a symlink'})
            entries.append(item)
            continue
        try:
            row, record_sha = load_json(record_path)
            item['record_sha256'] = record_sha
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # record.json is copied before other publication files and can be mid-write.
            item['snapshot_state'] = 'publication_unreadable_or_interrupted'
            item['deferred_reason'] = str(exc)
            deferred.append({**job, 'reason': str(exc)})
            entries.append(item)
            continue
        try:
            check_identity(row, job, 'public record.json')
        except (ValueError, TypeError, KeyError) as exc:
            item['snapshot_state'] = 'verification_failed'
            errors.append({**job, 'scope': 'public_identity', 'error': str(exc)})
            entries.append(item)
            continue
        # Missing publication files are not evidence of corruption while a run is active.
        expected_files = ['started.json', 'patch.diff']
        artifacts = row.get('artifacts')
        if not isinstance(artifacts, dict):
            item['snapshot_state'] = 'verification_failed'
            errors.append({**job, 'error': 'Public artifacts map is invalid'})
            entries.append(item)
            continue
        if any(not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts for name in artifacts):
            item['snapshot_state'] = 'verification_failed'
            errors.append({**job, 'error': 'Invalid artifact path'})
            entries.append(item)
            continue
        # A runner error before patch generation legitimately has no patch.diff.
        if row.get('infra_kind') == 'runner_error':
            expected_files.remove('patch.diff')
        expected_files += ['artifacts/' + name for name in artifacts]
        missing = [name for name in expected_files if not (public / name).is_file()]
        if missing:
            item['snapshot_state'] = 'publication_incomplete_or_interrupted'
            item['missing_public_files'] = missing
            deferred.append({**job, 'reason': 'Publication files missing', 'files': missing})
            entries.append(item)
            continue
        try:
            if (public / 'started.json').is_symlink():
                raise ValueError('Public started.json is a symlink')
            marker, marker_sha = load_json(public / 'started.json')
            check_started(marker, job, 'public started.json')
            item['started_at'] = marker['time']
            item['started_sha256'] = marker_sha
            # Keep the frozen reader as the authority for artifact and completion checks.
            # Restrict this read to a single ready job so a concurrent publication elsewhere
            # does not invalidate all completed records in the snapshot.
            loaded = load_rows(cases, {**manifest, 'schedule': [job]}, regrade=False)
            if len(loaded) != 1 or loaded[0] != row:
                raise ValueError('Record changed while being read')
            if digest(record_path.read_bytes()) != record_sha:
                raise ValueError('Record bytes changed while being read')
            item['public_verified'] = True
            item['snapshot_state'] = 'published'
            item['recorded_status'] = row.get('status')
            item['infra_kind'] = row.get('infra_kind')
            item['usage_missing'] = row.get('usage') is None
            evidence, raw_errors = raw_audit(raw, row, record_sha, marker_sha)
            item['raw'] = evidence
            for error in raw_errors:
                errors.append({**job, 'scope': 'local_raw', 'error': error})
            verified_rows.append(row)
            for field in ('protected_changes', 'outside_changes', 'artifact_errors'):
                values = row.get(field, [])
                if values:
                    violations.append({**job, 'job_digest': key, 'field': field, 'items': values})
            for index, command in enumerate(row.get('tool_audit', [])):
                if isinstance(command, dict) and command.get('review_flags'):
                    flags.append({**job, 'job_digest': key, 'record_path': item['record_path'],
                                  'tool_audit_index_zero_based': index,
                                  'review_flags': command['review_flags'], 'command': command.get('command'),
                                  'exit_code': command.get('exit_code')})
        except (AssertionError, OSError, ValueError, TypeError, KeyError) as exc:
            item['snapshot_state'] = 'verification_failed_or_concurrent_publication'
            errors.append({**job, 'scope': 'public_record', 'error': type(exc).__name__ + ': ' + str(exc),
                           'note': 'Repeat on a quiescent batch before diagnosing corruption; publication is not atomic.'})
        entries.append(item)
    counts = Counter(item['snapshot_state'] for item in entries)
    complete = len(verified_rows) == len(scheduled) and all(item['public_verified'] for item in entries)
    if require_complete and not complete:
        errors.append({'scope': 'completeness', 'error': '--require-complete requires every scheduled public record and its files to verify.'})
    raw_counts = Counter(item['raw']['availability'] for item in entries)
    return {
        'audit_kind': KIND, 'started_at': started_at, 'finished_at': datetime.now(timezone.utc).isoformat(),
        'script_sha256': digest(Path(__file__).read_bytes()),
        'manifest_sha256': digest((RUN / 'manifest.json').read_bytes()),
        'frozen_inputs_verified': True, 'regraded': False, 'model_calls': 0,
        'require_complete': require_complete, 'integrity_checks_passed': not errors,
        'all_scheduled_public_records_verified': complete,
        'snapshot_note': 'Best used after stopping execution or at a batch boundary. Started/unpublished is not proof of a live process; partial publication is deferred, not declared corrupt. No effects or task labels are judged.',
        'planned': len(scheduled), 'public_records_observed': sum(item['record_sha256'] is not None for item in entries),
        'public_records_verified': len(verified_rows), 'pending_not_started': counts.get('pending', 0),
        'unpublished_started_or_publication_incomplete': sum(count for state, count in counts.items() if state not in ('pending', 'published', 'verification_failed', 'verification_failed_or_concurrent_publication')),
        'snapshot_state_counts': dict(counts), 'recorded_status_counts': dict(Counter(row.get('status') for row in verified_rows)),
        'usage_missing_in_verified_records': sum(row.get('usage') is None for row in verified_rows),
        'raw_availability_counts': dict(raw_counts),
        'local_raw_records_verified': sum(item['raw']['verified'] for item in entries),
        'local_raw_unavailable_for_verified_public_records': sum(item['public_verified'] and item['raw']['availability'] == 'absent' for item in entries),
        'unexpected_results_entries': unexpected, 'deferred_publications': deferred,
        'workspace_violation_records': violations, 'commands_requiring_review': flags,
        'review_note': 'Flags only index existing recorded commands for maintainer review; no flag is itself a violation, and no flags do not prove confinement.',
        'errors': errors, 'jobs': entries,
    }, manifest


def save_output(path, report, manifest):
    path = path.expanduser().resolve()
    forbidden_files = {(ROOT / name).resolve() for name in manifest['files_sha256']}
    forbidden_files.add((RUN / 'manifest.json').resolve())
    if path in forbidden_files or path.suffix != '.json':
        raise ValueError('--output must name a non-frozen audit JSON file')
    for folder in ('results', 'raw-runs', 'preflight', 'snapshots'):
        if path.is_relative_to((RUN / folder).resolve()):
            raise ValueError('--output cannot write into evidence or frozen material directories')
    if path.exists():
        previous = json.loads(path.read_text())
        if not isinstance(previous, dict) or previous.get('audit_kind') != KIND:
            raise ValueError('--output refuses to overwrite a file not produced by this auditor')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Optional non-frozen audit JSON; stdout remains JSON. Existing auditor output may be replaced.')
    parser.add_argument('--require-complete', action='store_true', help='Fail unless all 144 scheduled public records verify; raw evidence is optional.')
    args = parser.parse_args()
    try:
        report, manifest = audit(args.require_complete)
        if args.output:
            save_output(args.output, report, manifest)
    except (AssertionError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({'audit_kind': KIND, 'integrity_checks_passed': False,
                          'error': type(exc).__name__ + ': ' + str(exc),
                          'note': 'Audit did not complete; do not infer evidence was verified.'}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['integrity_checks_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
