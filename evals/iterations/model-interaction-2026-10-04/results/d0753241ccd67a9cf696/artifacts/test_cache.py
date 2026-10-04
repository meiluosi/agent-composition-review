"""Contract regressions; run with ./run_python test_cache.py [app|original]."""
from copy import deepcopy
import importlib
import sys
import unittest

implementation = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app')
RetrievalCache = implementation.RetrievalCache
rank_documents = implementation.rank_documents


def doc(identifier='a', score=1):
    return {'id': identifier, 'score': score,
            'payload': {'nested': [{'values': [None, True, 1, 1.5, 'value']} ]}}


def query():
    return {'text': 'search', 'filters': {'a': '1', 'b': '2'}}


class ContractTests(unittest.TestCase):
    def test_ranking_and_limits(self):
        documents = [doc('z', 0), doc('b', -4), doc('a', -4),
                     doc('large', 10 ** 200), doc('é', 0), doc('A', 0)]
        saved = deepcopy(documents)
        expected = ['large', 'A', 'z', 'é', 'a', 'b']
        for limit in (0, 1, 3, 6, 20):
            result = rank_documents(documents, limit)
            self.assertEqual([d['id'] for d in result], expected[:limit])
            if result:
                result[0]['payload']['nested'][0]['values'].append('changed')
                result[0]['score'] = -999
            self.assertEqual(documents, saved)
        self.assertEqual(rank_documents([], 10), [])

    def test_twenty_container_payload(self):
        payload = {'leaf': [None, False, '雪', -3.5]}
        for i in range(18):
            payload = {'child': payload} if i % 2 else [payload]
        documents = [{'id': 'deep', 'score': 0, 'payload': payload}]
        saved = deepcopy(documents)
        result = rank_documents(documents, 1)
        self.assertEqual(result, saved)
        result[0]['payload']['child'].clear()
        self.assertEqual(documents, saved)

    def test_tenant_identity(self):
        calls = []
        def source(tenant, q, revision):
            calls.append(tenant)
            return [doc(tenant)]
        cache = RetrievalCache(source)
        self.assertEqual(cache.search('one', query(), 'v1', 0)[0]['id'], 'one')
        self.assertEqual(cache.search('two', query(), 'v1', 1)[0]['id'], 'two')
        self.assertEqual(cache.search('one', query(), 'v1', 2)[0]['id'], 'one')
        self.assertEqual(calls, ['one', 'two'])

    def test_revision_identity(self):
        calls = []
        def source(tenant, q, revision):
            calls.append(revision)
            return [doc(revision)]
        cache = RetrievalCache(source)
        for revision in ('v1', 'v2', 'v1'):
            self.assertEqual(cache.search('team', query(), revision, 0)[0]['id'], revision)
        self.assertEqual(calls, ['v1', 'v2'])

    def test_query_identity_and_filter_order(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source)
        variants = [query(), {'text': 'other', 'filters': query()['filters']},
                    {'text': 'search', 'filters': {'c': '1', 'b': '2'}},
                    {'text': 'search', 'filters': {'a': '9', 'b': '2'}},
                    {'text': 'search', 'filters': {}},
                    {'text': 'a|b\x00雪', 'filters': {'': 'x:y', 'x': '\ud800'}}]
        for index, q in enumerate(variants, 1):
            self.assertEqual(cache.search('team', q, 'v1', 0)[0]['id'], str(index))
        reversed_query = {'filters': {'b': '2', 'a': '1'}, 'text': 'search'}
        self.assertEqual(cache.search('team', reversed_query, 'v1', 0)[0]['id'], '1')
        self.assertEqual(len(calls), len(variants))

    def test_full_result_retained_across_limits(self):
        calls = []
        documents = [doc('low', -1), doc('high', 9), doc('mid', 2)]
        def source(*args):
            calls.append(args)
            return documents
        cache = RetrievalCache(source)
        self.assertEqual(len(cache.search('t', query(), 'r', 0, 1)), 1)
        self.assertEqual([d['id'] for d in cache.search('t', query(), 'r', 1, 20)],
                         ['high', 'mid', 'low'])
        self.assertEqual(len(calls), 1)

    def test_ttl_boundary_and_hits_do_not_extend(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source, ttl=3)
        for now, expected in [(4, '1'), (4, '1'), (6, '1'), (7, '2'),
                              (9, '2'), (10, '3'), (1000, '4')]:
            self.assertEqual(cache.search('t', query(), 'r', now)[0]['id'], expected)
        self.assertEqual(len(calls), 4)

    def test_empty_success_is_cached(self):
        calls = []
        def source(*args):
            calls.append(args)
            return []
        cache = RetrievalCache(source, ttl=1)
        for now in (0, 0, 1):
            self.assertEqual(cache.search('t', query(), 'r', now), [])
        self.assertEqual(len(calls), 2)

    def test_zero_limit_never_fetches_or_refreshes(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source, ttl=3)
        self.assertEqual(cache.search('t', query(), 'r', 0, 0), [])
        self.assertEqual(calls, [])
        self.assertEqual(cache.search('t', query(), 'r', 1)[0]['id'], '1')
        self.assertEqual(cache.search('t', query(), 'r', 2, 0), [])
        self.assertEqual(cache.search('t', query(), 'r', 4, 0), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.search('t', query(), 'r', 4)[0]['id'], '2')

    def test_instance_isolation(self):
        first = RetrievalCache(lambda *args: [doc('first')])
        second = RetrievalCache(lambda *args: [doc('second')])
        self.assertEqual(first.search('t', query(), 'r', 0)[0]['id'], 'first')
        self.assertEqual(second.search('t', query(), 'r', 0)[0]['id'], 'second')

    def test_source_query_mutation_is_isolated(self):
        retained = []
        def source(tenant, q, revision):
            retained.append(q)
            q['text'] = 'source edit'
            q['filters']['a'] = 'source edit'
            return [doc()]
        cache = RetrievalCache(source)
        caller_query = query()
        saved = deepcopy(caller_query)
        cache.search('t', caller_query, 'r', 0)
        self.assertEqual(caller_query, saved)
        retained[0]['filters'].clear()
        self.assertEqual(caller_query, saved)
        cache.search('t', caller_query, 'r', 1)
        self.assertEqual(len(retained), 1)

    def test_source_documents_mutation_is_isolated(self):
        documents = [doc('a', 1), doc('b', 2)]
        saved = deepcopy(documents)
        cache = RetrievalCache(lambda *args: documents)
        result = cache.search('t', query(), 'r', 0)
        self.assertEqual(documents, saved)
        documents[1]['payload']['nested'][0]['values'].append('source edit')
        documents[1]['score'] = -100
        documents.clear()
        self.assertEqual(cache.search('t', query(), 'r', 1), result)

    def test_returned_documents_mutation_is_isolated(self):
        documents = [doc()]
        saved = deepcopy(documents)
        cache = RetrievalCache(lambda *args: documents)
        for now in (0, 1):
            result = cache.search('t', query(), 'r', now)
            result[0]['payload']['nested'][0]['values'].clear()
            result[0]['id'] = 'edited'
            result.append(doc('extra'))
            self.assertEqual(documents, saved)
            self.assertEqual(cache.search('t', query(), 'r', now), saved)

    def test_exception_propagation_retry_and_other_entries(self):
        failure = RuntimeError('source unavailable')
        attempts = []
        def source(tenant, q, revision):
            attempts.append(q['text'])
            if q['text'] == 'bad' and attempts.count('bad') == 1:
                raise failure
            return [doc(q['text'])]
        cache = RetrievalCache(source)
        good = {'text': 'good', 'filters': {}}
        bad = {'text': 'bad', 'filters': {}}
        cache.search('t', good, 'r', 0)
        with self.assertRaises(RuntimeError) as raised:
            cache.search('t', bad, 'r', 1)
        self.assertIs(raised.exception, failure)
        self.assertEqual(cache.search('t', good, 'r', 2)[0]['id'], 'good')
        self.assertEqual(cache.search('t', bad, 'r', 2)[0]['id'], 'bad')
        self.assertEqual(attempts, ['good', 'bad', 'bad'])

    def test_expired_entry_not_returned_on_error(self):
        failure = ValueError('retry')
        calls = []
        def source(*args):
            calls.append(args)
            if len(calls) in (2, 3):
                raise failure
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source, ttl=1)
        cache.search('t', query(), 'r', 0)
        for now in (1, 2):
            with self.assertRaises(ValueError) as raised:
                cache.search('t', query(), 'r', now)
            self.assertIs(raised.exception, failure)
        self.assertEqual(cache.search('t', query(), 'r', 3)[0]['id'], '4')


if __name__ == '__main__':
    unittest.main(verbosity=2)
