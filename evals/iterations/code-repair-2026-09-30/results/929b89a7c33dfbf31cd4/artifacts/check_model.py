"""Check declaration consistency only; behavior is tested in test_review.py."""
import json
from pathlib import Path


model = json.loads(Path('model.json').read_text())
assert model['schema_version'] == 1
types = model['types']
actions = {a['id']: a for a in model['actions']}
assert len(actions) == len(model['actions'])
assert set(model['required_actions']) <= actions.keys()
for fields in types.values():
    for constraint in fields.values():
        assert constraint['type'] in {'string', 'number', 'boolean', 'object', 'array', 'null'}
        assert 'enum' not in constraint or constraint['enum']
for action in actions.values():
    assert action['input_type'] in types and action['output_type'] in types
    assert action['meaning']
    assert set(action['reads'] + action['writes']) <= model['resources'].keys()
    for condition in action['pre'] + action['post']:
        assert {'id', 'requirement', 'must'} <= condition.keys()
        assert condition['must']['op'] in {'eq', 'ne', 'le', 'ge', 'contains', 'subset', 'unique', 'and', 'or'}
        for expression in condition['must']['args']:
            if 'path' in expression:
                side, field = expression['path'].split('.', 1)
                assert side in {'input', 'output'}
                assert field in types[action[side + '_type']]
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
print('Model declarations, references, condition paths, and evidence ranges are valid.')
