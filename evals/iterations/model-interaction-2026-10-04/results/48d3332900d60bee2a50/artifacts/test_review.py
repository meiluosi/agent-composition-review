"""Contract regression tests, shared by the original and delivered modules."""
import copy
import random
import unittest

import app
import original


def doc(identifier='a', score=1):
    return {'id': identifier, 'score': score,
            'payload': {'nested': [{'values': [None, True, 1.5, 'text']} ]}}


def query(text='text', **filters):
    return {'text': text, 'filters': filters}


class ContractTests:
    def test_ranking_and_input_preservation(self):
        documents = [doc('negative', -10**200), doc('zero', 0),
                     doc('z', 10**200), doc('a', 10**200),
                     doc('\U00010000', 3), doc('\ue000', 3)]
        before = copy.deepcopy(documents)
        for limit in (0, 1, 3, 6, 20):
            result = self.module.rank_documents(documents, limit)
            self.assertEqual([d['id'] for d in result],
                             ['a', 'z', '\ue000', '\U00010000', 'zero',
                              'negative'][:limit])
            if result:
                result[0]['payload']['nested'][0]['values'].append('changed')
                result[0]['score'] = -1
            self.assertEqual(documents, before)
        self.assertEqual(self.module.rank_documents([], 4), [])

    def test_random_ranking(self):
        rng = random.Random(7341)
        for size in range(40):
            documents = [doc(str(i), rng.randrange(-3, 4)) for i in range(size)]
            rng.shuffle(documents)
            # Independently group score levels and order IDs within each level.
            expected = []
            for score in range(3, -4, -1):
                expected.extend(sorted(d['id'] for d in documents
                                       if d['score'] == score))
            for limit in (0, 1, size // 2, size, size + 5):
                actual = self.module.rank_documents(documents, limit)
                self.assertEqual([d['id'] for d in actual], expected[:limit])

    def test_full_result_and_fixed_expiration(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc('low', -1), doc('high', len(calls)), doc('mid', 0)]
        cache = self.module.RetrievalCache(source, ttl=10)
        self.assertEqual(len(cache.search('t', query(), 'r', 5, 1)), 1)
        self.assertEqual(len(cache.search('t', query(), 'r', 6, 10)), 3)
        self.assertEqual(cache.search('t', query(), 'r', 14)[0]['score'], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.search('t', query(), 'r', 15)[0]['score'], 2)
        self.assertEqual(len(calls), 2)
        cache.search('t', query(), 'r', 24)
        self.assertEqual(len(calls), 2)
        cache.search('t', query(), 'r', 25)
        self.assertEqual(len(calls), 3)

    def test_identity_and_filter_order(self):
        calls = []
        def source(*args):
            calls.append(copy.deepcopy(args))
            return [doc(str(len(calls)))]
        cache = self.module.RetrievalCache(source)
        identities = [
            ('t', query('x', a='1', b='2'), 'r'),
            ('other', query('x', a='1', b='2'), 'r'),
            ('t', query('y', a='1', b='2'), 'r'),
            ('t', query('x', c='1', b='2'), 'r'),
            ('t', query('x', a='3', b='2'), 'r'),
            ('t', query('x', a='1', b='2'), 'other'),
            ('t', query('x', a='1'), 'r'),
            ('t', query('x'), 'r'),
            ('t|x', query('y'), 'r'),
            ('t', query('x|y'), 'r'),
            ('t', query('\ud800\x00"\\', **{'\udfff': '\x00'}), 'r'),
        ]
        for i, identity in enumerate(identities, 1):
            self.assertEqual(cache.search(*identity, now=0)[0]['id'], str(i))
        for i, (tenant, request, revision) in reversed(list(enumerate(identities, 1))):
            reordered = {'filters': dict(reversed(list(request['filters'].items()))),
                         'text': request['text']}
            self.assertEqual(cache.search(tenant, reordered, revision, 1)[0]['id'],
                             str(i))
        self.assertEqual(calls, identities)

    def test_instance_isolation(self):
        calls = []
        def source(*args):
            calls.append(args)
            return [doc(str(len(calls)))]
        first = self.module.RetrievalCache(source)
        second = self.module.RetrievalCache(source)
        self.assertEqual(first.search('t', query(), 'r', 0)[0]['id'], '1')
        self.assertEqual(second.search('t', query(), 'r', 0)[0]['id'], '2')
        self.assertEqual(first.search('t', query(), 'r', 1)[0]['id'], '1')
        self.assertEqual(len(calls), 2)

    def test_zero_limit_and_empty_success(self):
        calls = []
        def source(*args):
            calls.append(args)
            return []
        cache = self.module.RetrievalCache(source, ttl=2)
        self.assertEqual(cache.search('t', query(), 'r', 0, 0), [])
        self.assertEqual(calls, [])
        self.assertEqual(cache.search('t', query(), 'r', 1), [])
        self.assertEqual(cache.search('t', query(), 'r', 2), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.search('t', query(), 'r', 2, 0), [])
        self.assertEqual(cache.search('t', query(), 'r', 3, 0), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.search('t', query(), 'r', 3), [])
        self.assertEqual(len(calls), 2)

    def test_exceptions_retry_and_other_entries(self):
        failure = RuntimeError('source unavailable')
        calls = []
        failing = set()
        def source(tenant, request, revision):
            calls.append(tenant)
            if tenant in failing:
                raise failure
            return [doc(str(len(calls)))]
        cache = self.module.RetrievalCache(source, ttl=10)
        cache.search('old', query(), 'r', 0)
        valid = cache.search('valid', query(), 'r', 5)
        failing.update(('old', 'new'))
        for tenant in ('old', 'new', 'old', 'new'):
            with self.assertRaises(RuntimeError) as caught:
                cache.search(tenant, query(), 'r', 10)
            self.assertIs(caught.exception, failure)
            self.assertEqual(cache.search('valid', query(), 'r', 10), valid)
        self.assertEqual(calls, ['old', 'valid', 'old', 'new', 'old', 'new'])
        failing.clear()
        self.assertEqual(cache.search('old', query(), 'r', 10)[0]['id'], '7')
        self.assertEqual(cache.search('new', query(), 'r', 10)[0]['id'], '8')
        cache.search('old', query(), 'r', 11)
        cache.search('new', query(), 'r', 11)
        self.assertEqual(len(calls), 8)

    def test_query_and_document_ownership(self):
        documents = [doc()]
        expected = copy.deepcopy(documents)
        retained_queries = []
        def source(tenant, request, revision):
            retained_queries.append(request)
            request['text'] = 'source change'
            request['filters']['a'] = 'source change'
            return documents
        request = query(a='original')
        before = copy.deepcopy(request)
        cache = self.module.RetrievalCache(source)
        result = cache.search('t', request, 'r', 0)
        self.assertEqual(request, before)
        result[0]['payload']['nested'][0]['values'].append('caller change')
        result[0]['id'] = 'caller change'
        result.append(doc('extra'))
        self.assertEqual(documents, expected)
        documents[0]['payload']['nested'][0]['values'].clear()
        documents[0]['score'] = 999
        documents.clear()
        retained_queries[0]['filters'].clear()
        self.assertEqual(request, before)
        self.assertEqual(cache.search('t', before, 'r', 1), expected)
        request['filters']['a'] = 'caller query change'
        self.assertEqual(cache.search('t', before, 'r', 2), expected)
        self.assertEqual(len(retained_queries), 1)

    def test_maximum_payload_depth(self):
        payload = {'leaf': [None, False, 0.0, '雪']}
        for i in range(18):
            payload = {'child': payload} if i % 2 else [payload]
        # 20 containers along the deepest path, with an object at the root.
        documents = [{'id': 'deep', 'score': -1, 'payload': payload}]
        before = copy.deepcopy(documents)
        cache = self.module.RetrievalCache(lambda *args: documents)
        ranked = self.module.rank_documents(documents, 1)
        result = cache.search('t', query(), 'r', 0)
        self.assertEqual(ranked, before)
        self.assertEqual(result, before)
        ranked[0]['payload'].clear()
        result[0]['payload'].clear()
        self.assertEqual(documents, before)
        documents[0]['payload'].clear()
        self.assertEqual(cache.search('t', query(), 'r', 1), before)


class TestOriginal(ContractTests, unittest.TestCase):
    module = original


class TestApp(ContractTests, unittest.TestCase):
    module = app


if __name__ == '__main__':
    unittest.main(verbosity=2)
