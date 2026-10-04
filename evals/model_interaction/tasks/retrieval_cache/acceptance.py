"""Maintainer-side acceptance; run with the candidate app.py path."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
import sys


def document(key='a', score=1, value='value'):
    return {'id': key, 'score': score, 'payload': {'text': value, 'tags': ['base'], 'nested': {'values': [1, 2]}}}


def query(text='question', filters=None):
    return {'text': text, 'filters': {} if filters is None else dict(filters)}


def same_error(error, callback):
    try:
        callback()
    except Exception as caught:
        assert caught is error, 'source exception identity changed'
    else:
        raise AssertionError('source exception was hidden')


def rank_order_and_limit(app):
    docs = [document('z', -2), document('a', -2), document('negative', -10),
            document('large', 2**80), document('zero', 0), document('雪', -2)]
    before = deepcopy(docs)
    ordered = [docs[3], docs[4], docs[1], docs[0], docs[5], docs[2]]
    for limit in [0, 1, 2, 5, 6, 20]:
        assert app.rank_documents(docs, limit) == ordered[:limit], limit
        assert docs == before, 'ranking changed caller input'
    assert app.rank_documents([], 3) == []


def full_result_cache(app):
    calls = []
    docs = [document('a', 1), document('b', 3), document('c', 2)]
    def source(tenant, request, revision):
        calls.append((tenant, request, revision))
        return docs
    cache = app.RetrievalCache(source, ttl=10)
    assert cache.search('t', query(), 'r', 0, 1) == [docs[1]]
    assert cache.search('t', query(), 'r', 1, 3) == [docs[1], docs[2], docs[0]]
    assert cache.search('t', query(), 'r', 2, 20) == [docs[1], docs[2], docs[0]]
    assert len(calls) == 1, 'limit incorrectly changed cache identity'
    assert calls[0] == ('t', query(), 'r'), 'source received incorrect request'


def tenant_revision_isolation(app):
    calls = []
    def source(tenant, request, revision):
        calls.append((tenant, revision))
        return [document(value=tenant + '/' + revision)]
    cache = app.RetrievalCache(source, ttl=100)
    identities = [('one', 'v1'), ('two', 'v1'), ('one', 'v2'), ('two', 'v2')]
    for now, (tenant, revision) in enumerate(identities + identities):
        assert cache.search(tenant, query(), revision, now) == [document(value=tenant + '/' + revision)]
    assert calls == identities, 'identities were merged or unnecessary fetches occurred'


def query_identity(app):
    calls = []
    def source(tenant, request, revision):
        calls.append(deepcopy(request))
        return [document(value=json.dumps(request, sort_keys=True))]
    cache = app.RetrievalCache(source, ttl=100)
    requests = [query('first', {'a': '1', 'b': '2'}), query('second', {'a': '1', 'b': '2'}),
                query('first', {'a': '2', 'b': '2'}), query('first', {'a': '1', 'c': '2'})]
    for now, request in enumerate(requests):
        assert cache.search('t', request, 'r', now) == [document(value=json.dumps(request, sort_keys=True))]
    equivalent = query('first', {'b': '2', 'a': '1'})
    assert cache.search('t', equivalent, 'r', 4) == [document(value=json.dumps(requests[0], sort_keys=True))]
    assert calls == requests, 'filter order should not create a distinct identity'


def expiry_without_sliding(app):
    calls = []
    def source(*args):
        calls.append(True)
        return [document(value=str(len(calls)))]
    cache = app.RetrievalCache(source, ttl=5)
    for now, generation in [(0, 1), (4, 1), (5, 2), (9, 2), (10, 3)]:
        assert cache.search('t', query(), 'r', now) == [document(value=str(generation))]
    assert len(calls) == 3, 'expiration equality or hit lifetime is wrong'


def caller_query_isolation(app):
    calls = []
    retained = []
    def source(tenant, request, revision):
        calls.append(True)
        retained.append(request)
        original = request['filters'].get('scope', '')
        request['text'] = 'source rewrote query'
        request['filters']['scope'] = 'source changed filter'
        return [document(value=original)]
    cache = app.RetrievalCache(source, ttl=10)
    request = query(filters={'scope': 'original'})
    before = deepcopy(request)
    assert cache.search('t', request, 'r', 0) == [document(value='original')]
    assert request == before, 'source changed caller query'
    retained[0]['filters'].clear()
    assert cache.search('t', before, 'r', 1) == [document(value='original')]
    request['filters']['scope'] = 'later'
    assert cache.search('t', request, 'r', 2) == [document(value='later')]
    assert cache.search('t', before, 'r', 3) == [document(value='original')]
    assert len(calls) == 2, 'query snapshots were not retained independently'


def source_result_isolation(app):
    docs = [document('a', 3), document('b', 1)]
    expected = deepcopy(docs)
    cache = app.RetrievalCache(lambda *args: docs, ttl=10)
    assert cache.search('t', query(), 'r', 0) == expected
    docs[0]['payload']['nested']['values'].append(999)
    docs[0]['score'] = -50
    docs.reverse()
    docs.append(document('extra', 999))
    assert cache.search('t', query(), 'r', 1) == expected, 'source alias changed cached data'


def returned_result_isolation(app):
    docs = [document('a', 3), document('b', 1)]
    expected = deepcopy(docs)
    ranked = app.rank_documents(docs, 2)
    ranked[0]['payload']['tags'].append('changed')
    ranked[1]['payload']['nested']['values'].clear()
    assert docs == expected, 'ranking output aliases input'
    cache = app.RetrievalCache(lambda *args: docs, ttl=10)
    result = cache.search('t', query(), 'r', 0)
    result[0]['payload']['tags'].append('caller changed')
    result[0]['score'] = 1000
    result.reverse()
    result.clear()
    assert cache.search('t', query(), 'r', 1) == expected, 'returned result changed cached data'
    assert docs == expected, 'cache modified source documents'
    # Payload object plus its leaf list, with 18 extra containers: depth 20.
    payload = {'leaf': [1, 2]}
    for depth in range(18):
        payload = [payload] if depth % 2 == 0 else {'next': payload}
    deep_document = {'id': 'deep', 'score': 1, 'payload': payload}
    deep_expected = deepcopy(deep_document)
    deep_cache = app.RetrievalCache(lambda *args: [deep_document], ttl=10)
    copy = deep_cache.search('t', query(), 'r', 0)[0]['payload']
    while not (isinstance(copy, dict) and 'leaf' in copy):
        copy = copy[0] if isinstance(copy, list) else copy['next']
    copy['leaf'].append(99)
    assert deep_cache.search('t', query(), 'r', 1) == [deep_expected]
    assert deep_document == deep_expected


def failure_not_cached(app):
    marker = RuntimeError('source failed')
    calls = []
    def source(tenant, request, revision):
        calls.append(tenant)
        if tenant == 'bad' and calls.count('bad') == 1:
            raise marker
        return [document(value=tenant)]
    cache = app.RetrievalCache(source, ttl=100)
    assert cache.search('good', query(), 'r', 0) == [document(value='good')]
    same_error(marker, lambda: cache.search('bad', query(), 'r', 1))
    assert cache.search('bad', query(), 'r', 2) == [document(value='bad')]
    assert cache.search('good', query(), 'r', 3) == [document(value='good')]
    assert calls == ['good', 'bad', 'bad'], 'failure erased other entries or was cached'


def expired_failure_retry(app):
    marker = LookupError('refresh failed')
    calls = []
    def source(*args):
        calls.append(True)
        if len(calls) in (2, 3):
            raise marker
        return [document(value=str(len(calls)))]
    cache = app.RetrievalCache(source, ttl=2)
    assert cache.search('t', query(), 'r', 0) == [document(value='1')]
    same_error(marker, lambda: cache.search('t', query(), 'r', 2))
    same_error(marker, lambda: cache.search('t', query(), 'r', 2))
    assert cache.search('t', query(), 'r', 2) == [document(value='4')]
    assert cache.search('t', query(), 'r', 3) == [document(value='4')]
    assert len(calls) == 4


def empty_results_and_zero_limit(app):
    calls = []
    def source(*args):
        calls.append(True)
        return []
    cache = app.RetrievalCache(source, ttl=3)
    assert cache.search('t', query(), 'r', 0, 0) == [] and calls == []
    assert cache.search('t', query(), 'r', 1, 3) == []
    assert cache.search('t', query(), 'r', 3, 3) == [] and len(calls) == 1
    assert cache.search('t', query(), 'r', 4, 0) == [] and len(calls) == 1
    assert cache.search('t', query(), 'r', 4, 3) == [] and len(calls) == 2
    cache = app.RetrievalCache(lambda *args: [document()], ttl=3)
    assert cache.search('t', query(), 'r', 0, 0) == []
    assert cache.search('t', query(), 'r', 0, 1) == [document()]


def independent_instances(app):
    a = app.RetrievalCache(lambda *args: [document(value='a')], ttl=10)
    b = app.RetrievalCache(lambda *args: [document(value='b')], ttl=10)
    assert a.search('t', query(), 'r', 0) == [document(value='a')]
    assert b.search('t', query(), 'r', 0) == [document(value='b')]
    assert a.search('t', query(), 'r', 1) == [document(value='a')]


TESTS = [(fn.__name__, fn) for fn in [rank_order_and_limit, full_result_cache,
    tenant_revision_isolation, query_identity, expiry_without_sliding,
    caller_query_isolation, source_result_isolation, returned_result_isolation,
    failure_not_cached, expired_failure_retry, empty_results_and_zero_limit,
    independent_instances]]


def main():
    checks = []
    module, load_error = None, None
    with redirect_stdout(io.StringIO()):
        try:
            spec = importlib.util.spec_from_file_location('app', sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        except BaseException as exc:
            load_error = exc
        for name, test in TESTS:
            try:
                if load_error is not None:
                    raise load_error
                test(module)
                checks.append({'test': name, 'passed': True})
            except BaseException as exc:
                checks.append({'test': name, 'passed': False, 'error': type(exc).__name__ + ': ' + str(exc)})
    print(json.dumps({'passed': all(item['passed'] for item in checks), 'tests_run': len(checks), 'checks': checks}))


if __name__ == '__main__':
    main()
