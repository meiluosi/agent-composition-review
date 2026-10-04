#!/usr/bin/env python3
"""Separately register recovery from one verified classifier false positive.

The original 144-job schedule, source/acceptance inputs and failed record remain
unchanged. This manager freezes only never-started jobs; it never retries a job.
"""
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
import run_model_interaction as base
from analyze_model_interaction import load_rows

ROOT, RUN = base.ROOT, base.RUN
PLAN = RUN / 'RECOVERY_PLAN.md'
MANIFEST = RUN / 'classifier-recovery-manifest.json'
TEST = ROOT / 'evals/model_interaction_followup/test_infrastructure_classifier.py'
PHASE = 'classifier_recovery'
EXPECTED_EXISTING = 64
EXPECTED_REMAINING = 80
EXCEPTION_KEY = 'f4e02ac31dd8396bcc69'
EXCEPTION_JOB = {'case_id': 'm01', 'condition': 'A_tools', 'trial': 1, 'model_requested': 'gpt-6-astra'}
EXCEPTION_HASHES = {
    'record.json': '6ec46b8ba64f3bba30bc22e60ceef07195208272d870276d0c1822a0d3e6484f',
    'events.jsonl': 'b71ae0fbf1107411062f24761a096a1dbcfc570f41ddbd128f77bb5cbc329e9c',
    'stderr.txt': 'fdbc13de5c5d6a88abb48dc7559cfb756f53619b4f25f014d7838e1c7148451e',
}
SNAPSHOT_BASENAME = '1791096884019646000.sh'
LEGACY_CLASSIFIER = base.infrastructure_kind
# A numeric substring in a path, timestamp or token count is not HTTP evidence.
STATUS_CONTEXT = re.compile(
    r'\b(?:http(?:/\d(?:\.\d)?)?(?:[ _-]+status(?:[ _-]+code)?)?'
    r'|https?(?:status(?:error)?)|status(?:[ _-]?code)?)'
    r'[\s:=\"\'()\[\],-]*([1-5]\d{2})(?!\d)', re.I)


def infrastructure_kind(meta, stderr):
    """Preserve circuit breaking while requiring actual diagnostic context."""
    if meta.get('completed') and meta.get('exit_code', 0) == 0:
        return None
    failures = [event for event in meta.get('events', [])
                if isinstance(event, dict) and event.get('type') in ('error', 'turn.failed')]
    text = (json.dumps(failures, ensure_ascii=False) + '\n' + stderr).lower()
    codes = {int(match.group(1)) for match in STATUS_CONTEXT.finditer(text)}
    categories = (
        ('quota', {429}, (
            r'\busage limit\b', r'\brate[ _-]?limit(?:ed|ing)?\b', r'\binsufficient_quota\b',
            r'\bquota[ _-](?:exceeded|exhausted|limit)\b', r'\btoo many requests\b')),
        ('auth', {401, 403}, (
            r'\bunauthori[sz]ed\b', r'\bunauthenticated\b',
            r'\bauthentication (?:failed|failure|error|required)\b',
            r'\b(?:invalid|expired|missing) (?:access token|api key|authentication token)\b',
            r'\blogin required\b', r'\bnot logged in\b', r'\bplease (?:log|sign) in\b')),
        ('transport', set(), (
            r'\berror sending request\b', r'\bconnection (?:refused|reset|closed)\b',
            r'\bdns error\b', r'\bnetwork is unreachable\b', r'\bstream disconnected\b',
            r'\bfailed to establish (?:a )?(?:connection|network|tls|ssl)\b')),
        ('service', {500, 502, 503, 504}, (
            r'\bservice unavailable\b', r'\binternal server error\b', r'\bbad gateway\b',
            r'\bmodel not found\b', r'\bmodel[^\n]{0,100}\b(?:not supported|does not exist)\b')),
    )
    for kind, statuses, patterns in categories:
        if codes & statuses or any(re.search(pattern, text) for pattern in patterns):
            return kind
    # Unknown terminal service errors still stop scheduling. A normal task timeout
    # without service diagnostics remains a task timeout, including this exception.
    if failures:
        return 'service'
    if not meta.get('completed') and not meta.get('timed_out'):
        return 'service'
    return None


