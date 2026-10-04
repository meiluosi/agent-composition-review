import concurrent.futures
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import unittest


Queue = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app').Queue


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir='.')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'queue.db'
        self.q = Queue(self.path)

    def test_fifo_expiry_and_receipts(self):
        self.q.put('first', 1)
        self.q.put('second', 2)
        first = self.q.claim('same', 0.25, 0.5)
        self.assertEqual(set(first), {'payload', 'expires_at', 'receipt'})
        self.assertEqual(first['expires_at'], 0.75)
        second = self.q.claim('same', 0.25, 2)
        self.assertEqual(second['payload'], 2)
        self.assertIsNone(self.q.claim('same', 0.5, 1))
        self.assertFalse(self.q.ack(first['receipt'], 0.75))
        replacement = Queue(self.path).claim('same', 0.75, 1)
        self.assertEqual(replacement['payload'], 1)
        self.assertFalse(self.q.ack(first['receipt'], 0.75))
        self.assertTrue(Queue(self.path).ack(replacement['receipt'], 1))
        self.assertFalse(self.q.ack(replacement['receipt'], 1))
        self.assertTrue(self.q.ack(second['receipt'], 1))
        self.assertIsNone(Queue(self.path).claim('other', 10, 1))
        with self.assertRaises(ValueError):
            self.q.put('first', 'duplicate completed')

    def test_payload_values_and_isolation(self):
        payload = [None, True, False, '', 42, -0.0, 1.25, [], {},
                   {'nested': ['你好', '\x00', {'a': []}]}]
        expected = json.loads(json.dumps(payload))
        self.q.put('one', payload)
        payload[-1]['nested'].append('input mutation')
        first = self.q.claim('worker', 0, 1)
        self.assertEqual(first['payload'], expected)
        self.assertEqual(math.copysign(1, first['payload'][5]), -1)
        first['payload'][-1]['nested'].clear()
        first['receipt']['generation'] = -1
        second = self.q.claim('worker', 1, 1)
        self.assertEqual(second['payload'], expected)
        self.assertTrue(self.q.ack(second['receipt'], 1))

    def test_rejected_payloads_leave_queue_intact(self):
        self.q.put('existing', {'ok': True})
        cyclic = []
        cyclic.append(cyclic)
        deep = None
        for _ in range(20000):
            deep = [deep]
        bad_values = [object(), b'bytes', {1, 2}, float('nan'),
                      float('inf'), -float('inf'), {'nested': [object()]},
                      cyclic, deep]
        for value in bad_values:
            with self.assertRaises((TypeError, ValueError, RecursionError)):
                self.q.put('reusable', value)
        self.q.put('reusable', 'valid')
        with self.assertRaises(ValueError):
            self.q.put('existing', 'replacement')
        first = self.q.claim('w', 0, 1)
        self.assertEqual(first['payload'], {'ok': True})
        self.assertTrue(self.q.ack(first['receipt'], 0))
        self.assertEqual(self.q.claim('w', 0, 1)['payload'], 'valid')
        self.assertIsNone(self.q.claim('w', 0, 1))

    def test_invalid_durations_do_not_claim_or_replace(self):
        self.q.put('one', None)
        bad_values = [0, -0.0, -1, -1e12, float('nan'), float('inf'), -float('inf')]
        for duration in bad_values:
            with self.assertRaises(ValueError):
                self.q.claim('bad', 0, duration)
        first = self.q.claim('w', 0, 1)
        for duration in bad_values:
            with self.assertRaises(ValueError):
                self.q.claim('bad', 0, duration)
        self.assertTrue(self.q.ack(first['receipt'], 0))

    def test_numeric_boundaries(self):
        scenarios = [(-10**12, 10**12), (10**12, 10**12),
                     (0, 5e-324), (10**12, 5e-324), (-0.5, 0.125)]
        for index, (now, duration) in enumerate(scenarios):
            with self.subTest(now=now, duration=duration):
                q = Queue(Path(self.directory.name) / f'boundary-{index}.db')
                q.put('one', index)
                delivery = q.claim('w', now, duration)
                expiry = now + duration
                self.assertEqual(delivery['expires_at'], expiry)
                if expiry > now:
                    self.assertIsNone(q.claim('w', now, duration))
                self.assertFalse(q.ack(delivery['receipt'], expiry))
                self.assertIsNotNone(q.claim('w', expiry, duration))

    def test_unicode_payload_values_are_exact(self):
        payload = {'\ud800\udc00': 'pair', '\U00010000': 'scalar',
                   'strings': ['\ud800', '\udfff', '\ud800\udc00', '你好', '\x00']}
        self.q.put('unicode', payload)
        result = Queue(self.path).claim('w', 0, 1)
        self.assertEqual(result['payload'], payload)

    def test_unicode_ids_and_workers(self):
        for index, name in enumerate(['\ud800', '\udfff', '\ud800\udc00',
                                      '\U00010000', '你好', '\x00']):
            self.q.put(name, index)
            delivery = Queue(self.path).claim(name, index, 0.5)
            self.assertEqual(delivery['payload'], index)
            self.assertTrue(self.q.ack(delivery['receipt'], index))
            with self.assertRaises(ValueError):
                self.q.put(name, 'duplicate')

    def test_existing_database_and_receipts(self):
        original = importlib.import_module('original').Queue(self.path)
        original.put('old', {'old': '你好'})
        receipt = original.claim('old-worker', 0, 1)['receipt']
        self.assertTrue(Queue(self.path).ack(receipt, 0.5))
        original.put('ready', [None, {}])
        self.assertEqual(Queue(self.path).claim('new-worker', 1, 1)['payload'], [None, {}])

    def test_concurrent_claim_and_ack(self):
        self.q.put('one', {'one': 1})
        queues = [Queue(self.path) for _ in range(8)]
        barrier = threading.Barrier(len(queues))

        def claim(q):
            barrier.wait()
            return q.claim('same', 0, 10)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(claim, queues))
        winners = [claim for claim in claims if claim is not None]
        self.assertEqual(len(winners), 1)

        def ack(q):
            barrier.wait()
            return q.ack(winners[0]['receipt'], 1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(ack, queues)), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
