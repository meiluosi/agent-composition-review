"""Contract regression tests; optional module argument selects the implementation."""
import importlib
import sys
import unittest
from copy import deepcopy

implementation = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app')
RetrievalCache = implementation.RetrievalCache
rank_documents = implementation.rank_documents


def document(identifier='a', score=1):
    return {'id': identifier, 'score': score,
            'payload': {'nested': [{'value': 'original'}]}}


def query():
    return {'text': 'search', 'filters': {'x': '1', 'y': '2'}}


class ContractTests(unittest.TestCase):
    def test_ranking_and_independent_copies(self):
        documents = [document('z', 0), document('b', 10**100),
                     document('a', 10**100), document('negative', -10**100),
                     document('é', 0), document('A', 0)]
        before = deepcopy(documents)
        self.assertEqual(rank_documents(documents, 0), [])
        self.assertEqual(rank_documents([], 10), [])
        self.assertEqual([d['id'] for d in rank_documents(documents, 99)],
                         ['a', 'b', 'A', 'z', 'é', 'negative'])
        ranked = rank_documents(documents, 2)
        self.assertEqual([d['id'] for d in ranked], ['a', 'b'])
        ranked[0]['payload']['nested'][0]['value'] = 'changed'
        ranked[1]['score'] = 42
        self.assertEqual(documents, before)
        self.assertEqual(rank_documents(documents, 2), [before[2], before[1]])

    def test_maximum_payload_depth(self):
        payload = {'values': None, 'number': 1.25, 'flag': True}
        for index in range(19):
            payload = {'child': payload} if index % 2 == 0 else [payload]
        documents = [{'id': 'deep', 'score': 0, 'payload': payload}]
        self.assertEqual(rank_documents(documents, 1), documents)
        cache = RetrievalCache(lambda *args: documents)
        result = cache.search('t', query(), 'r', 0)
        result[0]['payload'].clear()
        self.assertEqual(cache.search('t', query(), 'r', 1), documents)

    def test_every_identity_component(self):
        base = query()
        requests = [
            ('t', base, 'r'),
            ('other tenant', base, 'r'),
            ('t', base, 'other revision'),
            ('t', {'text': 'different', 'filters': base['filters']}, 'r'),
            ('t', {'text': 'search', 'filters': {'z': '1', 'y': '2'}}, 'r'),
            ('t', {'text': 'search', 'filters': {'x': 'changed', 'y': '2'}}, 'r'),
            ('t', {'text': 'search', 'filters': {'x': '1'}}, 'r'),
            ('t|r', {'text': '\u0000雪', 'filters': {'': '"|:'}}, 'r'),
            ('t', {'text': '\u0000雪', 'filters': {'': '"|:'}}, 'r|r'),
        ]
        calls = []
        def source(*args):
            calls.append(deepcopy(args))
            return [document(str(len(calls)))]
        cache = RetrievalCache(source)
        for index, args in enumerate(requests, 1):
            self.assertEqual(cache.search(*args, now=0)[0]['id'], str(index))
        for index, args in enumerate(requests, 1):
            self.assertEqual(cache.search(*args, now=1)[0]['id'], str(index))
        self.assertEqual(len(calls), len(requests))

    def test_filter_order_is_irrelevant(self):
        calls = []
        cache = RetrievalCache(lambda *args: calls.append(args) or [document()])
        cache.search('t', query(), 'r', 0)
        reordered = {'filters': {'y': '2', 'x': '1'}, 'text': 'search'}
        cache.search('t', reordered, 'r', 1)
        self.assertEqual(len(calls), 1)

    def test_cache_is_private_to_instance(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [document(str(len(calls)))]
        first, second = RetrievalCache(source), RetrievalCache(source)
        self.assertEqual(first.search('t', query(), 'r', 0)[0]['id'], '1')
        self.assertEqual(second.search('t', query(), 'r', 0)[0]['id'], '2')
        self.assertEqual(first.search('t', query(), 'r', 1)[0]['id'], '1')

    def test_full_result_retained_and_ttl_does_not_slide(self):
        calls = []
        documents = [document('a', 1), document('b', 2), document('c', 3)]
        def source(*args):
            calls.append(args)
            return documents
        cache = RetrievalCache(source, ttl=10)
        self.assertEqual(len(cache.search('t', query(), 'r', 5, 1)), 1)
        self.assertEqual(len(cache.search('t', query(), 'r', 14, 100)), 3)
        self.assertEqual(len(calls), 1)
        cache.search('t', query(), 'r', 15)
        self.assertEqual(len(calls), 2)
        cache.search('t', query(), 'r', 24)
        self.assertEqual(len(calls), 2)
        cache.search('t', query(), 'r', 25)
        self.assertEqual(len(calls), 3)

    def test_zero_limit_does_not_populate_or_refresh(self):
        calls = []
        cache = RetrievalCache(lambda *args: calls.append(args) or [document()], ttl=2)
        self.assertEqual(cache.search('t', query(), 'r', 0, 0), [])
        self.assertEqual(calls, [])
        cache.search('t', query(), 'r', 1)
        self.assertEqual(cache.search('t', query(), 'r', 2, 0), [])
        self.assertEqual(cache.search('t', query(), 'r', 3, 0), [])
        self.assertEqual(len(calls), 1)
        cache.search('t', query(), 'r', 3)
        self.assertEqual(len(calls), 2)

    def test_empty_success_is_cached(self):
        calls = []
        cache = RetrievalCache(lambda *args: calls.append(args) or [], ttl=1)
        self.assertEqual(cache.search('t', query(), 'r', 0), [])
        self.assertEqual(cache.search('t', query(), 'r', 0), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.search('t', query(), 'r', 1), [])
        self.assertEqual(len(calls), 2)

    def test_source_cannot_mutate_caller_query(self):
        caller_query = query()
        before = deepcopy(caller_query)
        retained = []
        def source(tenant, supplied, revision):
            retained.append(supplied)
            supplied['filters']['x'] = 'during call'
            supplied['text'] = 'changed'
            return [document()]
        cache = RetrievalCache(source)
        cache.search('t', caller_query, 'r', 0)
        self.assertEqual(caller_query, before)
        retained[0]['filters'].clear()
        self.assertEqual(caller_query, before)
        cache.search('t', before, 'r', 1)
        self.assertEqual(len(retained), 1)

    def test_source_retained_documents_cannot_mutate_cache(self):
        documents = [document()]
        before = deepcopy(documents)
        cache = RetrievalCache(lambda *args: documents)
        cache.search('t', query(), 'r', 0)
        documents[0]['payload']['nested'][0]['value'] = 'source mutation'
        documents[0]['id'] = 'changed'
        documents.append(document('new', 100))
        self.assertEqual(cache.search('t', query(), 'r', 1), before)

    def test_returned_documents_cannot_mutate_source_or_cache(self):
        documents = [document()]
        before = deepcopy(documents)
        cache = RetrievalCache(lambda *args: documents)
        for now in [0, 1]:
            result = cache.search('t', query(), 'r', now)
            result[0]['payload']['nested'][0]['value'] = 'caller mutation'
            result[0]['score'] = -999
            result.clear()
            self.assertEqual(documents, before)
        self.assertEqual(cache.search('t', query(), 'r', 2), before)

    def test_source_exceptions_propagate_and_retry_without_losing_other_entries(self):
        error = RuntimeError('source failed')
        failing = {'value': False}
        calls = []
        def source(tenant, supplied, revision):
            calls.append(supplied['text'])
            if failing['value']:
                supplied['filters'].clear()
                raise error
            return [document(str(len(calls)))]
        cache = RetrievalCache(source, ttl=10)
        first_query, other_query = query(), {'text': 'other', 'filters': {}}
        first = cache.search('t', first_query, 'r', 0)
        other = cache.search('t', other_query, 'r', 5)
        failing['value'] = True
        for now in [10, 11]:
            with self.assertRaises(RuntimeError) as raised:
                cache.search('t', first_query, 'r', now)
            self.assertIs(raised.exception, error)
            self.assertEqual(first_query, query())
            self.assertEqual(cache.search('t', other_query, 'r', now), other)
        self.assertEqual(len(calls), 4)
        failing['value'] = False
        refreshed = cache.search('t', first_query, 'r', 12)
        self.assertNotEqual(refreshed, first)
        self.assertEqual(cache.search('t', first_query, 'r', 13), refreshed)
        self.assertEqual(len(calls), 5)


if __name__ == '__main__':
    unittest.main()
