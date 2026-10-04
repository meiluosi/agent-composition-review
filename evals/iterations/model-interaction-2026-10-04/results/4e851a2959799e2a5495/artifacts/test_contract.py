"""Business-contract regressions; run against app or the untouched original."""
import importlib
import sys
import unittest
from copy import deepcopy


MODULE = sys.argv.pop(1) if len(sys.argv) > 1 else 'app'
api = importlib.import_module(MODULE)


def doc(identifier='a', score=1, payload=None):
    return {'id': identifier, 'score': score,
            'payload': {'nested': [{'value': 'original'}]} if payload is None else payload}


def query():
    return {'text': 'search', 'filters': {'a': '1', 'b': '2'}}


class ContractTests(unittest.TestCase):
    def test_ranking_limits_order_and_input_preservation(self):
        documents = [doc('z', -1), doc('b', 0), doc('a', 0),
                     doc('large', 10**200), doc('negative', -(10**200)),
                     doc('\U00010000', 0), doc('\ue000', 0)]
        before = deepcopy(documents)
        expected_ids = ['large', 'a', 'b', '\ue000', '\U00010000', 'z', 'negative']
        for limit in [0, 1, 2, 6, 7, 8, 10**100]:
            with self.subTest(limit=limit):
                result = api.rank_documents(documents, limit)
                self.assertEqual([d['id'] for d in result], expected_ids[:limit])
                self.assertEqual(documents, before)
        self.assertEqual(api.rank_documents([], 0), [])
        self.assertEqual(api.rank_documents([], 3), [])

    def test_rank_copies_nested_payloads_to_depth_twenty(self):
        payload = {'leaf': [None, True, False, 1, 1.25, 'text']}
        # Initial object and list count as two containers; add eighteen.
        for i in range(18):
            payload = {'child': payload} if i % 2 else [payload]
        documents = [doc(payload=payload)]
        before = deepcopy(documents)
        first = api.rank_documents(documents, 1)
        second = api.rank_documents(documents, 1)
        node = first[0]['payload']
        for i in reversed(range(18)):
            node = node['child'] if i % 2 else node[0]
        node['leaf'].append('changed')
        first[0]['id'] = 'changed'
        self.assertEqual(documents, before)
        self.assertEqual(second, before)

    def test_full_result_cached_across_limits(self):
        calls = []
        documents = [doc('c', -1), doc('a', 3), doc('b', 0)]
        def source(*args):
            calls.append(args)
            return documents
        cache = api.RetrievalCache(source)
        self.assertEqual(cache.search('t', query(), 'r', 0, limit=1), [documents[1]])
        self.assertEqual(cache.search('t', query(), 'r', 1, limit=3),
                         [documents[1], documents[2], documents[0]])
        self.assertEqual(len(calls), 1)

    def test_default_search_limit(self):
        documents = [doc(str(i), i) for i in range(12)]
        cache = api.RetrievalCache(lambda *args: documents)
        self.assertEqual(cache.search('t', query(), 'r', 0), list(reversed(documents))[:10])

    def test_identity_and_filter_order(self):
        calls = []
        def source(tenant, q, revision):
            calls.append((tenant, deepcopy(q), revision))
            return [doc(str(len(calls)))]
        cache = api.RetrievalCache(source)
        cases = [
            ('t', query(), 'r'),
            ('other', query(), 'r'),
            ('t', query(), 'other'),
            ('t', {'text': 'different', 'filters': {'a': '1', 'b': '2'}}, 'r'),
            ('t', {'text': 'search', 'filters': {'c': '1', 'b': '2'}}, 'r'),
            ('t', {'text': 'search', 'filters': {'a': 'changed', 'b': '2'}}, 'r'),
            ('t', {'text': 'search', 'filters': {}}, 'r'),
            ('t', {'text': '\U00010000', 'filters': {'a': '1'}}, 'r'),
            ('t', {'text': '\ud800\udc00', 'filters': {'a': '1'}}, 'r'),
            ('t', {'text': 'search', 'filters': {'a:b': 'c'}}, 'r'),
            ('t', {'text': 'search', 'filters': {'a': 'b:c'}}, 'r'),
        ]
        for i, (tenant, q, revision) in enumerate(cases, 1):
            self.assertEqual(cache.search(tenant, q, revision, 0), [doc(str(i))])
        for i, (tenant, q, revision) in enumerate(cases, 1):
            reordered = {'filters': dict(reversed(list(q['filters'].items()))), 'text': q['text']}
            self.assertEqual(cache.search(tenant, reordered, revision, 1), [doc(str(i))])
        self.assertEqual(len(calls), len(cases))
        self.assertEqual(calls, cases)

    def test_ttl_boundary_and_hits_do_not_extend_lifetime(self):
        for ttl in [1, 10, 10**100]:
            with self.subTest(ttl=ttl):
                calls = []
                def source(*args):
                    calls.append(args)
                    return [doc(str(len(calls)))]
                cache = api.RetrievalCache(source, ttl=ttl)
                for now, expected in [(3, '1'), (3, '1'), (3 + ttl - 1, '1'),
                                      (3 + ttl, '2'), (3 + 2 * ttl - 1, '2'),
                                      (3 + 2 * ttl, '3')]:
                    self.assertEqual(cache.search('t', query(), 'r', now), [doc(expected)])
                self.assertEqual(len(calls), 3)

    def test_instances_are_private(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        first = api.RetrievalCache(source)
        second = api.RetrievalCache(source)
        self.assertEqual(first.search('t', query(), 'r', 0), [doc('1')])
        self.assertEqual(second.search('t', query(), 'r', 0), [doc('2')])
        self.assertEqual(first.search('t', query(), 'r', 1), [doc('1')])
        self.assertEqual(len(calls), 2)

    def test_empty_success_is_cached(self):
        calls = []
        def source(*args):
            calls.append(args)
            return []
        cache = api.RetrievalCache(source)
        for now in [0, 9, 10, 19]:
            self.assertEqual(cache.search('t', query(), 'r', now), [])
        self.assertEqual(len(calls), 2)

    def test_zero_limit_never_loads_or_refreshes(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        cache = api.RetrievalCache(source)
        self.assertEqual(cache.search('t', query(), 'r', 0, 0), [])
        self.assertEqual(calls, [])
        self.assertEqual(cache.search('t', query(), 'r', 1), [doc('1')])
        self.assertEqual(cache.search('t', query(), 'r', 9, 0), [])
        self.assertEqual(cache.search('t', query(), 'r', 11, 0), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.search('t', query(), 'r', 12), [doc('2')])
        self.assertEqual(len(calls), 2)

    def test_source_and_caller_mutations_are_isolated(self):
        documents = [doc('a'), doc('b', 0)]
        snapshot = deepcopy(documents)
        retained = []
        q = query()
        q_before = deepcopy(q)
        def source(tenant, source_query, revision):
            retained.append(source_query)
            source_query['filters']['a'] = 'source changed'
            source_query['text'] = 'source changed'
            return documents
        cache = api.RetrievalCache(source)
        result = cache.search('t', q, 'r', 0)
        self.assertEqual(q, q_before)
        result[0]['payload']['nested'][0]['value'] = 'caller changed'
        result[0]['id'] = 'caller changed'
        result.pop()
        self.assertEqual(documents, snapshot)
        documents[0]['payload']['nested'][0]['value'] = 'source changed'
        documents.clear()
        retained[0]['filters'].clear()
        self.assertEqual(q, q_before)
        q['filters'].clear()
        again = cache.search('t', q_before, 'r', 1)
        self.assertEqual(again, snapshot)
        again[0]['payload']['nested'].clear()
        self.assertEqual(cache.search('t', q_before, 'r', 2), snapshot)
        self.assertEqual(len(retained), 1)

    def test_exceptions_propagate_retry_and_preserve_other_entries(self):
        error = RuntimeError('source failed')
        calls = []
        failures = set()
        def source(tenant, q, revision):
            calls.append(tenant)
            if tenant in failures:
                q['filters'].clear()
                raise error
            return [doc(tenant + str(calls.count(tenant)))]
        cache = api.RetrievalCache(source)
        q = query()
        self.assertEqual(cache.search('a', q, 'r', 0), [doc('a1')])
        self.assertEqual(cache.search('b', q, 'r', 5), [doc('b1')])
        failures.update(['a', 'new'])
        for now, tenant in [(10, 'a'), (11, 'a'), (11, 'new')]:
            with self.assertRaises(RuntimeError) as raised:
                cache.search(tenant, q, 'r', now)
            self.assertIs(raised.exception, error)
            self.assertEqual(q, query())
            self.assertEqual(cache.search('b', q, 'r', now), [doc('b1')])
        failures.clear()
        self.assertEqual(cache.search('a', q, 'r', 12), [doc('a4')])
        self.assertEqual(cache.search('new', q, 'r', 12), [doc('new2')])
        self.assertEqual(cache.search('a', q, 'r', 13), [doc('a4')])
        self.assertEqual(calls, ['a', 'b', 'a', 'a', 'new', 'a', 'new'])


if __name__ == '__main__':
    unittest.main()