def read_object(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing or invalid evidence file: ' + str(path))
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Expected an object: ' + str(path))
    return value


def identity(value, job, label):
    if any(value.get(key) != expected for key, expected in job.items()):
        raise ValueError(label + ': job identity mismatch')
    if value.get('canary') is not False:
        raise ValueError(label + ': not a formal attempt')


def record_pair(job):
    key = base.digest(job)[:20]
    public, raw = RUN / 'results' / key, RUN / 'raw-runs' / key
    if public.is_symlink() or raw.is_symlink():
        raise ValueError('Symlink attempt directory: ' + key)
    row = read_object(public / 'record.json')
    identity(row, job, 'public record')
    for name in ('record.json', 'started.json'):
        left, right = public / name, raw / name
        a, b = read_object(left), read_object(right)
        identity(a, job, 'public ' + name)
        identity(b, job, 'raw ' + name)
        if left.read_bytes() != right.read_bytes():
            raise ValueError('Raw/public evidence mismatch: ' + key + '/' + name)
    marker = read_object(public / 'started.json')
    if datetime.fromisoformat(marker['time']).tzinfo is None:
        raise ValueError('Start marker lacks timezone: ' + key)
    return row


def check_known_exception(require_raw=True):
    if base.digest(EXCEPTION_JOB)[:20] != EXCEPTION_KEY:
        raise ValueError('Exception job identity changed')
    public = RUN / 'results' / EXCEPTION_KEY
    raw = RUN / 'raw-runs' / EXCEPTION_KEY
    row = read_object(public / 'record.json')
    identity(row, EXCEPTION_JOB, 'known exception')
    if base.digest((public / 'record.json').read_bytes()) != EXCEPTION_HASHES['record.json']:
        raise ValueError('The one permitted exception record changed')
    expected = {'status': 'infrastructure_failure', 'infra_kind': 'auth', 'exit_code': -9,
                'timed_out': True, 'completed': True, 'prediction': None, 'terminal_errors': []}
    if any(row.get(key) != value for key, value in expected.items()):
        raise ValueError('Known exception is not the recorded timeout')
    if row.get('event_sha256') != EXCEPTION_HASHES['events.jsonl'] or row.get('stderr_sha256') != EXCEPTION_HASHES['stderr.txt']:
        raise ValueError('Known exception provenance hashes changed')
    if require_raw:
        for name, sha in EXCEPTION_HASHES.items():
            path = raw / name
            if path.is_symlink() or not path.is_file() or base.digest(path.read_bytes()) != sha:
                raise ValueError('Known exception raw evidence changed: ' + name)
        stderr = (raw / 'stderr.txt').read_text()
        events = [json.loads(line) for line in (raw / 'events.jsonl').read_text().splitlines() if line.strip()]
        if any(event.get('type') in ('error', 'turn.failed') for event in events):
            raise ValueError('Known exception contains a terminal service error')
        if not any(event.get('type') == 'turn.completed' for event in events):
            raise ValueError('Known exception lacks its recorded completion event')
        if stderr.count('401') != 1 or stderr.count(SNAPSHOT_BASENAME) != 1 or '401' in stderr.replace(SNAPSHOT_BASENAME, ''):
            raise ValueError('401 is not exclusively in the documented snapshot filename')
        meta = {**row, 'events': events}
        if LEGACY_CLASSIFIER(meta, stderr) != 'auth' or infrastructure_kind(meta, stderr) is not None:
            raise ValueError('Classifier explanation no longer matches the recorded evidence')
    return row


def file_hashes(paths):
    result = {}
    for path in paths:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError('Invalid frozen evidence path: ' + str(path))
        result[str(path.relative_to(ROOT))] = base.digest(path.read_bytes())
    return result


def freeze():
    if MANIFEST.exists():
        raise ValueError('Recovery is already frozen; do not overwrite it')
    if not PLAN.is_file() or not PLAN.read_text().strip() or not TEST.is_file():
        raise ValueError('RECOVERY_PLAN.md and classifier tests must exist before freezing')
    original, cases = base.verify()
    check_known_exception(require_raw=True)
    known_keys = {base.digest(job)[:20] for job in original['schedule']}
    if (RUN / 'results').exists():
        for path in (RUN / 'results').iterdir():
            if path.name not in known_keys or path.is_symlink() or not path.is_dir():
                raise ValueError('Unscheduled public result: ' + str(path))
    canary_keys = {'canary-' + base.digest({'model_requested': model, 'case_id': 'canary', 'condition': 'preflight', 'trial': 1})[:20]
                   for model in base.MODELS}
    if (RUN / 'raw-runs').exists():
        for path in (RUN / 'raw-runs').iterdir():
            if path.name not in known_keys | canary_keys or path.is_symlink() or not path.is_dir():
                raise ValueError('Unscheduled raw attempt: ' + str(path))
    completed, pending = [], []
    public_files, raw_files = [], []
    for job in original['schedule']:
        key = base.digest(job)[:20]
        public, raw = RUN / 'results' / key, RUN / 'raw-runs' / key
        if not public.exists() and not raw.exists():
            pending.append(job)
            continue
        row = record_pair(job)  # Any started/unpublished attempt blocks freezing.
        if row.get('infra_kind') and key != EXCEPTION_KEY:
            raise ValueError('Another infrastructure failure cannot be waived: ' + key)
        if row.get('status') == 'infrastructure_failure' and key != EXCEPTION_KEY:
            raise ValueError('Another infrastructure status cannot be waived: ' + key)
        completed.append(job)
        public_files.extend(base.files(public))
        for name, expected in [('events.jsonl', row.get('event_sha256')), ('stderr.txt', row.get('stderr_sha256'))]:
            path = raw / name
            if expected is None:
                if path.exists():
                    raise ValueError('Unhashed raw log exists: ' + key + '/' + name)
            elif not path.is_file() or path.is_symlink() or base.digest(path.read_bytes()) != expected:
                raise ValueError('Raw log does not match the existing record: ' + key + '/' + name)
            else:
                raw_files.append(path)
        raw_files.extend([raw / 'record.json', raw / 'started.json'])
    load_rows(cases, {**original, 'schedule': completed}, regrade=False)
    if len(completed) != EXPECTED_EXISTING or len(pending) != EXPECTED_REMAINING:
        raise ValueError('This recovery plan requires exactly 64 preserved attempts and 80 never-started jobs')
    manifest = {
        'kind': 'registered_numeric_classifier_false_positive_recovery',
        'execution_phase': PHASE, 'frozen_at': datetime.now(timezone.utc).isoformat(),
        'original_planned': len(original['schedule']), 'existing_attempts': completed, 'schedule': pending,
        'reason': 'One exact preserved timeout record was misclassified because 401 occurred inside a shell-snapshot timestamp filename.',
        'exception_job': EXCEPTION_JOB, 'exception_job_digest': EXCEPTION_KEY,
        'exception_evidence_sha256': EXCEPTION_HASHES,
        'original_record_preserved': True, 'retries_authorized': 0,
        'cli_version': original['cli_version'], 'python_version': original['python_version'],
        'execution_platform': original['execution_platform'], 'reasoning_effort': original['reasoning_effort'],
        'concurrency': original['concurrency'], 'timeout_seconds': original['timeout_seconds'],
        'files_sha256': file_hashes([Path(__file__), TEST, PLAN, RUN / 'manifest.json', *public_files]),
        'raw_files_sha256': file_hashes(raw_files),
    }
    with MANIFEST.open('x') as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'frozen': True, 'previous_attempts': len(completed), 'never_started_recovery_jobs': len(pending),
                      'original_schedule_total': len(original['schedule']), 'old_records_changed': 0}), flush=True)


