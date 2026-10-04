"""Contract regressions; run against app or the untouched original module."""
import importlib
import sys
import unittest
from copy import deepcopy


target = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app')


def doc(identifier='a', score=1):
    return {'id': identifier, 'score': score,
            'payload': {'nested': [{'values': [None, True, 1, 1.5, '字']} ]}}


def query(text='hello', **filters):
    return {'text': text, 'filters': filters}


class ContractTests(unittest.TestCase):
    def test_ranking_and_limits(self):
        documents = [doc('z', 0), doc('字', 4), doc('a', 4),
                     doc('negative', -10**100), doc('huge', 10**100)]
        before = deepcopy(documents)
        for limit in (0, 1, 3, 5, 100):
            self.assertEqual([d['id'] for d in target.rank_documents(documents, limit)],
                             ['huge', 'a', '字', 'z', 'negative'][:limit])
        self.assertEqual(documents, before)
        self.assertEqual(target.rank_documents([], 3), [])

    def test_rank_deep_copy_at_maximum_payload_depth(self):
        payload = {'leaf': [1]}
        # Payload contains 20 containers on the longest path.
        for _ in range(18):
            payload = {'next': payload}
        documents = [{'id': 'a', 'score': 0, 'payload': payload}]
        before = deepcopy(documents)
        result = target.rank_documents(documents, 1)
        leaf = result[0]['payload']
        for _ in range(18):
            leaf = leaf['next']
        leaf['leaf'].append(2)
        result[0]['score'] = 100
        result.append(doc())
        self.assertEqual(documents, before)
        self.assertEqual(target.rank_documents(documents, 1), before)

    def check_identity(self, identities):
        calls = []
        def source(tenant, request, revision):
            calls.append((tenant, deepcopy(request), revision))
            return [doc(str(len(calls)))]
        cache = target.RetrievalCache(source)
        for i, identity in enumerate(identities, 1):
            self.assertEqual(cache.search(*identity, now=0), [doc(str(i))])
        for i, identity in enumerate(identities, 1):
            self.assertEqual(cache.search(*identity, now=1), [doc(str(i))])
        self.assertEqual(len(calls), len(identities))

    def test_tenant_identity(self):
        self.check_identity([('one', query(), 'v1'), ('two', query(), 'v1')])

    def test_revision_identity(self):
        self.check_identity([('one', query(), 'v1'), ('one', query(), 'v2')])

    def test_query_identity(self):
        queries = [query(), query(''), query('字\x00'), query(a=''),
                   query(a='1'), query(b='1'), query(a='1', b='2'),
                   query(a='1|b:2'), query(**{'': '1', '字': '\x00'})]
        self.check_identity([('one', q, 'v1') for q in queries])

    def test_filter_order_is_irrelevant(self):
        calls = []
        cache = target.RetrievalCache(lambda *args: calls.append(args) or [doc()])
        first = query(a='1', b='2')
        second = {'filters': {'b': '2', 'a': '1'}, 'text': 'hello'}
        self.assertEqual(cache.search('t', first, 'v', 0),
                         cache.search('t', second, 'v', 1))
        self.assertEqual(len(calls), 1)

    def test_instances_are_private(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        first = target.RetrievalCache(source)
        second = target.RetrievalCache(source)
        self.assertEqual(first.search('t', query(), 'v', 0), [doc('1')])
        self.assertEqual(second.search('t', query(), 'v', 0), [doc('2')])
        self.assertEqual(first.search('t', query(), 'v', 1), [doc('1')])

    def test_full_result_cached_and_default_limit(self):
        documents = [doc(str(i), i) for i in range(15)]
        calls = []
        cache = target.RetrievalCache(lambda *args: calls.append(args) or documents)
        expected = list(reversed(documents))
        self.assertEqual(cache.search('t', query(), 'v', 0, 1), expected[:1])
        self.assertEqual(cache.search('t', query(), 'v', 1), expected[:10])
        self.assertEqual(cache.search('t', query(), 'v', 2, 20), expected)
        self.assertEqual(len(calls), 1)

    def test_expiration_boundary_and_hits_do_not_extend_ttl(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        cache = target.RetrievalCache(source, ttl=3)
        for now, expected in [(5, '1'), (5, '1'), (7, '1'),
                              (8, '2'), (10, '2'), (11, '3'), (100, '4')]:
            self.assertEqual(cache.search('t', query(), 'v', now), [doc(expected)])
        self.assertEqual(len(calls), 4)

    def test_empty_success_is_cached(self):
        calls = []
        cache = target.RetrievalCache(lambda *args: calls.append(args) or [])
        for now in (0, 1, 9):
            self.assertEqual(cache.search('t', query(), 'v', now), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.search('t', query(), 'v', 10), [])
        self.assertEqual(len(calls), 2)

    def test_zero_limit_neither_populates_nor_refreshes(self):
        calls = []
        cache = target.RetrievalCache(lambda *args: calls.append(args) or [doc()], ttl=2)
        self.assertEqual(cache.search('t', query(), 'v', 0, 0), [])
        self.assertEqual(len(calls), 0)
        cache.search('t', query(), 'v', 1)
        self.assertEqual(cache.search('t', query(), 'v', 2, 0), [])
        self.assertEqual(cache.search('t', query(), 'v', 3, 0), [])
        self.assertEqual(len(calls), 1)
        cache.search('t', query(), 'v', 3)
        self.assertEqual(len(calls), 2)

    def test_source_cannot_mutate_caller_or_key(self):
        held = []
        def source(tenant, request, revision):
            held.append(request)
            request['text'] = 'changed'
            request['filters']['a'] = 'changed'
            return [doc()]
        cache = target.RetrievalCache(source)
        request = query(a='original')
        before = deepcopy(request)
        cache.search('t', request, 'v', 0)
        self.assertEqual(request, before)
        held[0]['filters'].clear()
        self.assertEqual(request, before)
        self.assertEqual(cache.search('t', before, 'v', 1), [doc()])
        self.assertEqual(len(held), 1)

    def test_source_result_is_snapshotted(self):
        documents = [doc()]
        before = deepcopy(documents)
        cache = target.RetrievalCache(lambda *args: documents)
        result = cache.search('t', query(), 'v', 0)
        documents[0]['payload']['nested'][0]['values'].append('source mutation')
        documents[0]['score'] = -20
        documents.append(doc('new', 100))
        self.assertEqual(result, before)
        self.assertEqual(cache.search('t', query(), 'v', 1), before)

    def test_caller_results_and_query_are_independent(self):
        documents = [doc()]
        before = deepcopy(documents)
        calls = []
        cache = target.RetrievalCache(lambda *args: calls.append(args) or documents)
        request = query(a='1')
        for now in (0, 1):
            result = cache.search('t', request, 'v', now)
            result[0]['payload']['nested'][0]['values'].clear()
            result[0]['id'] = 'mutated'
            result.clear()
            self.assertEqual(documents, before)
        self.assertEqual(cache.search('t', request, 'v', 2), before)
        request['filters']['a'] = '2'
        cache.search('t', request, 'v', 3)
        self.assertEqual(len(calls), 2)
        cache.search('t', query(a='1'), 'v', 4)
        self.assertEqual(len(calls), 2)

    def test_exceptions_retry_and_preserve_other_valid_entries(self):
        failure = RuntimeError('source failed')
        calls = []
        failing = set()
        def source(tenant, request, revision):
            text = request['text']
            calls.append(text)
            if text in failing:
                request['filters'].clear()
                raise failure
            return [doc(text + str(len(calls)))]
        cache = target.RetrievalCache(source, ttl=10)
        old = cache.search('t', query('old'), 'v', 0)
        valid = cache.search('t', query('valid'), 'v', 5)
        failing.update(['old', 'new'])
        for text in ('old', 'new', 'old'):
            request = query(text, a='1') if text == 'new' else query(text)
            before = deepcopy(request)
            with self.assertRaises(RuntimeError) as caught:
                cache.search('t', request, 'v', 10)
            self.assertIs(caught.exception, failure)
            self.assertEqual(request, before)
        self.assertEqual(cache.search('t', query('valid'), 'v', 10), valid)
        self.assertEqual(calls, ['old', 'valid', 'old', 'new', 'old'])
        failing.clear()
        self.assertNotEqual(cache.search('t', query('old'), 'v', 11), old)
        cache.search('t', query('new', a='1'), 'v', 11)
        self.assertEqual(len(calls), 7)


if __name__ == '__main__':
    unittest.main()
