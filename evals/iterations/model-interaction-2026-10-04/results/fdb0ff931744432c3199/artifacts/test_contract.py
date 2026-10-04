"""Contract regressions; optionally run against original.py."""
import importlib
import sys
import unittest
from copy import deepcopy

api = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app')


def query():
    return {'text': 'search', 'filters': {'a': '1', 'b': '2'}}


def docs():
    return [
        {'id': 'z', 'score': 0, 'payload': {'items': [{'value': 'old'}]}},
        {'id': 'b', 'score': 9, 'payload': {}},
        {'id': 'a', 'score': 9, 'payload': {}},
        {'id': 'negative', 'score': -10, 'payload': {}},
    ]


class ContractTests(unittest.TestCase):
    def test_rank_order_limits_and_input(self):
        values = docs() + [
            {'id': 'large', 'score': 10 ** 100, 'payload': {}},
            {'id': '\U0001f600', 'score': 9, 'payload': {}},
        ]
        before = deepcopy(values)
        expected = ['large', 'a', 'b', '\U0001f600', 'z', 'negative']
        for limit in (0, 1, 3, 6, 100):
            self.assertEqual([d['id'] for d in api.rank_documents(values, limit)], expected[:limit])
            self.assertEqual(values, before)
        self.assertEqual(api.rank_documents([], 10), [])

    def test_rank_nested_independent_copies(self):
        payload = {'leaf': [None, True, False, 1.25, 'text']}
        # Two initial containers and eighteen wrappers: maximum allowed depth.
        for index in range(18):
            payload = {'nested': payload} if index % 2 else [payload]
        values = [{'id': 'deep', 'score': -1, 'payload': payload}]
        before = deepcopy(values)
        first = api.rank_documents(values, 1)
        second = api.rank_documents(values, 1)
        first[0]['payload'].clear()
        self.assertEqual(values, before)
        self.assertEqual(second, before)

    def test_tenant_and_revision_identity(self):
        calls = []
        def source(tenant, q, revision):
            calls.append((tenant, revision))
            return [{'id': tenant + revision, 'score': 0, 'payload': {}}]
        cache = api.RetrievalCache(source)
        for tenant, revision in [('t1', 'v1'), ('t2', 'v1'), ('t1', 'v2')]:
            with self.subTest(tenant=tenant, revision=revision):
                self.assertEqual(cache.search(tenant, query(), revision, 0)[0]['id'], tenant + revision)
        self.assertEqual(len(calls), 3)
        cache.search('t1', query(), 'v1', 1)
        self.assertEqual(len(calls), 3)

    def test_query_identity_and_filter_order(self):
        calls = []
        def source(t, q, r):
            calls.append(deepcopy(q))
            return [{'id': str(len(calls)), 'score': 0, 'payload': {}}]
        cache = api.RetrievalCache(source)
        first = cache.search('t', query(), 'r', 0)
        reordered = {'filters': {'b': '2', 'a': '1'}, 'text': 'search'}
        self.assertEqual(cache.search('t', reordered, 'r', 0), first)
        variants = [
            {'text': 'Search', 'filters': {'a': '1', 'b': '2'}},
            {'text': 'search', 'filters': {'A': '1', 'b': '2'}},
            {'text': 'search', 'filters': {'a': '0', 'b': '2'}},
            {'text': 'search', 'filters': {}},
            {'text': 'search\x00|雪', 'filters': {'a|b': '\x00'}},
        ]
        for q in variants:
            self.assertNotEqual(cache.search('t', q, 'r', 0), first)
        self.assertEqual(len(calls), 6)

    def test_source_query_mutation_isolated(self):
        retained = []
        def source(t, q, r):
            retained.append(q)
            q['text'] = 'changed'
            q['filters']['a'] = 'changed'
            return docs()
        cache = api.RetrievalCache(source)
        caller = query()
        before = deepcopy(caller)
        first = cache.search('t', caller, 'r', 0)
        self.assertEqual(caller, before)
        retained[0]['filters'].clear()
        self.assertEqual(caller, before)
        self.assertEqual(cache.search('t', caller, 'r', 1), first)
        self.assertEqual(len(retained), 1)

    def test_source_documents_snapshot(self):
        owned = docs()
        cache = api.RetrievalCache(lambda *args: owned)
        first = cache.search('t', query(), 'r', 0)
        owned[0]['payload']['items'][0]['value'] = 'changed'
        owned[0]['score'] = 100
        owned.pop()
        self.assertEqual(cache.search('t', query(), 'r', 1), first)

    def test_return_mutation_and_full_result(self):
        owned = docs()
        before = deepcopy(owned)
        calls = []
        def source(*args):
            calls.append(args)
            return owned
        cache = api.RetrievalCache(source)
        small = cache.search('t', query(), 'r', 0, limit=1)
        small[0]['payload']['new'] = []
        small.clear()
        large = cache.search('t', query(), 'r', 1, limit=100)
        self.assertEqual(len(large), 4)
        large[2]['payload']['items'][0]['value'] = 'changed'
        large[0]['score'] = -100
        self.assertEqual(cache.search('t', query(), 'r', 2, limit=100), api.rank_documents(before, 100))
        self.assertEqual(owned, before)
        self.assertEqual(len(calls), 1)

    def test_ttl_boundary_no_sliding(self):
        for ttl in (1, 10, 10 ** 30):
            with self.subTest(ttl=ttl):
                calls = []
                def source(*args):
                    calls.append(args)
                    return [{'id': str(len(calls)), 'score': 0, 'payload': {}}]
                cache = api.RetrievalCache(source, ttl)
                first = cache.search('t', query(), 'r', 5)
                self.assertEqual(cache.search('t', query(), 'r', 5 + ttl - 1), first)
                refreshed = cache.search('t', query(), 'r', 5 + ttl)
                self.assertNotEqual(refreshed, first)
                self.assertEqual(cache.search('t', query(), 'r', 5 + ttl), refreshed)
                self.assertEqual(len(calls), 2)

    def test_zero_limit_no_acquisition_or_refresh(self):
        calls = []
        def source(*args):
            calls.append(args)
            return docs()
        cache = api.RetrievalCache(source, 10)
        self.assertEqual(cache.search('t', query(), 'r', 0, 0), [])
        self.assertEqual(calls, [])
        cache.search('t', query(), 'r', 1)
        self.assertEqual(cache.search('t', query(), 'r', 9, 0), [])
        self.assertEqual(cache.search('t', query(), 'r', 11, 0), [])
        self.assertEqual(len(calls), 1)
        cache.search('t', query(), 'r', 11)
        self.assertEqual(len(calls), 2)

    def test_empty_success_is_cached(self):
        calls = []
        def source(*args):
            calls.append(args)
            return []
        cache = api.RetrievalCache(source)
        for now in (0, 1, 9, 10, 11):
            self.assertEqual(cache.search('t', query(), 'r', now), [])
        self.assertEqual(len(calls), 2)

    def test_exception_propagation_retry_and_other_entries(self):
        error = RuntimeError('source failure')
        calls = []
        fail = set()
        def source(t, q, r):
            calls.append(q['text'])
            if q['text'] in fail:
                q['filters'].clear()
                raise error
            return docs()
        cache = api.RetrievalCache(source, 10)
        q = query()
        other = {'text': 'other', 'filters': {}}
        cache.search('t', q, 'r', 0)
        cache.search('t', other, 'r', 5)
        fail.add('search')
        for now in (10, 11):
            with self.assertRaises(RuntimeError) as caught:
                cache.search('t', q, 'r', now)
            self.assertIs(caught.exception, error)
        self.assertEqual(q, query())
        cache.search('t', other, 'r', 11)
        self.assertEqual(calls, ['search', 'other', 'search', 'search'])
        fail.clear()
        cache.search('t', q, 'r', 12)
        cache.search('t', q, 'r', 13)
        self.assertEqual(len(calls), 5)

    def test_instance_isolation(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [{'id': str(len(calls)), 'score': 0, 'payload': {}}]
        a, b = api.RetrievalCache(source), api.RetrievalCache(source)
        one = a.search('t', query(), 'r', 0)
        two = b.search('t', query(), 'r', 0)
        self.assertNotEqual(one, two)
        self.assertEqual(a.search('t', query(), 'r', 1), one)
        self.assertEqual(b.search('t', query(), 'r', 1), two)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
