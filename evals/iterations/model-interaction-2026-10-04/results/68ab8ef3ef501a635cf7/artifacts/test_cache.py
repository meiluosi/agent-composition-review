"""Contract regression tests; optionally pass 'original' to test the baseline."""
from copy import deepcopy
import importlib
import random
import sys
import unittest

implementation = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app')
RetrievalCache = implementation.RetrievalCache
rank_documents = implementation.rank_documents


def document(identifier='a', score=1):
    return {'id': identifier, 'score': score,
            'payload': {'nested': [{'values': [None, True, 1, 1.5, 'text']} ]}}


def query():
    return {'text': 'search', 'filters': {'a': '1', 'b': '2'}}


class CacheContract(unittest.TestCase):
    def test_rank_limits_order_and_input(self):
        docs = [document('z', -1), document('b', 0), document('a', 0),
                document('huge', 10**100), document('negative', -(10**100))]
        before = deepcopy(docs)
        expected = ['huge', 'a', 'b', 'z', 'negative']
        for limit in range(8):
            with self.subTest(limit=limit):
                self.assertEqual([d['id'] for d in rank_documents(docs, limit)],
                                 expected[:limit])
        self.assertEqual(docs, before)
        self.assertEqual(rank_documents([], 0), [])
        self.assertEqual(rank_documents([], 10), [])

    def test_random_ranking(self):
        rng = random.Random(912)
        for count in range(35):
            docs = [document(str(i), rng.choice([-10**80, -1, 0, 1, 10**80]))
                    for i in range(count)]
            rng.shuffle(docs)
            # Independent ordering oracle: stable two-pass sorting.
            ordered = sorted(sorted(docs, key=lambda d: d['id']),
                             key=lambda d: d['score'], reverse=True)
            for limit in (0, 1, count // 2, count, count + 3):
                with self.subTest(count=count, limit=limit):
                    self.assertEqual(rank_documents(docs, limit), ordered[:limit])

    def test_rank_nested_copy_at_max_depth(self):
        payload = {'values': [None, False, 2.5, '尾']}
        for _ in range(18):
            payload = {'child': payload}
        docs = [{'id': 'nested', 'score': 0, 'payload': payload}]
        before = deepcopy(docs)
        result = rank_documents(docs, 1)
        cursor = result[0]['payload']
        for _ in range(18):
            cursor = cursor['child']
        cursor['values'].append('changed')
        result[0]['id'] = 'changed'
        self.assertEqual(docs, before)
        self.assertEqual(rank_documents(docs, 1), before)

    def test_full_result_and_default_limit(self):
        docs = [document(str(i), i) for i in range(15)]
        calls = []
        def source(*args):
            calls.append(args)
            return docs
        cache = RetrievalCache(source)
        expected = list(reversed(docs))
        self.assertEqual(cache.search('t', query(), 'r', 0, 1), expected[:1])
        self.assertEqual(cache.search('t', query(), 'r', 1, 20), expected)
        self.assertEqual(cache.search('t', query(), 'r', 2), expected[:10])
        self.assertEqual(len(calls), 1)

    def test_query_identity_and_instance_isolation(self):
        calls = []
        def source(*args):
            calls.append(deepcopy(args))
            return [document(str(len(calls)))]
        cache = RetrievalCache(source)
        variants = [
            ('t', query(), 'r'),
            ('other', query(), 'r'),
            ('t', {'text': 'different', 'filters': {'a': '1', 'b': '2'}}, 'r'),
            ('t', {'text': 'search', 'filters': {'a': '1', 'c': '2'}}, 'r'),
            ('t', {'text': 'search', 'filters': {'a': '1', 'b': '3'}}, 'r'),
            ('t', query(), 'other'),
            ('t', {'text': '', 'filters': {'': '\x00"\\尾'}}, 'r'),
        ]
        for index, (tenant, q, revision) in enumerate(variants, 1):
            self.assertEqual(cache.search(tenant, q, revision, 0)[0]['id'], str(index))
        for index, (tenant, q, revision) in enumerate(variants, 1):
            reordered = {'filters': dict(reversed(list(q['filters'].items()))),
                         'text': q['text']}
            self.assertEqual(cache.search(tenant, reordered, revision, 1)[0]['id'], str(index))
        self.assertEqual(len(calls), len(variants))
        other = RetrievalCache(source)
        self.assertEqual(other.search('t', query(), 'r', 1)[0]['id'], '8')

    def test_expiry_hits_do_not_extend_lifetime(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [document(str(len(calls)))]
        cache = RetrievalCache(source, ttl=3)
        for now, expected in [(5, '1'), (5, '1'), (7, '1'), (8, '2'),
                              (10, '2'), (11, '3'), (100, '4')]:
            self.assertEqual(cache.search('t', query(), 'r', now)[0]['id'], expected)
        self.assertEqual(len(calls), 4)

    def test_zero_limit_does_not_populate_or_refresh(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [document(str(len(calls)))]
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
        for now in (0, 0, 1, 1):
            self.assertEqual(cache.search('t', query(), 'r', now), [])
        self.assertEqual(len(calls), 2)

    def test_exception_identity_retry_and_other_entries(self):
        failure = RuntimeError('source failure')
        failing = set()
        calls = []
        def source(tenant, q, revision):
            calls.append(tenant)
            if tenant in failing:
                raise failure
            return [document(str(len(calls)))]
        cache = RetrievalCache(source, ttl=5)
        cache.search('old', query(), 'r', 0)
        valid = cache.search('valid', query(), 'r', 3)
        failing.update(['old', 'new'])
        for tenant in ('old', 'old', 'new', 'new'):
            with self.assertRaises(RuntimeError) as caught:
                cache.search(tenant, query(), 'r', 5)
            self.assertIs(caught.exception, failure)
        self.assertEqual(cache.search('valid', query(), 'r', 5), valid)
        self.assertEqual(calls, ['old', 'valid', 'old', 'old', 'new', 'new'])
        failing.clear()
        self.assertEqual(cache.search('old', query(), 'r', 6)[0]['id'], '7')
        self.assertEqual(cache.search('new', query(), 'r', 6)[0]['id'], '8')

    def test_source_and_caller_mutation_isolation(self):
        owned = [document()]
        expected = deepcopy(owned)
        retained = []
        def source(tenant, q, revision):
            retained.append(q)
            q['text'] = 'source changed'
            q['filters']['a'] = 'source changed'
            return owned
        cache = RetrievalCache(source)
        caller_query = query()
        result = cache.search('t', caller_query, 'r', 0)
        self.assertEqual(caller_query, query())
        result[0]['payload']['nested'][0]['values'].append('caller changed')
        result[0]['score'] = 99
        self.assertEqual(owned, expected)
        owned[0]['payload']['nested'][0]['values'].clear()
        owned.append(document('new'))
        retained[0]['filters'].clear()
        caller_query['filters'].clear()
        again = cache.search('t', query(), 'r', 1)
        self.assertEqual(again, expected)
        again.clear()
        self.assertEqual(cache.search('t', query(), 'r', 2), expected)
        self.assertEqual(len(retained), 1)


if __name__ == '__main__':
    unittest.main()
