"""Private task metadata; only source, request and smoke enter model workspaces."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASKS = Path(__file__).resolve().parent / 'tasks'
FAMILIES = ('lease_queue', 'retrieval_cache', 'stream_events', 'tool_results')
VARIANTS = ('incident', 'normal', 'local')


def cases():
    result = []
    for family in FAMILIES:
        folder = TASKS / family
        meta = json.loads((folder / 'meta.json').read_text())
        expected = meta['expected_checks']
        names = meta['test_names']
        if type(expected) is not int or expected <= 0 or len(names) != expected or len(set(names)) != expected or not all(isinstance(n, str) and n for n in names):
            raise ValueError('Invalid expected checks: ' + family)
        for variant in VARIANTS:
            source = folder / (variant + '.py')
            result.append({
                'case_id': f'm{len(result) + 1:02d}', 'family': family, 'variant': variant,
                'source_file': str(source.relative_to(ROOT)), 'source': source.read_text(),
                'request': (folder / 'request.md').read_text(),
                'smoke': (folder / 'smoke.py').read_text(),
                'expected_checks': expected, 'test_names': names, 'provenance': meta,
            })
    return result
