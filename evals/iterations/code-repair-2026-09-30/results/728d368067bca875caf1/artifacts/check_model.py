"""Check model references only; behavioral coverage is in test_app.py."""
import json
from pathlib import Path

model = json.loads(Path('model.json').read_text())
assert model['schema_version'] == 1
types = model['types']
actions = {a['id']: a for a in model['actions']}
assert len(actions) == len(model['actions'])
assert set(model['required_actions']) == set(actions)
for fields in types.values():
    for constraint in fields.values():
        assert constraint['type'] in {
            'string', 'number', 'boolean', 'object', 'array', 'null'}
        if 'enum' in constraint:
            assert constraint['enum']
for action in actions.values():
    assert action['input_type'] in types
    assert action['output_type'] in types
    assert action['meaning']
    assert set(action['reads'] + action['writes']) <= set(model['resources'])
    for field in ['pre', 'post']:
        assert isinstance(action[field], list)
        for condition in action[field]:
            assert {'id', 'requirement', 'must'} <= condition.keys()
            assert condition['must']['op'] in {
                'eq', 'ne', 'le', 'ge', 'contains', 'subset', 'unique', 'and', 'or'}
for edge in model['edges']:
    source, target = actions[edge['from']], actions[edge['to']]
    for binding in edge['bindings']:
        assert binding['output'] in types[source['output_type']]
        assert binding['input'] in types[target['input_type']]
lines = Path('app.py').read_text().splitlines()
for evidence in model['scope']['evidence']:
    assert evidence['path'] == 'app.py'
    assert 1 <= evidence['start'] <= evidence['end'] <= len(lines)
    assert evidence['supports']
assert model['scope']['limitations']
print('Model references and evidence ranges valid; this is not behavioral proof.')