def verify(check_cli=True, check_raw=True):
    original, cases = base.verify(check_cli=check_cli)
    recovery = read_object(MANIFEST)
    for field in ('cli_version', 'python_version', 'execution_platform', 'reasoning_effort', 'concurrency', 'timeout_seconds'):
        if recovery[field] != original[field]:
            raise ValueError('Recovery changed original execution setting: ' + field)
    if (recovery.get('execution_phase') != PHASE or recovery.get('retries_authorized') != 0
            or recovery.get('original_planned') != len(original['schedule'])
            or recovery.get('original_record_preserved') is not True
            or recovery.get('exception_job') != EXCEPTION_JOB
            or recovery.get('exception_job_digest') != EXCEPTION_KEY
            or recovery.get('exception_evidence_sha256') != EXCEPTION_HASHES):
        raise ValueError('Invalid recovery scope or exception provenance')
    for field in ['files_sha256'] + (['raw_files_sha256'] if check_raw else []):
        for name, expected in recovery[field].items():
            path = ROOT / name
            if path.is_symlink() or not path.is_file() or base.digest(path.read_bytes()) != expected:
                raise ValueError('Changed recovery-frozen evidence: ' + name)
    existing_keys = {base.digest(job)[:20] for job in recovery['existing_attempts']}
    expected_existing = [job for job in original['schedule'] if base.digest(job)[:20] in existing_keys]
    expected_remaining = [job for job in original['schedule'] if base.digest(job)[:20] not in existing_keys]
    if recovery['existing_attempts'] != expected_existing or recovery['schedule'] != expected_remaining:
        raise ValueError('Recovery is not the untouched remainder of the original schedule')
    if (len(recovery['existing_attempts']) != EXPECTED_EXISTING
            or len(recovery['schedule']) != EXPECTED_REMAINING
            or len(recovery['existing_attempts']) + len(recovery['schedule']) != len(original['schedule'])):
        raise ValueError('Recovery must preserve exactly 64 attempts and schedule exactly 80 untouched jobs')
    check_known_exception(require_raw=check_raw)
    verify_recovery_records(recovery, cases, original, base.digest(MANIFEST.read_bytes()), check_raw=check_raw)
    return recovery, original, cases



