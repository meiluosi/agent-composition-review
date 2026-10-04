#!/usr/bin/env python3
"""Freeze and execute 144 paired model-by-guidance attempts without retries."""
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.model_interaction.catalog import cases
from evals.model_interaction.grading import behavioral_check, behavioral_signature, grade_source
from evals.model_interaction.protocol import FINAL_SCHEMA, final_valid, prompt

RUN = ROOT / 'evals/iterations/model-interaction-2026-10-04'
SOURCE = ROOT / 'candidates/v4/agent-composition-review'
MATERIAL = ROOT / 'evals/model_interaction'
ARMS = ['A_tools', 'B_checklist', 'C_skill']
MODELS = ['gpt-6-astra', 'gpt-6-luna']
DISABLED = ['plugins', 'apps', 'multi_agent', 'browser_use', 'computer_use', 'image_generation',
            'view_image', 'skill_search', 'hooks', 'unbounded_connection_retries']
SEED = 202610041
CANARY_REQUEST = '''Implement add(a, b) in app.py to return the sum of two numbers. Write the implementation and run ./run_python smoke.py. Only use this temporary workspace. Do not inspect other directories or network, install packages, or start models. Keep original.py, task.md, smoke.py and run_python unchanged. Return the requested JSON with initial_verdict=violation, summary, tests_executed and limitations.'''
CANARY_SOURCE = 'def add(a, b):\n    return a - b\n'
CANARY_SMOKE = 'from app import add\nassert add(7, 2) == 9\nassert add(-3, 1) == -2\nprint("canary passed")\n'
CANARY_ACCEPTANCE = '''import json,sys
from app import add
ok=add(7,2)==9 and add(-3,1)==-2
print(json.dumps({'passed':ok,'tests_run':1,'checks':[{'test':'sum','passed':ok}]}))
'''


