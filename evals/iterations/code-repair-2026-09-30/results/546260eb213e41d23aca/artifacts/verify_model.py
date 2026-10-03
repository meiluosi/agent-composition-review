import json
from collections import Counter
from pathlib import Path

from app import build_graph
from test_review import expected


def resolve(event, expression):
    if 'literal' in expression:
        return expression['literal']
    value = event
    for key in expression['path'].split('.'):
        value = value[key]
    return value


def check(event, predicate):
    a, b = [resolve(event, item) for item in predicate['args']]
    return {'eq': lambda: a == b, 'ge': lambda: a >= b,
            'le': lambda: a <= b, 'contains': lambda: b in a}[predicate['op']]()


model = json.loads(Path('model.json').read_text())
assert model['schema_version'] == 1
assert Path('app.py').read_bytes() == Path('original.py').read_bytes()
actions = {a['id']: a for a in model['actions']}
assert len(actions) == len(model['actions'])
assert set(model['required_actions']) == set(actions)
for action in actions.values():
    assert action['meaning']
    assert action['input_type'] in model['types']
    assert action['output_type'] in model['types']
    assert set(action['reads'] + action['writes']) <= set(model['resources'])
for edge in model['edges']:
    assert edge['from'] in actions and edge['to'] in actions
    for binding in edge['bindings']:
        assert binding['output'] in model['types'][actions[edge['from']]['output_type']]
        assert binding['input'] in model['types'][actions[edge['to']]['input_type']]
lines = Path('app.py').read_text().splitlines()
for evidence in model['scope']['evidence']:
    assert evidence['path'] == 'app.py'
    assert 1 <= evidence['start'] <= evidence['end'] <= len(lines)

traces = []
for initial in [dict(count=1, label='old', left=2, right=3),
                dict(count=-20, label='中文🙂', left=800, right=-900),
                dict(count=-7, label='', left=-19, right=23)]:
    calls = []
    raw = list(build_graph(calls=calls).stream(initial, stream_mode='debug', subgraphs=True))
    observed = {}
    counts = Counter()
    child_result = None
    for namespace, item in raw:
        payload = item['payload']
        name = payload['name']
        if name == 'analysis' and item['type'] == 'task_result':
            child_result = payload['result']
        if name not in actions:
            continue
        record = observed.setdefault(name, {})
        if item['type'] == 'task':
            record['input'] = payload['input']
            counts[name] += 1
        elif item['type'] == 'task_result':
            assert payload['error'] is None
            record['output'] = payload['result']
    assert counts == Counter({name: 1 for name in actions})
    assert Counter(calls) == counts
    context = {'initial': initial, 'actions': observed}
    for name, action in actions.items():
        event = dict(observed[name], context=context)
        assert set(event['output']) == set(model['types'][action['output_type']])
        for condition in action['pre'] + action['post']:
            if 'when' not in condition or check(event, condition['when']):
                assert check(event, condition['must']), condition['id']
    for edge in model['edges']:
        for binding in edge['bindings']:
            assert (observed[edge['from']]['output'][binding['output']] ==
                    observed[edge['to']]['input'][binding['input']])
    want = expected(initial)
    for name, field in [('prepare', 'count'), ('refresh', 'label'),
                        ('left', 'left'), ('right', 'right')]:
        assert observed[name]['output'] == {field: want[field]}
    assert child_result == {'left': want['left'], 'right': want['right']}
    traces.append({'initial': initial, 'actions': observed,
                   'analysis_output': child_result, 'calls': calls, 'raw_debug_events': raw})
Path('trace.json').write_text(json.dumps(traces, ensure_ascii=False, indent=2) + '\n')
print('PASS: model references, source line bounds, unchanged app, and 3 real execution traces')
