"""Business-contract checks; run with ./run_python test_contract.py."""
from copy import deepcopy
import random
import unittest

from app import RetrievalCache, rank_documents


def doc(identifier='one', score=1):
    return {'id': identifier, 'score': score,
            'payload': {'items': [{'value': [None, True, 1.25, '文本']} ]}}


def query(text='q', **filters):
    return {'text': text, 'filters': filters}


def expected_rank(documents, limit):
    # Two stable sorts express score precedence independently of app's key.
    by_id = sorted(documents, key=lambda item: item['id'])
    return deepcopy(sorted(by_id, key=lambda item: item['score'], reverse=True)[:limit])


class ContractTests(unittest.TestCase):
    def test_rank_order_limits_and_input_preservation(self):
        documents = [doc('z', 0), doc('a', 0), doc('负', -10**100),
                     doc('大', 10**100), doc('A', 0), doc('😀', 0)]
        before = deepcopy(documents)
        for limit in (0, 1, 3, 6, 100):
            self.assertEqual(rank_documents(documents, limit), expected_rank(documents, limit))
            self.assertEqual(documents, before)
        self.assertEqual(rank_documents([], 10), [])
        result = rank_documents(documents, 100)
        result[0]['payload']['items'][0]['value'].append('changed')
        result[0]['score'] = 999
        result.pop()
        self.assertEqual(documents, before)
        self.assertEqual(rank_documents(documents, 100), expected_rank(before, 100))

    def test_payload_at_twenty_container_depth(self):
        payload = {'leaf': [None, False, 3.5, 'x']}
        # Initial object and array count as two containers.
        for depth in range(18):
            payload = {'child': payload} if depth % 2 else [payload]
        self.assertIsInstance(payload, dict)
        documents = [{'id': 'deep', 'score': 0, 'payload': payload}]
        before = deepcopy(documents)
        cache = RetrievalCache(lambda *args: documents)
        for result in (rank_documents(documents, 1), cache.search('t', query(), 'r', 0)):
            leaf = result[0]['payload']
            for depth in reversed(range(18)):
                leaf = leaf['child'] if depth % 2 else leaf[0]
            leaf['leaf'].append('changed')
        self.assertEqual(documents, before)
        self.assertEqual(cache.search('t', query(), 'r', 1), before)

    def test_identity_and_filter_order(self):
        calls = []
        def source(tenant, request, revision):
            calls.append((tenant, deepcopy(request), revision))
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source, ttl=100)
        identities = [
            ('t', query('q', a='1', b='2'), 'r'),
            ('other', query('q', a='1', b='2'), 'r'),
            ('t', query('q', a='1', b='2'), 'other'),
            ('t', query('other', a='1', b='2'), 'r'),
            ('t', query('q', c='1', b='2'), 'r'),
            ('t', query('q', a='changed', b='2'), 'r'),
            ('t', query('q', a='1'), 'r'),
            ('t', query('q', a='1', b='2', c='3'), 'r'),
            ('t', query('q\x00\n"\\😀', **{'': '', 'é': 'e\u0301'}), 'r'),
            ('t', query('q\x00\n"\\😀', **{'': '', 'é': 'é'}), 'r'),
        ]
        for i, (tenant, request, revision) in enumerate(identities, 1):
            self.assertEqual(cache.search(tenant, request, revision, 0), [doc(str(i))])
        for i, (tenant, request, revision) in enumerate(identities, 1):
            reordered = {'filters': dict(reversed(list(request['filters'].items()))),
                         'text': request['text']}
            self.assertEqual(cache.search(tenant, reordered, revision, 1), [doc(str(i))])
        self.assertEqual(len(calls), len(identities))

    def test_full_result_and_fixed_expiration(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc('low', -2), doc('high', 2), doc('middle', 0)]
        cache = RetrievalCache(source, ttl=10)
        self.assertEqual(len(cache.search('t', query(), 'r', 0, 1)), 1)
        self.assertEqual(len(cache.search('t', query(), 'r', 9, 100)), 3)
        self.assertEqual(len(calls), 1)
        cache.search('t', query(), 'r', 10)
        self.assertEqual(len(calls), 2)
        cache.search('t', query(), 'r', 19)
        self.assertEqual(len(calls), 2)
        cache.search('t', query(), 'r', 20)
        self.assertEqual(len(calls), 3)

    def test_zero_limit_no_acquisition_or_refresh(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source, ttl=2)
        self.assertEqual(cache.search('t', query(), 'r', 0, 0), [])
        self.assertEqual(calls, [])
        self.assertEqual(cache.search('t', query(), 'r', 0), [doc('1')])
        self.assertEqual(cache.search('t', query(), 'r', 1, 0), [])
        self.assertEqual(cache.search('t', query(), 'r', 2, 0), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.search('t', query(), 'r', 2), [doc('2')])

    def test_empty_success_cached_and_instances_private(self):
        calls = []
        def source(*args):
            calls.append(args)
            return []
        first, second = RetrievalCache(source), RetrievalCache(source)
        for now in (0, 0, 9):
            self.assertEqual(first.search('t', query(), 'r', now), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(second.search('t', query(), 'r', 9), [])
        self.assertEqual(len(calls), 2)
        first.search('t', query(), 'r', 10)
        self.assertEqual(len(calls), 3)

    def test_source_and_caller_mutation_isolation(self):
        documents = [doc('b', 2), doc('a', 1)]
        before = deepcopy(documents)
        retained_queries = []
        def source(tenant, request, revision):
            retained_queries.append(request)
            request['text'] = 'source changed'
            request['filters']['x'] = 'source changed'
            return documents
        request = query('original', x='original')
        original_request = deepcopy(request)
        cache = RetrievalCache(source)
        result = cache.search('t', request, 'r', 0)
        self.assertEqual(request, original_request)
        self.assertEqual(documents, before)
        result[0]['payload']['items'][0]['value'].append('caller changed')
        result[0]['id'] = 'caller changed'
        result.clear()
        self.assertEqual(documents, before)
        documents[0]['payload']['items'][0]['value'].clear()
        documents.reverse()
        documents.append(doc('new', 100))
        retained_queries[0]['filters'].clear()
        request['filters']['x'] = 'caller changed'
        self.assertEqual(cache.search('t', original_request, 'r', 1), before)
        self.assertEqual(len(retained_queries), 1)

    def test_exception_identity_retry_and_other_entries(self):
        error = RuntimeError('source failure')
        calls = []
        fail = False
        def source(tenant, request, revision):
            calls.append(request['text'])
            if fail and request['text'] == 'a':
                raise error
            return [doc(str(len(calls)))]
        cache = RetrievalCache(source, ttl=10)
        self.assertEqual(cache.search('t', query('a'), 'r', 0), [doc('1')])
        self.assertEqual(cache.search('t', query('b'), 'r', 5), [doc('2')])
        fail = True
        for now in (10, 11):
            with self.assertRaises(RuntimeError) as caught:
                cache.search('t', query('a'), 'r', now)
            self.assertIs(caught.exception, error)
            self.assertEqual(cache.search('t', query('b'), 'r', now), [doc('2')])
        self.assertEqual(calls, ['a', 'b', 'a', 'a'])
        fail = False
        self.assertEqual(cache.search('t', query('a'), 'r', 12), [doc('5')])
        self.assertEqual(cache.search('t', query('a'), 'r', 13), [doc('5')])
        self.assertEqual(len(calls), 5)

    def test_failure_on_first_miss_does_not_cache(self):
        error = LookupError('first call')
        calls = []
        def source(*args):
            calls.append(args)
            if len(calls) == 1:
                raise error
            return []
        cache = RetrievalCache(source)
        with self.assertRaises(LookupError) as caught:
            cache.search('t', query(), 'r', 0)
        self.assertIs(caught.exception, error)
        self.assertEqual(cache.search('t', query(), 'r', 0), [])
        self.assertEqual(cache.search('t', query(), 'r', 1), [])
        self.assertEqual(len(calls), 2)

    def test_seeded_sequential_reference_model(self):
        rng = random.Random(2048)
        for ttl in (1, 3, 10, 10**30):
            acquired = {}
            now = 0
            call_count = 0
            error = ValueError('simulated failure')
            fail = False
            offered = []
            def source(*args):
                nonlocal call_count
                call_count += 1
                if fail:
                    raise error
                return offered
            cache = RetrievalCache(source, ttl)
            for step in range(500):
                now += rng.choice([0, 1, 2, 10])
                tenant = rng.choice(['t', 'u'])
                revision = rng.choice(['r1', 'r2'])
                text = rng.choice(['', 'q', '😀'])
                pairs = [('a', rng.choice(['1', '2'])), ('b', '3')]
                rng.shuffle(pairs)
                request = {'text': text, 'filters': dict(pairs)}
                identity = (tenant, text, frozenset(pairs), revision)
                limit = rng.choice([0, 1, 2, 10])
                offered = [doc(str(i), rng.choice([-10**50, -1, 0, 1, 10**50]))
                           for i in range(rng.randrange(8))]
                fail = rng.randrange(5) == 0
                previous_calls = call_count
                stored = acquired.get(identity)
                needs_source = limit > 0 and (stored is None or now - stored[0] >= ttl)
                if needs_source and fail:
                    with self.assertRaises(ValueError) as caught:
                        cache.search(tenant, request, revision, now, limit)
                    self.assertIs(caught.exception, error)
                else:
                    if needs_source:
                        acquired[identity] = (now, deepcopy(offered))
                    expected = [] if limit == 0 else expected_rank(acquired[identity][1], limit)
                    actual = cache.search(tenant, request, revision, now, limit)
                    self.assertEqual(actual, expected)
                    if actual:
                        actual[0]['payload']['items'].clear()
                self.assertEqual(call_count - previous_calls, int(needs_source))
                offered.clear()


if __name__ == '__main__':
    unittest.main(verbosity=2)