def digest(value):
    payload = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def files(root):
    return sorted(p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')


def core(arm):
    return (RUN / 'snapshots/C_skill/SKILL.md').read_text() if arm == 'C_skill' else ''


def guidance_prompt(arm):
    checklist = (RUN / 'snapshots/B_checklist/CHECKLIST.md').read_text() if arm == 'B_checklist' else ''
    return prompt(arm, core(arm), checklist)


def schedule(cs):
    rng = random.Random(SEED)
    jobs = []
    for trial in (1, 2):
        ordered = cs[:]
        rng.shuffle(ordered)
        for case in ordered:
            group = [{'case_id': case['case_id'], 'condition': arm, 'trial': trial, 'model_requested': model}
                     for model in MODELS for arm in ARMS]
            rng.shuffle(group)
            jobs.extend(group)
    return jobs


def freeze():
    if (RUN / 'manifest.json').exists():
        raise SystemExit('Already frozen; do not overwrite this iteration')
    required = [RUN / 'ANALYSIS_PLAN.md', RUN / 'PRECHECK_REVIEW.md', ROOT / 'scripts/analyze_model_interaction.py']
    for path in required:
        if not path.is_file():
            raise SystemExit('Required before freeze: ' + str(path))
    preflight_ready()
    cli_version = subprocess.check_output(['codex', '--version'], text=True).strip()
    for path in (RUN / 'preflight').glob('*/record.json'):
        assert json.loads(path.read_text())['cli_version'] == cli_version, 'Canary CLI differs from freeze'
    cs = cases()
    if len(cs) != 12:
        raise ValueError('Expected exactly 12 tasks')
    for arm in ARMS:
        inputs = files(SOURCE) if arm == 'C_skill' else [SOURCE / 'scripts/composition.py', SOURCE / 'scripts/check_evidence.py']
        for path in inputs:
            dest = RUN / 'snapshots' / arm / path.relative_to(SOURCE)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        shutil.copyfile(MATERIAL / 'TOOLS.md', RUN / 'snapshots' / arm / 'TOOLS.md')
        if arm == 'B_checklist':
            shutil.copyfile(MATERIAL / 'CHECKLIST.md', RUN / 'snapshots' / arm / 'CHECKLIST.md')
    write(RUN / 'cases.json', cs)
    write(RUN / 'prediction.schema.json', FINAL_SCHEMA)
    frozen = files(RUN / 'snapshots') + files(RUN / 'preflight') + files(MATERIAL) + required + [Path(__file__), RUN / 'cases.json', RUN / 'prediction.schema.json']
    manifest = {
        'kind': 'model_by_guidance_authored_development_screen', 'frozen_at': datetime.now(timezone.utc).isoformat(),
        'models_requested': MODELS, 'reasoning_effort': 'high', 'model_snapshot': None,
        'cli_version': cli_version,
        'python_version': platform.python_version(), 'execution_platform': platform.system(),
        'concurrency': 3, 'timeout_seconds': 360, 'grading_timeout_seconds': 30, 'trials': 2, 'seed': SEED,
        'schedule': schedule(cs), 'files_sha256': {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in frozen},
        'prompt_sha256': {arm: digest(guidance_prompt(arm).encode()) for arm in ARMS},
    }
    write(RUN / 'manifest.json', manifest)
    print('Frozen 144 attempts; no automatic retries or repairs of frozen material.')


def verify(check_cli=True):
    manifest = json.loads((RUN / 'manifest.json').read_text())
    for path, expected in manifest['files_sha256'].items():
        assert digest((ROOT / path).read_bytes()) == expected, 'Changed frozen file: ' + path
    for arm in ARMS:
        assert digest(guidance_prompt(arm).encode()) == manifest['prompt_sha256'][arm], 'Changed prompt: ' + arm
    if check_cli:
        assert subprocess.check_output(['codex', '--version'], text=True).strip() == manifest['cli_version'], 'CLI version changed'
        assert platform.python_version() == manifest['python_version'], 'Python version changed'
        assert platform.system() == manifest['execution_platform'], 'Execution platform changed'
    cs = {c['case_id']: c for c in json.loads((RUN / 'cases.json').read_text())}
    assert len(manifest['schedule']) == 144 and manifest['schedule'] == schedule(list(cs.values()))
    return manifest, cs


def stage_task(stage, case, arm=None):
    (stage / 'app.py').write_text(case['source'])
    (stage / 'original.py').write_text(case['source'])
    (stage / 'task.md').write_text(case['request'])
    (stage / 'smoke.py').write_text(case['smoke'])
    (stage / 'run_python').write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -B -S "$@"\n')
    (stage / 'run_python').chmod(0o755)
    if arm:
        for path in files(RUN / 'snapshots' / arm):
            dest = stage / path.relative_to(RUN / 'snapshots' / arm)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
    return {p.relative_to(stage).as_posix(): digest(p.read_bytes()) for p in files(stage) if p.name != 'app.py'}


def environment():
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1', LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
    for scheme, url in urllib.request.getproxies().items():
        if scheme in ('http', 'https') and url:
            env.setdefault(scheme.upper() + '_PROXY', url)
    return env


def cli_args(cwd, model, output, schema):
    args = ['codex', 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
            '--sandbox', 'workspace-write', '-C', str(cwd), '-m', model,
            '-c', 'model_reasoning_effort="high"', '-c', 'approval_policy="never"', '-c', 'web_search="disabled"',
            '--enable', 'skip_host_skill_discovery', '--json', '--output-schema', str(schema), '-o', str(output), '-']
    for feature in DISABLED:
        args += ['--disable', feature]
    return args


def invoke(args, prompt_text, out, timeout):
    started = time.monotonic()
    timed_out = False
    with (out / 'events.jsonl').open('w') as stdout, (out / 'stderr.txt').open('w') as stderr:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                env=environment(), start_new_session=True)
        try:
            proc.communicate(prompt_text.encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
    events = []
    for line in (out / 'events.jsonl').read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return {'exit_code': proc.returncode, 'timed_out': timed_out, 'elapsed_seconds': time.monotonic() - started,
            'usage': next((e.get('usage') for e in reversed(events) if e.get('type') == 'turn.completed'), None),
            'completed': any(e.get('type') == 'turn.completed' for e in events), 'events': events}


def infrastructure_kind(meta, stderr):
    # CLI warnings do not invalidate a successfully completed turn.
    if meta.get('completed') and meta.get('exit_code', 0) == 0:
        return None
    failures = [e for e in meta.get('events', []) if e.get('type') in ('error', 'turn.failed')]
    text = (json.dumps(failures, ensure_ascii=False) + '\n' + stderr).lower()
    for kind, markers in (
        ('quota', ('usage limit', 'rate limit', 'quota', 'insufficient_quota', '429')),
        ('auth', ('unauthorized', 'authentication', '401', 'sign in', 'login required')),
        ('transport', ('error sending request', 'connection refused', 'connection reset', 'connection closed', 'dns error', 'network is unreachable', 'stream disconnected', 'failed to establish')),
        ('service', ('service unavailable', 'internal server error', '502 bad gateway', '503', 'model not found', 'not supported', 'does not exist')),
    ):
        if any(marker in text for marker in markers):
            return kind
    if failures:
        return 'service'
    if not meta.get('completed') and not meta.get('timed_out'):
        return 'service'
    return None


def collect_artifacts(stage, out, protected):
    def safe_file(path):
        return (path.is_file() and not path.is_symlink()
                and path.resolve().is_relative_to(stage.resolve())
                and not any(parent.is_symlink() for parent in path.parents if parent != stage and stage in parent.parents))
    changed = [name for name, sha in protected.items()
               if not safe_file(stage / name) or digest((stage / name).read_bytes()) != sha]
    artifacts, errors = {}, []
    for path in stage.rglob('*'):
        name = path.relative_to(stage).as_posix()
        if path.is_symlink():
            errors.append('Symlink forbidden: ' + name)
            continue
        if not path.is_file() or name in protected or '__pycache__' in path.parts:
            continue
        if not path.resolve().is_relative_to(stage.resolve()):
            errors.append('Escaping artifact: ' + name)
            continue
        if path.stat().st_size > 500_000:
            errors.append('Oversized artifact: ' + name)
            continue
        if path.suffix in ('.py', '.json', '.md', '.txt'):
            try:
                path.read_text()
            except UnicodeError:
                errors.append('Non-text artifact: ' + name)
                continue
            dest = out / 'artifacts' / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            artifacts[name] = digest(path.read_bytes())
    return changed, artifacts, errors


def event_audit(events, stage):
    audits, outside = [], []
    for event in events:
        item = event.get('item', {})
        kind = item.get('type')
        if event.get('type') != 'item.completed' or kind in ('agent_message', 'reasoning', 'error', 'todo_list'):
            continue
        entry = {'type': kind, 'exit_code': item.get('exit_code')}
        if 'command' in item:
            command = item['command']
            entry['command'] = command.replace(str(stage), '<workspace>').replace(str(ROOT), '<repo>')
            entry['review_flags'] = [v for v in ('../', 'curl ', 'wget ', 'evals/', 'auth.json', '/Users/') if v in command]
        if 'changes' in item:
            entry['changes'] = item['changes']
            for change in item['changes']:
                path = change.get('path', '')
                if path and not (stage / path).resolve().is_relative_to(stage.resolve()):
                    outside.append(path)
            entry = json.loads(json.dumps(entry).replace(str(stage), '<workspace>'))
        audits.append(entry)
    return audits, outside


def publish(out, destination):
    """Publish selected text artifacts and compact audit; raw service logs stay local."""
    if destination.exists():
        raise RuntimeError('Refuse to overwrite published attempt: ' + str(destination))
    destination.mkdir(parents=True)
    for name in ('record.json', 'started.json', 'patch.diff'):
        if (out / name).is_file():
            shutil.copyfile(out / name, destination / name)
    if (out / 'artifacts').is_dir():
        shutil.copytree(out / 'artifacts', destination / 'artifacts')


def execute(job, case, manifest, out, prompt_text, schema, arm=None, canary=False):
    if out.exists():
        raise RuntimeError('Attempt already exists; no retry or overwrite: ' + str(job))
    out.mkdir(parents=True)
    write(out / 'started.json', {**job, 'time': datetime.now(timezone.utc).isoformat(), 'canary': canary})
    began = time.monotonic()
    meta = {'exit_code': None, 'timed_out': False, 'elapsed_seconds': 0, 'usage': None, 'completed': False}
    artifacts, changed, errors, audits, outside = {}, [], [], [], []
    source, prediction, terminal_errors = '', None, []
    acceptance = {'passed': False, 'tests_run': 0, 'checks': []}
    try:
        with tempfile.TemporaryDirectory(prefix='model-interaction-task-') as directory:
            stage = Path(directory)
            protected = stage_task(stage, case, arm)
            meta = invoke(cli_args(stage, job['model_requested'], out / 'prediction.json', schema),
                          prompt_text, out, manifest['timeout_seconds'])
            infra = infrastructure_kind(meta, (out / 'stderr.txt').read_text())
            changed, artifacts, errors = collect_artifacts(stage, out, protected)
            events = meta.pop('events')
            terminal_errors = [json.dumps(e, ensure_ascii=False).replace(directory, '<workspace>').replace(str(ROOT), '<repo>')[:3000]
                               for e in events if e.get('type') in ('error', 'turn.failed')][-10:]
            audits, outside = event_audit(events, stage)
            source = (out / 'artifacts/app.py').read_text() if (out / 'artifacts/app.py').is_file() else ''
        try:
            prediction = json.loads((out / 'prediction.json').read_text())
        except (OSError, ValueError):
            prediction = None
        if infra:
            status = 'infrastructure_failure'
        elif changed or outside or errors:
            status = 'workspace_violation'
        elif meta['timed_out']:
            status = 'task_timeout'
        elif meta['completed'] and meta['exit_code'] == 0 and final_valid(prediction):
            status = 'completed'
        else:
            status = 'invalid_output'
        acceptance = (grade_source(source, CANARY_ACCEPTANCE, 1, ['sum']) if canary
                      else behavioral_check(case['family'], source, manifest['grading_timeout_seconds']))
        if acceptance.get('isolation_failure'):
            infra, status = 'isolation', 'infrastructure_failure'
        if canary:
            tool_ran = any(a.get('type') == 'command_execution' and a.get('exit_code') == 0
                           and 'run_python' in a.get('command', '') and 'smoke.py' in a.get('command', '') for a in audits)
            acceptance['tool_executed'] = tool_ran
            acceptance['passed'] = acceptance['passed'] and tool_ran and source != case['source']
        (out / 'patch.diff').write_text(''.join(difflib.unified_diff(case['source'].splitlines(True), source.splitlines(True), fromfile='original.py', tofile='app.py')))
        record = {**job, **meta, 'status': status, 'infra_kind': infra, 'prediction': prediction,
                  'acceptance': acceptance, 'acceptance_signature': behavioral_signature(acceptance),
                  'source_changed': source != case['source'] if 'app.py' in artifacts else None, 'artifacts': artifacts,
                  'protected_changes': changed, 'outside_changes': outside, 'artifact_errors': errors,
                  'tool_audit': audits, 'case_sha256': digest(case), 'prompt_sha256': digest(prompt_text.encode()),
                  'event_sha256': digest((out / 'events.jsonl').read_bytes()),
                  'stderr_sha256': digest((out / 'stderr.txt').read_bytes()),
                  'reasoning_effort': 'high', 'cli_version': manifest['cli_version'], 'canary': canary,
                  'python_version': platform.python_version(), 'execution_platform': platform.system(),
                  'terminal_errors': terminal_errors}
    except Exception as error:
        meta.pop('events', None)
        record = {**job, **meta, 'status': 'infrastructure_failure', 'infra_kind': 'runner_error',
                  'error': type(error).__name__ + ': ' + str(error).replace(str(ROOT), '<repo>')[:3000],
                  'elapsed_seconds': time.monotonic() - began, 'canary': canary,
                  'prediction': prediction, 'acceptance': acceptance,
                  'acceptance_signature': behavioral_signature(acceptance),
                  'source_changed': source != case['source'] if 'app.py' in artifacts else None, 'artifacts': artifacts,
                  'protected_changes': changed, 'outside_changes': outside, 'artifact_errors': errors,
                  'tool_audit': audits, 'terminal_errors': terminal_errors,
                  'case_sha256': digest(case), 'prompt_sha256': digest(prompt_text.encode()),
                  'event_sha256': digest((out / 'events.jsonl').read_bytes()) if (out / 'events.jsonl').exists() else None,
                  'stderr_sha256': digest((out / 'stderr.txt').read_bytes()) if (out / 'stderr.txt').exists() else None,
                  'reasoning_effort': 'high', 'cli_version': manifest['cli_version'],
                  'python_version': platform.python_version(), 'execution_platform': platform.system()}
    write(out / 'record.json', record)
    return record


def run_one(job, case, manifest):
    key = digest(job)[:20]
    out = RUN / 'raw-runs' / key
    if (RUN / 'results' / key).exists():
        raise RuntimeError('Published attempt exists; no retry: ' + key)
    result = execute(job, case, manifest, out, guidance_prompt(job['condition']),
                     RUN / 'prediction.schema.json', job['condition'])
    publish(out, RUN / 'results' / key)
    return result


def canary(model=None):
    selected = [model] if model else MODELS
    folder = RUN / 'preflight'
    schema = folder / 'prediction.schema.json'
    if not schema.exists():
        write(schema, FINAL_SCHEMA)
    elif json.loads(schema.read_text()) != FINAL_SCHEMA:
        raise RuntimeError('Canary schema changed; preserve existing preflight')
    manifest = {'timeout_seconds': 360, 'cli_version': subprocess.check_output(['codex', '--version'], text=True).strip()}
    for requested in selected:
        job = {'model_requested': requested, 'case_id': 'canary', 'condition': 'preflight', 'trial': 1}
        key = digest(job)[:20]
        case = {'source': CANARY_SOURCE, 'request': CANARY_REQUEST, 'smoke': CANARY_SMOKE}
        out = RUN / 'raw-runs' / ('canary-' + key)
        if (folder / key).exists():
            raise RuntimeError('Published canary exists; no retry: ' + requested)
        result = execute(job, case, manifest, out, CANARY_REQUEST, schema, canary=True)
        publish(out, folder / key)
        print(json.dumps({'model': requested, 'status': result['status'], 'passed': result['acceptance']['passed'], 'infra_kind': result.get('infra_kind')}), flush=True)
        if result.get('infra_kind') or result['status'] != 'completed' or not result['acceptance']['passed']:
            return False
    return True


def preflight_ready():
    for model in MODELS:
        job = {'model_requested': model, 'case_id': 'canary', 'condition': 'preflight', 'trial': 1}
        path = RUN / 'preflight' / digest(job)[:20] / 'record.json'
        if not path.is_file():
            raise RuntimeError('Required canary missing for ' + model)
        record = json.loads(path.read_text())
        if record['status'] != 'completed' or not record['acceptance']['passed']:
            raise RuntimeError('Canary did not pass; stop and document environment recovery: ' + model)


def run(limit=None):
    manifest, cs = verify()
    preflight_ready()
    pending = []
    for job in manifest['schedule']:
        key = digest(job)[:20]
        out = RUN / 'raw-runs' / key
        public = RUN / 'results' / key
        records = [path for path in (out / 'record.json', public / 'record.json') if path.exists()]
        if records:
            values = [json.loads(path.read_text()) for path in records]
            if len(values) == 2 and values[0] != values[1]:
                raise RuntimeError('Raw/public record mismatch; inspect without rerunning: ' + str(job))
            if values[0].get('infra_kind'):
                raise RuntimeError('Existing infrastructure failure stops this frozen run; register recovery separately: ' + str(job))
            if not (public / 'record.json').exists():
                raise RuntimeError('Attempt finished but publication missing; recover existing files without rerunning: ' + str(job))
            continue
        if out.exists() or public.exists():
            raise RuntimeError('Unfinished prior attempt; no silent retry: ' + str(job))
        pending.append(job)
    if limit is not None:
        if limit < 1:
            raise ValueError('--limit must be positive')
        pending = pending[:limit]
    print(json.dumps({'planned': 144, 'scheduled_this_call': len(pending)}), flush=True)
    iterator = iter(pending)
    stopped = False
    count = 0
    with ThreadPoolExecutor(max_workers=manifest['concurrency']) as pool:
        active = {}
        def submit_next():
            job = next(iterator, None)
            if job is not None:
                active[pool.submit(run_one, job, cs[job['case_id']], manifest)] = job
        for _ in range(min(manifest['concurrency'], len(pending))):
            submit_next()
        while active:
            completed, _ = wait(active, return_when=FIRST_COMPLETED)
            results = []
            for future in completed:
                active.pop(future)
                try:
                    result = future.result()
                except Exception as error:
                    stopped = True
                    print(json.dumps({'scheduler_error': str(error), 'stop_scheduling': True}), flush=True)
                    continue
                results.append(result)
                count += 1
                if result.get('infra_kind'):
                    stopped = True
                print(json.dumps({'finished_this_call': count, **{k: result.get(k) for k in ('case_id', 'condition', 'trial', 'model_requested', 'status', 'infra_kind')}, 'passed': result['acceptance']['passed']}), flush=True)
            if not stopped:
                for _ in results:
                    submit_next()
    print(json.dumps({'finished_this_call': count, 'stopped_on_infrastructure': stopped}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze', 'verify', 'canary', 'run'])
    parser.add_argument('--model', choices=MODELS)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.action == 'freeze':
        freeze()
    elif args.action == 'verify':
        verify()
        print('All frozen model-interaction inputs verified.')
    elif args.action == 'canary':
        sys.exit(0 if canary(args.model) else 1)
    else:
        run(args.limit)
