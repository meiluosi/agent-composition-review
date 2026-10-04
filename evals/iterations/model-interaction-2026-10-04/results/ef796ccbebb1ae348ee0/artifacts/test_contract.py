"""Supplementary contract checks; run with ./run_python test_contract.py."""
from copy import deepcopy
import random
import unittest

from app import RetrievalCache, rank_documents


def doc(identifier='a', score=1):
    return {'id': identifier, 'score': score,
            'payload': {'nested': [{'values': [None, True, 2, 1.5, '中文']}]}}


def query(text='text', filters=None):
    return {'text': text, 'filters': {} if filters is None else filters}


class ContractTests(unittest.TestCase):
    def test_ranking_and_limits(self):
        documents = [doc('z', -5), doc('a', 0), doc('b', 0),
                     doc('big', 10 ** 5000), doc('small', -(10 ** 5000)),
                     doc('\U0001f600', 0), doc('\ud800', 0), doc('A', 0)]
        expected = ['big', 'A', 'a', 'b', '\ud800', '\U0001f600', 'z', 'small']
        before = deepcopy(documents)
        for limit in [0, 1, 3, len(documents), 100]:
            self.assertEqual([d['id'] for d in rank_documents(documents, limit)],
                             expected[:limit])
        self.assertEqual(documents, before)
        self.assertEqual(rank_documents([], 10), [])

    def test_twenty_containers_and_independent_ranking(self):
        payload = {'value': 'leaf'}
        for depth in range(19):
            payload = {'child': payload} if depth % 2 == 0 else [payload]
        documents = [{'id': 'deep', 'score': 0, 'payload': payload}]
        result = rank_documents(documents, 1)
        original_node, result_node = payload, result[0]['payload']
        for depth in range(19):
            self.assertIsNot(original_node, result_node)
            key = 'child' if isinstance(original_node, dict) else 0
            original_node, result_node = original_node[key], result_node[key]
        self.assertIsNot(original_node, result_node)
        result_node['value'] = 'changed'
        self.assertEqual(original_node['value'], 'leaf')
        self.assertEqual(rank_documents(documents, 1), documents)

    def test_query_identity(self):
        calls = []

        def source(tenant, request, revision):
            calls.append((tenant, deepcopy(request), revision))
            return [doc(str(len(calls)))]

        cache = RetrievalCache(source, ttl=100)
        identities = [
            ('team', query('text', {'a': '1', 'b': '2'}), 'v1'),
            ('other', query('text', {'a': '1', 'b': '2'}), 'v1'),
            ('team', query('other', {'a': '1', 'b': '2'}), 'v1'),
            ('team', query('text', {'a': '1', 'c': '2'}), 'v1'),
            ('team', query('text', {'a': '1', 'b': '3'}), 'v1'),
            ('team', query('text', {'a': '1', 'b': '2'}), 'v2'),
            ('team', query('text', {'a': '1'}), 'v1'),
            ('team', query('text', {'a': '1', 'b': ''}), 'v1'),
            ('team', query('a|b', {'c': 'd'}), 'v1'),
            ('team', query('a', {'b|c': 'd'}), 'v1'),
            ('team', query('\U0001f600', {'\x00': '\\"'}), 'v1'),
            ('team', query('\ud83d\ude00', {'\x00': '\\"'}), 'v1'),
            ('team', query('é'), 'v1'),
            ('team', query('e\u0301'), 'v1'),
        ]
        for index, (tenant, request, revision) in enumerate(identities, 1):
            self.assertEqual(cache.search(tenant, request, revision, 0)[0]['id'],
                             str(index))
        for index, (tenant, request, revision) in enumerate(identities, 1):
            reordered = {'filters': dict(reversed(list(request['filters'].items()))),
                         'text': request['text']}
            self.assertEqual(cache.search(tenant, reordered, revision, 1)[0]['id'],
                             str(index))
        self.assertEqual(len(calls), len(identities))

    def test_full_result_empty_result_and_instance_isolation(self):
        calls = []
        documents = [doc('low', -1), doc('high', 10), doc('middle', 0)]

        def source(tenant, request, revision):
            calls.append(tenant)
            return [] if tenant == 'empty' else documents

        first, second = RetrievalCache(source), RetrievalCache(source)
        self.assertEqual(len(first.search('t', query(), 'r', 0, 1)), 1)
        self.assertEqual(len(first.search('t', query(), 'r', 1, 100)), 3)
        second.search('t', query(), 'r', 1)
        self.assertEqual(calls, ['t', 't'])
        for now in [2, 3, 9, 11]:
            self.assertEqual(first.search('empty', query(), 'r', now), [])
        self.assertEqual(calls, ['t', 't', 'empty'])
        first.search('empty', query(), 'r', 12)
        self.assertEqual(calls, ['t', 't', 'empty', 'empty'])

    def test_ttl_boundary_and_no_sliding_expiration(self):
        calls = []

        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]

        cache = RetrievalCache(source, ttl=3)
        for now, expected in [(5, '1'), (5, '1'), (7, '1'), (8, '2'),
                              (10, '2'), (11, '3'), (100000, '4')]:
            self.assertEqual(cache.search('t', query(), 'r', now)[0]['id'], expected)
        self.assertEqual(len(calls), 4)

    def test_zero_limit_never_fetches_or_refreshes(self):
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

    def test_query_source_and_returned_data_ownership(self):
        source_documents = [doc()]
        retained_queries = []

        def source(tenant, request, revision):
            retained_queries.append(request)
            request['text'] = 'source changed'
            request['filters']['key'] = 'source changed'
            return source_documents

        cache = RetrievalCache(source)
        request = query('original', {'key': 'original'})
        expected_query = deepcopy(request)
        expected_documents = deepcopy(source_documents)
        result = cache.search('t', request, 'r', 0)
        self.assertEqual(request, expected_query)
        result[0]['payload']['nested'][0]['values'].append('caller changed')
        result[0]['id'] = 'changed'
        self.assertEqual(source_documents, expected_documents)
        source_documents[0]['payload']['nested'][0]['values'].clear()
        source_documents.clear()
        retained_queries[0]['filters'].clear()
        request['filters']['key'] = 'caller changed'
        self.assertEqual(cache.search('t', expected_query, 'r', 1), expected_documents)
        self.assertEqual(len(retained_queries), 1)

    def test_exception_identity_retry_and_other_entries(self):
        error = RuntimeError('source unavailable')
        fail = False
        calls = []

        def source(tenant, request, revision):
            calls.append(tenant)
            if fail:
                raise error
            return [doc(str(len(calls)))]

        cache = RetrievalCache(source, ttl=10)
        cache.search('old', query(), 'r', 0)
        valid = cache.search('valid', query(), 'r', 5)
        fail = True
        for tenant in ['new', 'new', 'old', 'old']:
            with self.assertRaises(RuntimeError) as caught:
                cache.search(tenant, query(), 'r', 10)
            self.assertIs(caught.exception, error)
            self.assertEqual(cache.search('valid', query(), 'r', 10), valid)
        self.assertEqual(calls, ['old', 'valid', 'new', 'new', 'old', 'old'])
        fail = False
        self.assertEqual(cache.search('old', query(), 'r', 10)[0]['id'], '7')
        self.assertEqual(cache.search('old', query(), 'r', 11)[0]['id'], '7')

    def test_deterministic_model_sequence(self):
        rng = random.Random(20261004)
        calls = 0
        fail = False
        current_documents = []
        error = ValueError('transient')

        def source(*args):
            nonlocal calls
            calls += 1
            if fail:
                raise error
            return current_documents

        cache = RetrievalCache(source, ttl=7)
        model = {}
        now = 0
        for step in range(500):
            now += rng.randrange(3)
            tenant, revision = rng.choice(['a', 'b']), rng.choice(['r1', 'r2'])
            request = query(rng.choice(['', 'q']), {'k': rng.choice(['x', 'y'])})
            limit = rng.choice([0, 1, 3, 20])
            fail = rng.randrange(5) == 0
            current_documents = [doc(str(i), rng.randint(-10, 10))
                                 for i in range(rng.randrange(6))]
            identity = (tenant, request['text'], tuple(sorted(request['filters'].items())),
                        revision)
            before_calls = calls
            entry = model.get(identity)
            needs_source = limit > 0 and (entry is None or now >= entry[0])
            if needs_source and fail:
                with self.assertRaises(ValueError) as caught:
                    cache.search(tenant, request, revision, now, limit)
                self.assertIs(caught.exception, error)
            else:
                if needs_source:
                    model[identity] = (now + 7, deepcopy(current_documents))
                expected = [] if limit == 0 else sorted(
                    model[identity][1], key=lambda d: (-d['score'], d['id']))[:limit]
                result = cache.search(tenant, request, revision, now, limit)
                self.assertEqual(result, expected, f'step {step}')
                if result:
                    result[0]['payload']['nested'].clear()
                current_documents.clear()
            self.assertEqual(calls - before_calls, int(needs_source), f'step {step}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
