"""Check model references and evidence ranges, not concurrent behavior."""
import json
from pathlib import Path


model = json.loads(Path('model.json').read_text())
assert model['schema_version'] == 1
actions = {action['id']: action for action in model['actions']}
assert len(actions) == len(model['actions'])
assert set(model['required_actions']) <= actions.keys()
for constraints in model['types'].values():
    for constraint in constraints.values():
        assert constraint['type'] in {
            'string', 'number', 'boolean', 'object', 'array', 'null'
        }
        assert 'enum' not in constraint or constraint['enum']
for action in actions.values():
    assert action['meaning']
    assert action['input_type'] in model['types']
    assert action['output_type'] in model['types']
    for resource in action['reads'] + action['writes']:
        assert resource in model['state_resources']
    for phase in ('pre', 'post'):
        assert action[phase]
        for condition in action[phase]:
            assert condition['id'] and condition['requirement']
            assert condition['must']['op'] in {
                'eq', 'ne', 'le', 'ge', 'contains', 'subset', 'unique', 'and', 'or'
            }
            assert isinstance(condition['must']['args'], list)
for edge in model['edges']:
    source, target = actions[edge['from']], actions[edge['to']]
    for binding in edge['bindings']:
        assert binding['output'] in model['types'][source['output_type']]
        assert binding['input'] in model['types'][target['input_type']]
for relation in model['relations']:
    assert set(relation['actions']) <= actions.keys()
source_lines = Path('app.py').read_text().splitlines()
for evidence in model['scope']['evidence']:
    assert evidence['path'] == 'app.py'
    assert 1 <= evidence['start'] <= evidence['end'] <= len(source_lines)
    assert evidence['supports']
print('Model references and evidence ranges valid; behavior checked separately.')
