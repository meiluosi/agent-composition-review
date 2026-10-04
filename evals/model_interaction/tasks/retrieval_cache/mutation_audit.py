"""Maintainer-authored negative controls; no model calls."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

MUTANTS = {
    'omit_tenant': ["key = (tenant, json.dumps(query, sort_keys=True, ensure_ascii=False), revision)", "key = (json.dumps(query, sort_keys=True, ensure_ascii=False), revision)"],
    'omit_revision': ["key = (tenant, json.dumps(query, sort_keys=True, ensure_ascii=False), revision)", "key = (tenant, json.dumps(query, sort_keys=True, ensure_ascii=False))"],
    'filter_insertion_order_identity': ['sort_keys=True', 'sort_keys=False'],
    'expiration_inclusive': ['now < entry[0]', 'now <= entry[0]'],
    'slide_lifetime_on_hit': ['            documents = entry[1]', '            documents = entry[1]\n            self._entries[key] = (now + self._ttl, documents)'],
    'cache_only_first_limit': ['self._entries[key] = (now + self._ttl, documents)', 'self._entries[key] = (now + self._ttl, rank_documents(documents, limit))'],
    'source_query_alias': ['self._source(tenant, deepcopy(query), revision)', 'self._source(tenant, query, revision)'],
    'source_result_alias': ['documents = deepcopy(self._source(tenant, deepcopy(query), revision))', 'documents = self._source(tenant, deepcopy(query), revision)'],
    'returned_document_alias': ['return deepcopy(ordered[:limit])', 'return ordered[:limit]'],
    'limit_off_by_one': ['return deepcopy(ordered[:limit])', 'return deepcopy(ordered[:limit + 1])'],
}


def main():
    folder = Path(__file__).resolve().parent
    source = (folder / 'normal.py').read_text()
    results = []
    for name, (old, new) in MUTANTS.items():
        assert source.count(old) == 1, (name, 'mutation must match exactly once')
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / 'app.py'
            candidate.write_text(source.replace(old, new, 1))
            process = subprocess.run([sys.executable, '-B', '-S', str(folder / 'acceptance.py'), str(candidate)],
                                     capture_output=True, text=True, timeout=5)
            assert process.returncode == 0, (name, process.stderr)
            result = json.loads(process.stdout)
            assert result['tests_run'] == 12 and len(result['checks']) == 12
            failed = [item['test'] for item in result['checks'] if not item['passed']]
            results.append({'mutant': name, 'detected': not result['passed'], 'failed_checks': failed})
    report = {'family': folder.name, 'all_detected': all(item['detected'] for item in results),
              'mutants': results,
              'scope': 'Authored negative controls; detecting these mutations does not establish complete correctness.'}
    print(json.dumps(report, indent=2))
    if not report['all_detected']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
