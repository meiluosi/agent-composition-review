"""Contract regression tests; the delivered app does not depend on this file."""
import unittest
from copy import deepcopy
from itertools import permutations
from app import RetrievalCache, rank_documents


def doc(identifier, score=0):
    return {'id': identifier, 'score': score,
            'payload': {'items': [{'value': ['original', None, True, 1.25]}]}}


def query():
    return {'text': 'question', 'filters': {'a': '1', 'b': '2'}}


class ContractTests(unittest.TestCase):
    def test_ranking_limits_order_and_input_preservation(self):
        docs = [doc('z', -2), doc('a', 0), doc('β', 10**100),
                doc('A', 0), doc('a\x00', 0)]
        expected = ['β', 'A', 'a', 'a\x00', 'z']
        for arrangement in permutations(docs):
            source = list(arrangement)
            before = deepcopy(source)
            for limit in (0, 1, 2, 5, 9):
                result = rank_documents(source, limit)
                self.assertEqual([d['id'] for d in result], expected[:limit])
                self.assertEqual(source, before)
        self.assertEqual(rank_documents([], 5), [])

    def test_rank_copies_nested_payload_at_max_depth(self):
        payload = {'leaf': 'original'}
        for i in range(19):
            payload = {'child': payload} if i % 2 == 0 else [payload]
        docs = [{'id': 'x', 'score': 0, 'payload': payload}]
        before = deepcopy(docs)
        result = rank_documents(docs, 1)
        node = result[0]['payload']
        for i in reversed(range(19)):
            node = node['child'] if i % 2 == 0 else node[0]
        node['leaf'] = 'changed'
        result[0]['id'] = 'changed'
        self.assertEqual(docs, before)
        self.assertEqual(rank_documents(docs, 1), before)

    def test_full_results_and_instance_isolation(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc('low', -1), doc('high', 10), doc('mid', 0)]
        first = RetrievalCache(source)
        second = RetrievalCache(source)
        self.assertEqual(len(first.search('t', query(), 'r', 0, 1)), 1)
        self.assertEqual([d['id'] for d in first.search('t', query(), 'r', 1, 10)],
                         ['high', 'mid', 'low'])
        self.assertEqual(len(calls), 1)
        second.search('t', query(), 'r', 1)
        self.assertEqual(len(calls), 2)

    def test_all_identity_fields_and_filter_order(self):
        calls = []
        def source(*args):
            calls.append(deepcopy(args))
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source)
        base = ('tenant', query(), 'revision')
        self.assertEqual(cache.search(*base, 0)[0]['id'], '1')
        reordered = {'filters': {'b': '2', 'a': '1'}, 'text': 'question'}
        self.assertEqual(cache.search('tenant', reordered, 'revision', 0)[0]['id'], '1')
        variants = [
            ('other', query(), 'revision'),
            ('tenant', query(), 'other'),
            ('tenant', {'text': 'Question', 'filters': query()['filters']}, 'revision'),
            ('tenant', {'text': 'question', 'filters': {'A': '1', 'b': '2'}}, 'revision'),
            ('tenant', {'text': 'question', 'filters': {'a': '2', 'b': '2'}}, 'revision'),
            ('tenant', {'text': 'question', 'filters': {'a': '1'}}, 'revision'),
            ('tenant', {'text': 'question\x00"\\😀', 'filters': {}}, 'revision'),
        ]
        for number, identity in enumerate(variants, 2):
            self.assertEqual(cache.search(*identity, 0)[0]['id'], str(number))
        self.assertEqual(cache.search(*base, 0)[0]['id'], '1')
        self.assertEqual(len(calls), 8)

    def test_expiry_equality_and_hits_do_not_extend_ttl(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source, ttl=10)
        for now, expected in ((3, '1'), (3, '1'), (12, '1'), (13, '2'),
                              (22, '2'), (23, '3'), (100, '4')):
            self.assertEqual(cache.search('t', query(), 'r', now)[0]['id'], expected)
        self.assertEqual(len(calls), 4)

    def test_zero_limit_does_not_populate_or_refresh(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source, ttl=2)
        self.assertEqual(cache.search('t', query(), 'r', 0, 0), [])
        self.assertEqual(calls, [])
        self.assertEqual(cache.search('t', query(), 'r', 1)[0]['id'], '1')
        self.assertEqual(cache.search('t', query(), 'r', 2, 0), [])
        self.assertEqual(cache.search('t', query(), 'r', 3, 0), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.search('t', query(), 'r', 3)[0]['id'], '2')

    def test_empty_success_is_cached(self):
        calls = []
        def source(*args):
            calls.append(args)
            return []
        cache = RetrievalCache(source, ttl=1)
        for now in (0, 0):
            self.assertEqual(cache.search('t', query(), 'r', now), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.search('t', query(), 'r', 1), [])
        self.assertEqual(len(calls), 2)

    def test_exception_identity_retry_and_other_valid_entries(self):
        calls = []
        error = RuntimeError('source failed')
        failing = set()
        def source(tenant, request, revision):
            calls.append(tenant)
            if tenant in failing:
                raise error
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source, ttl=10)
        cache.search('old', query(), 'r', 0)
        valid = cache.search('valid', query(), 'r', 5)
        failing.update(('old', 'new'))
        for tenant, now in (('old', 10), ('old', 11), ('new', 11), ('new', 12)):
            with self.assertRaises(RuntimeError) as caught:
                cache.search(tenant, query(), 'r', now)
            self.assertIs(caught.exception, error)
            self.assertEqual(cache.search('valid', query(), 'r', now), valid)
        self.assertEqual(calls, ['old', 'valid', 'old', 'old', 'new', 'new'])
        failing.clear()
        self.assertEqual(cache.search('old', query(), 'r', 12)[0]['id'], '7')
        self.assertEqual(cache.search('new', query(), 'r', 12)[0]['id'], '8')

    def test_source_and_caller_mutation_isolation(self):
        owned = [doc('one', 2), doc('two', 1)]
        snapshot = deepcopy(owned)
        requests = []
        def source(tenant, request, revision):
            requests.append(request)
            request['filters']['a'] = 'mutated in source'
            request['text'] = 'mutated in source'
            return owned
        cache = RetrievalCache(source)
        request = query()
        result = cache.search('t', request, 'r', 0)
        self.assertEqual(request, query())
        result[0]['payload']['items'][0]['value'].append('caller change')
        result[0]['score'] = 999
        result.pop()
        self.assertEqual(owned, snapshot)
        requests[0]['filters'].clear()
        owned[0]['payload']['items'][0]['value'].clear()
        owned[0]['id'] = 'source change'
        owned.clear()
        self.assertEqual(cache.search('t', request, 'r', 1), snapshot)
        self.assertEqual(request, query())
        self.assertEqual(len(requests), 1)


if __name__ == '__main__':
    unittest.main()