def verify_recovery_records(recovery, cases, original, manifest_sha, check_raw=True):
    """Validate published phase provenance; genuine infra is evidence, not invalidity.

    The run scheduler separately refuses to continue after any genuine infra.
    Offline readers do not require raw files, whereas local mode rejects any
    started attempt that has not completed publication.
    """
    published = []
    for job in recovery['schedule']:
        key = base.digest(job)[:20]
        public, raw = RUN / 'results' / key, RUN / 'raw-runs' / key
        if not public.exists():
            if check_raw and raw.exists():
                raise ValueError('Recovery attempt started but publication is incomplete: ' + key)
            continue
        if public.is_symlink() or not public.is_dir():
            raise ValueError('Invalid recovery public directory: ' + key)
        row = record_pair(job) if check_raw else read_object(public / 'record.json')
        identity(row, job, 'recovery public record')
        marker = read_object(public / 'started.json')
        identity(marker, job, 'recovery public start')
        if datetime.fromisoformat(marker['time']).tzinfo is None:
            raise ValueError('Recovery start marker lacks timezone: ' + key)
        if row.get('execution_phase') != PHASE or row.get('recovery_manifest_sha256') != manifest_sha:
            raise ValueError('Recovery record has wrong or missing phase provenance: ' + key)
        if check_raw:
            for name, expected in [('events.jsonl', row.get('event_sha256')), ('stderr.txt', row.get('stderr_sha256'))]:
                path = raw / name
                if expected is None:
                    if path.exists():
                        raise ValueError('Unhashed recovery raw log: ' + key + '/' + name)
                elif path.is_symlink() or not path.is_file() or base.digest(path.read_bytes()) != expected:
                    raise ValueError('Recovery raw log hash mismatch: ' + key + '/' + name)
        published.append(job)
    verified = load_rows(cases, {**original, 'schedule': published}, regrade=False)
    if len(verified) != len(published):
        raise ValueError('Recovery publication changed during verification')
    return len(verified)


