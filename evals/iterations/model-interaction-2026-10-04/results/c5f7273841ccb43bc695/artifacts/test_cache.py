"""Contract regressions; optionally pass 'original' to check the supplied baseline."""
from copy import deepcopy
import importlib
import sys
import unittest

implementation = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app')
RetrievalCache = implementation.RetrievalCache
rank_documents = implementation.rank_documents


def doc(identifier='x', score=1):
    return {'id': identifier, 'score': score,
            'payload': {'nested': [{'values': ['initial', None, True, 1.5]}]}}


def query():
    return {'text': 'hello', 'filters': {'a': '1', 'b': '2'}}


class ContractTests(unittest.TestCase):
    def test_rank_order_limits_and_input_preservation(self):
        documents = [doc('z', 0), doc('b', -5), doc('a', -5),
                     doc('huge', 10 ** 100), doc('A', 0), doc('é', 0)]
        before = deepcopy(documents)
        expected = ['huge', 'A', 'z', 'é', 'a', 'b']
        for limit in (0, 1, 3, 6, 100):
            self.assertEqual([d['id'] for d in rank_documents(documents, limit)],
                             expected[:limit])
        self.assertEqual(documents, before)
        self.assertEqual(rank_documents([], 10), [])

    def test_rank_independent_nested_copies_at_depth_boundary(self):
        # One payload object plus nineteen alternating nested containers.
        payload = {}
        current = payload
        for depth in range(19):
            child = [] if depth % 2 == 0 else {}
            if isinstance(current, dict):
                current['child'] = child
            else:
                current.append(child)
            current = child
        documents = [{'id': 'x', 'score': 0, 'payload': payload}]
        before = deepcopy(documents)
        first = rank_documents(documents, 1)
        second = rank_documents(documents, 1)
        current = first[0]['payload']
        for depth in range(19):
            current = current['child'] if isinstance(current, dict) else current[0]
        current.append('changed')
        self.assertEqual(documents, before)
        self.assertEqual(second, before)

    def test_every_identity_component(self):
        cases = [
            ('team', query(), 'v1'),
            ('other', query(), 'v1'),
            ('team', query(), 'v2'),
            ('team', {'text': 'different', 'filters': {'a': '1', 'b': '2'}}, 'v1'),
            ('team', {'text': 'hello', 'filters': {'c': '1', 'b': '2'}}, 'v1'),
            ('team', {'text': 'hello', 'filters': {'a': '3', 'b': '2'}}, 'v1'),
            ('team', {'text': 'hello', 'filters': {}}, 'v1'),
            ('team', {'text': 'hello', 'filters': {'a': '1', 'b': '2', '': ''}}, 'v1'),
        ]
        calls = []
        def source(tenant, q, revision):
            calls.append((tenant, deepcopy(q), revision))
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source)
        for index, args in enumerate(cases, 1):
            with self.subTest(args=args):
                self.assertEqual(cache.search(*args, now=0)[0]['id'], str(index))
        for index, args in reversed(list(enumerate(cases, 1))):
            self.assertEqual(cache.search(*args, now=1)[0]['id'], str(index))
        self.assertEqual(len(calls), len(cases))

    def test_filter_and_query_insertion_order(self):
        calls = []
        cache = RetrievalCache(lambda *args: calls.append(args) or [doc()])
        cache.search('team', query(), 'v1', 0)
        reordered = {'filters': {'b': '2', 'a': '1'}, 'text': 'hello'}
        self.assertEqual(cache.search('team', reordered, 'v1', 1), [doc()])
        self.assertEqual(len(calls), 1)

    def test_cache_instances_are_private(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        first, second = RetrievalCache(source), RetrievalCache(source)
        self.assertEqual(first.search('t', query(), 'r', 0)[0]['id'], '1')
        self.assertEqual(second.search('t', query(), 'r', 0)[0]['id'], '2')
        self.assertEqual(first.search('t', query(), 'r', 1)[0]['id'], '1')

    def test_full_result_retained_across_limits(self):
        documents = [doc('low', -1), doc('high', 10), doc('middle', 0)]
        before = deepcopy(documents)
        calls = []
        cache = RetrievalCache(lambda *args: calls.append(args) or documents)
        self.assertEqual(len(cache.search('t', query(), 'r', 0, 1)), 1)
        self.assertEqual([d['id'] for d in cache.search('t', query(), 'r', 1, 20)],
                         ['high', 'middle', 'low'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(documents, before)

    def test_ttl_boundary_and_hits_do_not_refresh(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source, ttl=3)
        for now, expected in [(2, '1'), (2, '1'), (4, '1'), (5, '2'),
                              (7, '2'), (8, '3'), (100, '4')]:
            self.assertEqual(cache.search('t', query(), 'r', now)[0]['id'], expected)
        self.assertEqual(len(calls), 4)

    def test_empty_success_is_cached(self):
        calls = []
        cache = RetrievalCache(lambda *args: calls.append(args) or [], ttl=1)
        for now in (0, 0, 1):
            self.assertEqual(cache.search('t', query(), 'r', now), [])
        self.assertEqual(len(calls), 2)

    def test_zero_does_not_fetch_populate_or_refresh(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source, ttl=2)
        self.assertEqual(cache.search('t', query(), 'r', 0, 0), [])
        self.assertEqual(calls, [])
        cache.search('t', query(), 'r', 1)
        self.assertEqual(cache.search('t', query(), 'r', 2, 0), [])
        self.assertEqual(cache.search('t', query(), 'r', 3, 0), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.search('t', query(), 'r', 3)[0]['id'], '2')

    def test_source_query_is_isolated_even_when_retained(self):
        retained = []
        def source(tenant, q, revision):
            retained.append(q)
            q['text'] = 'modified'
            q['filters']['a'] = 'modified'
            return [doc()]
        cache = RetrievalCache(source)
        caller = query()
        cache.search('t', caller, 'r', 0)
        self.assertEqual(caller, query())
        retained[0]['filters'].clear()
        self.assertEqual(caller, query())
        self.assertEqual(cache.search('t', query(), 'r', 1), [doc()])
        self.assertEqual(len(retained), 1)

    def test_source_documents_are_snapshotted(self):
        documents = [doc()]
        expected = deepcopy(documents)
        calls = []
        cache = RetrievalCache(lambda *args: calls.append(args) or documents)
        cache.search('t', query(), 'r', 0)
        documents[0]['payload']['nested'][0]['values'].append('later mutation')
        documents[0]['score'] = 100
        documents.append(doc('new'))
        self.assertEqual(cache.search('t', query(), 'r', 1), expected)
        documents.clear()
        self.assertEqual(cache.search('t', query(), 'r', 2), expected)
        self.assertEqual(len(calls), 1)

    def test_returned_documents_are_independent_on_miss_and_hit(self):
        documents = [doc()]
        expected = deepcopy(documents)
        cache = RetrievalCache(lambda *args: documents)
        for now in (0, 1):
            result = cache.search('t', query(), 'r', now)
            result[0]['payload']['nested'][0]['values'].clear()
            result[0]['id'] = 'changed'
            result.append(doc('extra'))
            self.assertEqual(documents, expected)
        self.assertEqual(cache.search('t', query(), 'r', 2), expected)

    def test_exceptions_propagate_retry_and_preserve_other_entries(self):
        error = RuntimeError('source unavailable')
        fail = False
        calls = []
        def source(tenant, q, revision):
            calls.append(q['text'])
            if fail:
                q['filters'].clear()
                raise error
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source, ttl=5)
        a = query()
        b = {'text': 'other', 'filters': {}}
        cache.search('t', a, 'r', 0)
        cache.search('t', b, 'r', 2)
        fail = True
        for now in (5, 6):
            with self.assertRaises(RuntimeError) as caught:
                cache.search('t', a, 'r', now)
            self.assertIs(caught.exception, error)
            self.assertEqual(a, query())
        self.assertEqual(cache.search('t', b, 'r', 6)[0]['id'], '2')
        fail = False
        self.assertEqual(cache.search('t', a, 'r', 6)[0]['id'], '5')
        self.assertEqual(len(calls), 5)

    def test_initial_source_failure_can_retry(self):
        calls = []
        error = ValueError('first call fails')
        def source(*args):
            calls.append(args)
            if len(calls) == 1:
                raise error
            return []
        cache = RetrievalCache(source)
        with self.assertRaises(ValueError) as caught:
            cache.search('t', query(), 'r', 0)
        self.assertIs(caught.exception, error)
        self.assertEqual(cache.search('t', query(), 'r', 0), [])
        self.assertEqual(cache.search('t', query(), 'r', 1), [])
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
