"""Contract checks; optionally pass a module filename as the first argument."""
import concurrent.futures
import importlib.util
import json
import math
import pathlib
import random
import sys
import tempfile
import threading
import unittest


module_path = sys.argv.pop(1) if len(sys.argv) > 1 and sys.argv[1].endswith('.py') else 'app.py'
spec = importlib.util.spec_from_file_location('queue_under_test', module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Queue = module.Queue


class QueueContract(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir='.')
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / 'queue.db'
        self.q = Queue(self.path)

    def test_fractional_lease_and_exact_expiry(self):
        self.q.put('a', None)
        claim = self.q.claim('w', 0.25, 0.5)
        self.assertEqual(claim['expires_at'], 0.75)
        self.assertEqual(set(claim), {'payload', 'expires_at', 'receipt'})
        self.assertIsNone(self.q.claim('w', 0.5, 1))
        self.assertFalse(self.q.ack(claim['receipt'], 0.75))
        renewed = self.q.claim('w', 0.75, 1.5)
        self.assertEqual(renewed['expires_at'], 2.25)
        self.assertFalse(self.q.ack(claim['receipt'], 0.75))
        self.assertTrue(self.q.ack(renewed['receipt'], 1))
        self.assertFalse(self.q.ack(renewed['receipt'], 1))

    def test_invalid_durations_leave_queue_unchanged(self):
        self.q.put('a', 'first')
        active = self.q.claim('w', 0, 1)
        for duration in [True, False, 0, 0.0, -1, -0.5, math.inf, -math.inf, math.nan]:
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    self.q.claim('other', 1, duration)
        self.assertFalse(self.q.ack(active['receipt'], 1))
        fresh = self.q.claim('w', 1, 2)
        self.assertEqual(fresh['payload'], 'first')
        self.assertTrue(self.q.ack(fresh['receipt'], 1))

    def test_invalid_duration_on_empty_queue(self):
        for duration in [True, False, 0, -1, math.nan, math.inf]:
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    self.q.claim('w', 0, duration)

    def test_order_and_persistence(self):
        self.q.put('z', 1)
        self.q.put('a', 2)
        self.q.put('m', 3)
        peer = Queue(self.path)
        first = peer.claim('w', 0, 2)
        second = self.q.claim('w', 0, 5)
        self.assertEqual([first['payload'], second['payload']], [1, 2])
        again = Queue(self.path).claim('w', 2, 5)
        self.assertEqual(again['payload'], 1)
        self.assertFalse(peer.ack(first['receipt'], 2))
        self.assertTrue(peer.ack(again['receipt'], 2))
        self.assertTrue(peer.ack(second['receipt'], 2))
        third = peer.claim('w', 2, 5)
        self.assertEqual(third['payload'], 3)
        self.assertTrue(self.q.ack(third['receipt'], 2))
        self.assertIsNone(Queue(self.path).claim('w', 10, 5))
        with self.assertRaises(ValueError):
            Queue(self.path).put('z', 'replacement')

    def test_payload_snapshot_and_delivery_isolation(self):
        payload = {'nested': [{'x': []}], 'empty': {}, 'text': '\ud800\x00雪'}
        self.q.put('a', payload)
        payload['nested'][0]['x'].append(1)
        first = self.q.claim('w', 0, 1)
        self.assertEqual(first['payload']['nested'][0]['x'], [])
        first['payload']['nested'][0]['x'].append(2)
        first['receipt']['worker'] = 'edited locally'
        second = self.q.claim('w', 1, 1)
        self.assertEqual(second['payload']['nested'][0]['x'], [])
        self.assertEqual(second['payload']['text'], '\ud800\x00雪')
        self.assertTrue(self.q.ack(second['receipt'], 1))

    def test_supported_payload_values(self):
        values = [None, False, True, '', '雪', '\ud800\udc00', '\U00010000',
                  {'\ud800\udc00': 1, '\U00010000': 2},
                  0, -1, 10**100, -0.0, 1.25,
                  [], {}, [[], {}], {'': [None, {'x': True}]}]
        for index, value in enumerate(values):
            self.q.put(str(index), value)
        for value in values:
            result = self.q.claim('w', 0, 1)
            self.assertEqual(result['payload'], value)
            self.assertIs(type(result['payload']), type(value))
            self.assertTrue(self.q.ack(result['receipt'], 0))

    def test_encoder_rejections_are_atomic(self):
        circular = []
        circular.append(circular)
        deep = []
        # CPython's C encoder limit need not equal the Python recursion limit.
        for _ in range(10_000):
            deep = [deep]
        with self.assertRaises(RecursionError):
            json.dumps(deep, allow_nan=False)
        bad = [object(), {1, 2}, b'bytes', complex(1, 2), math.nan, math.inf,
               -math.inf, {'nested': [math.nan]}, circular, deep]
        self.q.put('existing', {'ok': True})
        for index, payload in enumerate(bad):
            with self.subTest(index=index):
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.q.put('bad', payload)
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.q.put('existing', payload)
        result = self.q.claim('w', 0, 1)
        self.assertEqual(result['payload'], {'ok': True})
        self.assertTrue(self.q.ack(result['receipt'], 0))
        self.assertIsNone(self.q.claim('w', 0, 1))
        self.q.put('bad', 'now valid')

    def test_duplicate_preserves_active_job(self):
        self.q.put('a', 'original')
        claim = self.q.claim('w', 0, 2)
        with self.assertRaises(ValueError):
            self.q.put('a', 'replacement')
        self.assertTrue(self.q.ack(claim['receipt'], 1))

    def test_original_database_and_receipt_compatibility(self):
        legacy_spec = importlib.util.spec_from_file_location('legacy_queue', 'original.py')
        legacy = importlib.util.module_from_spec(legacy_spec)
        legacy_spec.loader.exec_module(legacy)
        legacy_queue = legacy.Queue(self.path)
        legacy_queue.put('old', {'text': '雪'})
        old_claim = legacy_queue.claim('w', 0, 5)
        reopened = Queue(self.path)
        self.assertIsNone(reopened.claim('w', 1, 1))
        self.assertTrue(reopened.ack(old_claim['receipt'], 1))
        with self.assertRaises(ValueError):
            reopened.put('old', 'duplicate')
        legacy_queue.put('pending', [None, {'x': []}])
        pending = reopened.claim('new worker', 1, 5)
        self.assertEqual(pending['payload'], [None, {'x': []}])
        self.assertTrue(legacy_queue.ack(pending['receipt'], 1))

    def test_all_nonempty_python_strings_as_ids_and_workers(self):
        strings = ['雪', 'a\x00b', '\ud800', '\udfff', 'literal \\ud800']
        for index, name in enumerate(strings):
            with self.subTest(name=repr(name)):
                self.q.put(name, index)
                claim = Queue(self.path).claim(name, 0, 1)
                self.assertEqual(claim['payload'], index)
                self.assertTrue(self.q.ack(claim['receipt'], 0))
                with self.assertRaises(ValueError):
                    Queue(self.path).put(name, None)

    def test_numeric_boundaries_follow_python_arithmetic(self):
        cases = [(-10**12, 0.125), (10**12, 0.125), (10**12, 10**12),
                 (10**12, 5e-324), (0, 5e-324), (-0.5, 0.75)]
        for index, (now, duration) in enumerate(cases):
            with self.subTest(now=now, duration=duration):
                q = Queue(pathlib.Path(self.directory.name) / f'boundary{index}.db')
                q.put('a', None)
                claim = q.claim('w', now, duration)
                expiry = now + duration
                self.assertEqual(claim['expires_at'], expiry)
                self.assertEqual(q.ack(claim['receipt'], now), expiry > now)

    def test_concurrent_claim_and_ack(self):
        self.q.put('a', 'only job')
        peers = [Queue(self.path) for _ in range(8)]
        barrier = threading.Barrier(len(peers))
        def claim(peer):
            barrier.wait(timeout=10)
            return peer.claim('same worker', 0, 5)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(claim, peers))
        winners = [result for result in claims if result is not None]
        self.assertEqual(len(winners), 1)
        barrier = threading.Barrier(len(peers))
        def ack(peer):
            barrier.wait(timeout=10)
            return peer.ack(winners[0]['receipt'], 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(ack, peers)).count(True), 1)

    def test_randomized_reference_scenarios(self):
        rng = random.Random(174)
        jobs = []
        receipts = []
        now = -5.0
        for step in range(300):
            now += rng.choice([0, 0.125, 0.5, 1])
            op = rng.choice(['put', 'claim', 'claim', 'ack'])
            q = Queue(self.path)
            if op == 'put':
                q.put(str(step), {'id': step})
                jobs.append({'id': step, 'state': 'ready', 'expiry': None, 'claim': None})
            elif op == 'claim':
                duration = rng.choice([0.125, 0.75, 1, 3.25])
                eligible = next((j for j in jobs if j['state'] == 'ready' or
                                 (j['state'] == 'leased' and j['expiry'] <= now)), None)
                result = q.claim(rng.choice(['a', 'b']), now, duration)
                if eligible is None:
                    self.assertIsNone(result)
                else:
                    self.assertEqual(result['payload'], {'id': eligible['id']})
                    self.assertEqual(result['expires_at'], now + duration)
                    claim_token = object()
                    eligible.update(state='leased', expiry=now + duration, claim=claim_token)
                    receipts.append((eligible, claim_token, result['receipt']))
            elif receipts:
                job, token, receipt = rng.choice(receipts)
                expected = job['state'] == 'leased' and job['claim'] is token and now < job['expiry']
                self.assertEqual(q.ack(receipt, now), expected)
                if expected:
                    job['state'] = 'done'


if __name__ == '__main__':
    unittest.main()
