"""Contract checks; pass app or original as the first command-line argument."""
import importlib
import sys
import unittest
from copy import deepcopy

implementation = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app')
RetrievalCache = implementation.RetrievalCache
rank_documents = implementation.rank_documents


def query():
    return {'text': 'search', 'filters': {'color': 'red', 'size': 'small'}}


def documents():
    return [
        {'id': 'b', 'score': 0, 'payload': {'items': [{'value': 'original'}]}},
        {'id': 'a', 'score': 0, 'payload': {}},
        {'id': 'negative', 'score': -10, 'payload': {}},
        {'id': 'large', 'score': 10**100, 'payload': {}},
    ]


class CacheContract(unittest.TestCase):
    def test_ranking_and_independent_payloads(self):
        source_docs = documents()
        before = deepcopy(source_docs)
        result = rank_documents(source_docs, 100)
        self.assertEqual([d['id'] for d in result], ['large', 'a', 'b', 'negative'])
        self.assertEqual(source_docs, before)
        result[2]['payload']['items'][0]['value'] = 'changed'
        self.assertEqual(source_docs, before)
        self.assertEqual(rank_documents(source_docs, 1), [source_docs[3]])
        self.assertEqual(rank_documents(source_docs, 0), [])
        self.assertEqual(rank_documents([], 10), [])

    def test_payload_depth_and_string_ties(self):
        payload = {'values': [None, True, False, 0, -1, 1.5, '文字']}
        for _ in range(18):
            payload = {'nested': payload}
        ids = ['é', 'a', 'A', '😀', '\ud800']
        docs = [{'id': key, 'score': -1, 'payload': deepcopy(payload)} for key in ids]
        result = rank_documents(docs, 10)
        self.assertEqual([d['id'] for d in result], sorted(ids))
        nested = result[0]['payload']
        for _ in range(18):
            nested = nested['nested']
        nested['values'].append('changed')
        self.assertEqual(docs[0]['payload'], payload)

    def test_tenant_and_revision_identity(self):
        for field in ('tenant', 'revision'):
            with self.subTest(field=field):
                calls = []
                def source(tenant, q, revision):
                    calls.append((tenant, revision))
                    return [{'id': tenant + '/' + revision, 'score': 1, 'payload': {}}]
                cache = RetrievalCache(source)
                first = cache.search('t1', query(), 'r1', 0)
                tenant, revision = ('t2', 'r1') if field == 'tenant' else ('t1', 'r2')
                second = cache.search(tenant, query(), revision, 1)
                self.assertEqual(second[0]['id'], tenant + '/' + revision)
                self.assertEqual(cache.search('t1', query(), 'r1', 2), first)
                self.assertEqual(len(calls), 2)

    def test_query_identity_and_filter_order(self):
        calls = []
        def source(t, q, r):
            calls.append(deepcopy(q))
            return [{'id': str(len(calls)), 'score': 1, 'payload': {}}]
        cache = RetrievalCache(source)
        first = cache.search('t', query(), 'r', 0)
        reordered = {'filters': {'size': 'small', 'color': 'red'}, 'text': 'search'}
        self.assertEqual(cache.search('t', reordered, 'r', 1), first)
        variants = [
            {'text': 'SEARCH', 'filters': query()['filters']},
            {'text': 'search', 'filters': {'Color': 'red', 'size': 'small'}},
            {'text': 'search', 'filters': {'color': 'blue', 'size': 'small'}},
            {'text': 'search', 'filters': {}},
            {'text': '搜索\ud800', 'filters': {'a:b': 'c', 'a': 'b:c'}},
        ]
        for q in variants:
            result = cache.search('t', q, 'r', 2)
            self.assertNotEqual(result, first)
            self.assertEqual(cache.search('t', deepcopy(q), 'r', 2), result)
        self.assertEqual(len(calls), 1 + len(variants))

    def test_source_query_mutation_isolation(self):
        retained = []
        def source(t, q, r):
            retained.append(q)
            q['text'] = 'changed'
            q['filters']['color'] = 'changed'
            return documents()
        cache = RetrievalCache(source)
        caller_query = query()
        cache.search('t', caller_query, 'r', 0)
        self.assertEqual(caller_query, query())
        retained[0]['filters'].clear()
        self.assertEqual(caller_query, query())
        cache.search('t', query(), 'r', 1)
        self.assertEqual(len(retained), 1)

    def test_source_document_mutation_isolation(self):
        owned = documents()
        expected = rank_documents(owned, 10)
        cache = RetrievalCache(lambda *args: owned)
        self.assertEqual(cache.search('t', query(), 'r', 0), expected)
        owned[0]['payload']['items'][0]['value'] = 'changed'
        owned[0]['score'] = 10**101
        owned.pop()
        self.assertEqual(cache.search('t', query(), 'r', 1), expected)

    def test_returned_results_and_full_result_retention(self):
        owned = documents()
        before = deepcopy(owned)
        calls = []
        def source(*args):
            calls.append(args)
            return owned
        cache = RetrievalCache(source)
        small = cache.search('t', query(), 'r', 0, limit=1)
        small[0]['payload']['new'] = []
        full = cache.search('t', query(), 'r', 1, limit=20)
        self.assertEqual(full, rank_documents(before, 20))
        full[2]['payload']['items'][0]['value'] = 'changed'
        full.clear()
        self.assertEqual(cache.search('t', query(), 'r', 2), rank_documents(before, 20))
        self.assertEqual(owned, before)
        self.assertEqual(len(calls), 1)

    def test_ttl_boundary_hits_and_zero_limit(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [{'id': str(len(calls)), 'score': 1, 'payload': {}}]
        cache = RetrievalCache(source, ttl=3)
        self.assertEqual(cache.search('t', query(), 'r', 0, limit=0), [])
        self.assertEqual(calls, [])
        first = cache.search('t', query(), 'r', 1)
        self.assertEqual(cache.search('t', query(), 'r', 1), first)
        self.assertEqual(cache.search('t', query(), 'r', 3), first)
        self.assertEqual(cache.search('t', query(), 'r', 4, limit=0), [])
        self.assertEqual(len(calls), 1)
        second = cache.search('t', query(), 'r', 4)
        self.assertNotEqual(first, second)
        self.assertEqual(cache.search('t', query(), 'r', 6), second)
        self.assertEqual(len(calls), 2)
        cache.search('t', query(), 'r', 7)
        self.assertEqual(len(calls), 3)

    def test_empty_success_is_cached(self):
        calls = []
        def source(*args):
            calls.append(args)
            return []
        cache = RetrievalCache(source, ttl=1)
        self.assertEqual(cache.search('t', query(), 'r', 0), [])
        self.assertEqual(cache.search('t', query(), 'r', 0), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.search('t', query(), 'r', 1), [])
        self.assertEqual(len(calls), 2)

    def test_failures_retry_and_preserve_other_entries(self):
        error = RuntimeError('source failure')
        calls = []
        failing = set()
        def source(t, q, r):
            calls.append(q['text'])
            if q['text'] in failing:
                q['filters'].clear()
                raise error
            return documents()
        cache = RetrievalCache(source, ttl=10)
        a, b = query(), {'text': 'other', 'filters': {}}
        expected = cache.search('t', a, 'r', 0)
        cache.search('t', b, 'r', 5)
        failing.add(a['text'])
        for now in (10, 11):
            with self.assertRaises(RuntimeError) as raised:
                cache.search('t', a, 'r', now)
            self.assertIs(raised.exception, error)
            self.assertEqual(a, query())
        self.assertEqual(cache.search('t', b, 'r', 11), expected)
        self.assertEqual(calls.count('other'), 1)
        failing.clear()
        self.assertEqual(cache.search('t', a, 'r', 12), expected)
        self.assertEqual(calls.count('search'), 4)

    def test_instance_privacy(self):
        calls = []
        def source(*args):
            calls.append(args)
            return documents()
        a, b = RetrievalCache(source), RetrievalCache(source)
        a.search('t', query(), 'r', 0)
        b.search('t', query(), 'r', 0)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