def pending_jobs(recovery, manifest_sha):
    pending = []
    for job in recovery['schedule']:
        key = base.digest(job)[:20]
        public, raw = RUN / 'results' / key, RUN / 'raw-runs' / key
        if not public.exists() and not raw.exists():
            pending.append(job)
            continue
        row = record_pair(job)
        if row.get('execution_phase') != PHASE or row.get('recovery_manifest_sha256') != manifest_sha:
            raise ValueError('Existing recovery attempt has wrong or missing phase provenance: ' + key)
        if row.get('infra_kind') or row.get('status') == 'infrastructure_failure':
            raise ValueError('A genuine recovery-phase infrastructure failure stops further scheduling: ' + key)
    return pending


def run_one(job, case, original, manifest_sha):
    key = base.digest(job)[:20]
    raw, public = RUN / 'raw-runs' / key, RUN / 'results' / key
    if raw.exists() or public.exists():
        raise ValueError('Attempt already started; no recovery retry: ' + key)
    row = base.execute(job, case, original, raw, base.guidance_prompt(job['condition']),
                       RUN / 'prediction.schema.json', job['condition'])
    row['execution_phase'] = PHASE
    row['recovery_manifest_sha256'] = manifest_sha
    base.write(raw / 'record.json', row)
    base.publish(raw, public)
    return row


def run(limit=None):
    recovery, original, cases = verify(check_cli=True, check_raw=True)
    manifest_sha = base.digest(MANIFEST.read_bytes())
    pending = pending_jobs(recovery, manifest_sha)
    if limit is not None:
        if limit < 1:
            raise ValueError('--limit must be positive')
        pending = pending[:limit]
    print(json.dumps({'phase': PHASE, 'scheduled_this_call': len(pending),
                      'phase_planned': len(recovery['schedule']), 'original_planned': len(original['schedule'])}), flush=True)
    iterator = iter(pending)
    stopped, count = False, 0
    previous = base.infrastructure_kind
    # Explicit process-local replacement of exactly the faulty classifier; the
    # original source and all execution, prompts and grading remain unchanged.
    base.infrastructure_kind = infrastructure_kind
    try:
        with ThreadPoolExecutor(max_workers=original['concurrency']) as pool:
            active = {}
            def submit_next():
                job = next(iterator, None)
                if job is not None:
                    active[pool.submit(run_one, job, cases[job['case_id']], original, manifest_sha)] = job
            for _ in range(min(original['concurrency'], len(pending))):
                submit_next()
            while active:
                finished, _ = wait(active, return_when=FIRST_COMPLETED)
                finished_count = 0
                for future in finished:
                    job = active.pop(future)
                    try:
                        row = future.result()
                    except Exception as exc:
                        stopped = True
                        print(json.dumps({'phase': PHASE, **job, 'scheduler_error': str(exc), 'stop_scheduling': True}), flush=True)
                        continue
                    count += 1
                    finished_count += 1
                    if row.get('infra_kind') or row.get('status') == 'infrastructure_failure':
                        stopped = True
                    print(json.dumps({'phase': PHASE, 'finished_this_call': count,
                                      **{key: row.get(key) for key in (*job.keys(), 'status', 'infra_kind')},
                                      'passed': row['acceptance']['passed']}), flush=True)
                if not stopped:
                    for _ in range(finished_count):
                        submit_next()
    finally:
        base.infrastructure_kind = previous
    print(json.dumps({'phase': PHASE, 'finished_this_call': count, 'stopped_on_infrastructure': stopped}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze', 'verify', 'run'])
    parser.add_argument('--limit', type=int)
    parser.add_argument('--public-only', '--offline', dest='public_only', action='store_true', help='For offline verify only: skip unavailable local raw hashes and runtime version checks.')
    args = parser.parse_args()
    if args.public_only and args.action != 'verify':
        parser.error('--public-only is restricted to offline verify')
    if args.action == 'freeze':
        freeze()
    elif args.action == 'run':
        run(args.limit)
    else:
        recovery, original, _ = verify(check_cli=not args.public_only, check_raw=not args.public_only)
        print(json.dumps({'verified': True, 'local_raw_verified': not args.public_only,
                          'recovery_planned': len(recovery['schedule']), 'original_planned': len(original['schedule'])}))
