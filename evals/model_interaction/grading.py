"""Independent, strict behavioral checks; missing isolation fails explicitly."""
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
TASKS = Path(__file__).resolve().parent / 'tasks'


def behavioral_signature(result):
    """Stable grading fields; no platform-specific paths or wall-clock timing."""
    return {
        'passed': result.get('passed') is True,
        'tests_run': result.get('tests_run'),
        'checks': [{'test': c.get('test'), 'passed': c.get('passed') is True}
                   for c in result.get('checks', []) if isinstance(c, dict)],
        'exit_code': result.get('exit_code'), 'timed_out': result.get('timed_out', False),
        'invalid_output': result.get('invalid_output', False),
        'isolation_failure': result.get('isolation_failure', False),
    }


def validate_result(result, expected, names, returncode, timed_out):
    if not isinstance(result, dict):
        result = {'invalid_output': True, 'tests_run': 0, 'checks': []}
    checks = result.get('checks', [])
    valid_checks = (isinstance(checks, list) and len(checks) == expected
                    and all(isinstance(c, dict) and isinstance(c.get('test'), str)
                            and c.get('passed') is True and not c.get('skipped')
                            for c in checks))
    if valid_checks:
        valid_checks = {c['test'] for c in checks} == set(names) and len({c['test'] for c in checks}) == expected
    result['passed'] = bool(result.get('passed') is True and returncode == 0 and not timed_out
                            and type(result.get('tests_run')) is int and result['tests_run'] == expected
                            and valid_checks and not result.get('skipped'))
    if not isinstance(checks, list):
        result['checks'] = []
    return result


def grade_source(source, acceptance, expected, names, timeout=30):
    with tempfile.TemporaryDirectory(prefix='model-interaction-grade-') as directory:
        stage = Path(directory)
        (stage / 'app.py').write_text(source)
        (stage / 'acceptance.py').write_text(acceptance)
        command = [sys.executable, '-B', '-S', str(stage / 'acceptance.py'), str(stage / 'app.py')]
        sandbox = 'process_timeout_only'
        if platform.system() == 'Darwin':
            policy = '(version 1) (allow default) (deny network*) (deny file-write*) (allow file-write* (subpath ' + json.dumps(str(stage.resolve())) + '))'
            (stage / 'policy.sb').write_text(policy)
            command = ['/usr/bin/sandbox-exec', '-f', str(stage / 'policy.sb'), *command]
            sandbox = 'macos_no_network_writes_only_grade_directory'
        env = os.environ.copy()
        env.update(TMPDIR=directory, PYTHONDONTWRITEBYTECODE='1', LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        for name in list(env):
            if any(s in name.upper() for s in ('TOKEN', 'API_KEY', 'SECRET', 'PASSWORD')):
                env.pop(name)
        proc = subprocess.Popen(command, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, start_new_session=True, text=True)
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        try:
            result = json.loads(stdout)
        except ValueError:
            result = {'invalid_output': True, 'tests_run': 0, 'checks': []}
        result = validate_result(result, expected, names, proc.returncode, timed_out)
        isolation_failure = sandbox.startswith('macos') and ('sandbox-exec:' in stderr or 'sandbox_apply:' in stderr)
        if isolation_failure:
            result['passed'] = False
            result['isolation_failure'] = True
        result.update(exit_code=proc.returncode, timed_out=timed_out, sandbox=sandbox,
                      stderr=stderr.replace(directory, '<grade>')[:12000])
        for check in result.get('checks', []):
            if isinstance(check, dict) and isinstance(check.get('error'), str):
                check['error'] = check['error'].replace(directory, '<grade>')[:4000]
        return result


def behavioral_check(family, source, timeout=30):
    folder = TASKS / family
    meta = json.loads((folder / 'meta.json').read_text())
    return grade_source(source, (folder / 'acceptance.py').read_text(),
                        meta['expected_checks'], meta['test_names'], timeout)
