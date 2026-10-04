"""Contract regressions; run with ./run_python test_queue.py [module.py]."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor


target = sys.argv.pop(1) if len(sys.argv) > 1 else 'app.py'
spec = importlib.util.spec_from_file_location('queue_under_test', target)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QueueContract(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir='.')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'queue.db'
        self.q = module.Queue(self.path)

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return db.execute('SELECT * FROM jobs ORDER BY seq').fetchall()

    def test_invalid_durations_are_atomic(self):
        self.q.put('a', None)
        for duration in [True, False, 0, -1, -0.5, math.inf, -math.inf, math.nan]:
            with self.subTest(duration=duration):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.q.claim('worker', 0, duration)
                self.assertEqual(self.snapshot(), before)

    def test_string_identifiers(self):
        for identifier in ['ordinary', '中文', '\x00', '\ud800', '\udfff', '\ud800\udfff', '𐀀']:
            with self.subTest(identifier=ascii(identifier)):
                self.q.put(identifier, identifier)
                delivery = self.q.claim(identifier, 0, 1)
                self.assertEqual(delivery['payload'], identifier)
                self.assertTrue(module.Queue(self.path).ack(delivery['receipt'], 0.5))
                with self.assertRaises(ValueError):
                    self.q.put(identifier, 'duplicate')

    def test_payload_values_and_isolation(self):
        values = [None, True, False, '', '\ud800', '\ud800\udfff',
                  {'\ud800\udfff': '\ud800\udfff'}, 10**100, -2, 1.25, -0.0,
                  [], {}, {'a': [None, {'empty': {}, 'items': []}]}]
        for index, value in enumerate(values):
            original = copy.deepcopy(value)
            self.q.put(str(index), value)
            if isinstance(value, dict):
                value['mutated'] = 1
            elif isinstance(value, list):
                value.append('mutated')
            delivery = self.q.claim('w', index * 2, 1)
            self.assertEqual(set(delivery), {'payload', 'expires_at', 'receipt'})
            self.assertEqual(delivery['payload'], original)
            if isinstance(delivery['payload'], dict):
                delivery['payload']['returned'] = 'mutation'
            elif isinstance(delivery['payload'], list):
                delivery['payload'].append('mutation')
            delivery['receipt']['worker'] = 'edited only locally'
            again = self.q.claim('w', index * 2 + 1, 1)
            self.assertEqual(again['payload'], original)
            self.assertTrue(self.q.ack(again['receipt'], index * 2 + 1))

    def test_rejected_payloads_leave_state_intact(self):
        self.q.put('active', {'v': 1})
        delivery = self.q.claim('w', 0, 5)
        cycle = []
        cycle.append(cycle)
        deep = None
        # The C encoder's nesting limit need not equal sys.getrecursionlimit().
        for _ in range(20000):
            deep = [deep]
        with self.assertRaises(RecursionError):
            json.dumps(deep, allow_nan=False)
        bad = [object(), {1, 2}, b'bytes', complex(1, 2), math.nan,
               math.inf, -math.inf, {'nested': [math.nan]}, cycle, deep]
        before = self.snapshot()
        for value in bad:
            for identifier in ['new', 'active']:
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.q.put(identifier, value)
                self.assertEqual(self.snapshot(), before)
        self.q.put('new', None)
        self.assertTrue(self.q.ack(delivery['receipt'], 1))

    def test_fifo_leases_and_stale_receipts(self):
        for identifier in ['a', 'b', 'c']:
            self.q.put(identifier, identifier)
        a = self.q.claim('same', 0, 0.5)
        b = self.q.claim('same', 0.1, 2)
        self.assertEqual((a['payload'], b['payload']), ('a', 'b'))
        c = self.q.claim('other', 0.2, 2)
        self.assertEqual(c['payload'], 'c')
        self.assertIsNone(self.q.claim('same', 0.3, 1))
        self.assertFalse(self.q.ack(a['receipt'], 0.5))
        renewed = self.q.claim('same', 0.5, 1)
        self.assertEqual(renewed['payload'], 'a')
        before = self.snapshot()
        self.assertFalse(self.q.ack(a['receipt'], 0.6))
        self.assertEqual(self.snapshot(), before)
        other = module.Queue(self.path)
        self.assertTrue(other.ack(renewed['receipt'], 0.6))
        self.assertFalse(self.q.ack(renewed['receipt'], 0.6))
        self.assertTrue(other.ack(b['receipt'], 0.6))
        self.assertTrue(other.ack(c['receipt'], 0.6))
        self.assertIsNone(other.claim('w', 10, 1))
        with self.assertRaises(ValueError):
            other.put('a', 'completed duplicate')

    def test_expiry_uses_python_arithmetic(self):
        for now, duration in [(0, 0.1), (-10**12, 0.25), (10**12, 10**12),
                              (10**12, 5e-324), (0, 5e-324), (-0.5, 0.5)]:
            with self.subTest(now=now, duration=duration):
                path = Path(self.directory.name) / ('case' + str(now) + str(duration))
                q = module.Queue(path)
                q.put('a', None)
                first = q.claim('w', now, duration)
                self.assertEqual(first['expires_at'], now + duration)
                expiry = now + duration
                before = math.nextafter(expiry, -math.inf)
                if before >= now:
                    self.assertIsNone(q.claim('w', before, duration))
                self.assertFalse(q.ack(first['receipt'], expiry))
                self.assertIsNotNone(q.claim('w', expiry, duration))

    def test_atomic_claim_and_ack_across_connections(self):
        self.q.put('a', {'work': 1})
        queues = [module.Queue(self.path) for _ in range(8)]
        for now in range(4):
            barrier = threading.Barrier(len(queues))
            def claim(q):
                barrier.wait()
                return q.claim('same', now, 1)
            with ThreadPoolExecutor(max_workers=len(queues)) as pool:
                deliveries = list(pool.map(claim, queues))
            winners = [item for item in deliveries if item is not None]
            self.assertEqual(len(winners), 1)
            if now:
                self.assertFalse(self.q.ack(previous['receipt'], now))
            previous = winners[0]
        barrier = threading.Barrier(len(queues))
        def ack(q):
            barrier.wait()
            return q.ack(previous['receipt'], 3.5)
        with ThreadPoolExecutor(max_workers=len(queues)) as pool:
            results = list(pool.map(ack, queues))
        self.assertEqual(sum(results), 1)

    def test_original_database_compatibility(self):
        original_spec = importlib.util.spec_from_file_location('legacy_queue', 'original.py')
        original = importlib.util.module_from_spec(original_spec)
        original_spec.loader.exec_module(original)
        legacy = original.Queue(self.path)
        legacy.put('legacy', {'saved': True})
        delivery = legacy.claim('old-worker', 0, 2)
        self.assertTrue(self.q.ack(delivery['receipt'], 1))
        self.q.put('new', [])
        delivery = self.q.claim('new-worker', 1, 2)
        self.assertTrue(legacy.ack(delivery['receipt'], 2))

    def test_seeded_reference_model(self):
        rng = random.Random(9071)
        jobs = {}
        receipts = []
        now = -2.0
        for step in range(500):
            now += rng.choice([0, 0.01, 0.5, 1])
            q = module.Queue(self.path)
            action = rng.choice(['put', 'claim', 'claim', 'ack'])
            if action == 'put':
                identifier = str(rng.randrange(40))
                value = {'id': identifier, 'nested': [step, None, []]}
                if identifier in jobs:
                    with self.assertRaises(ValueError):
                        q.put(identifier, value)
                else:
                    q.put(identifier, value)
                    jobs[identifier] = {'payload': value, 'state': 'ready', 'version': 0}
            elif action == 'claim':
                duration = rng.choice([0.01, 0.5, 1, 10])
                expected = next((key for key, job in jobs.items()
                                 if job['state'] == 'ready' or
                                 (job['state'] == 'leased' and job['expiry'] <= now)), None)
                actual = q.claim(rng.choice(['a', 'b']), now, duration)
                if expected is None:
                    self.assertIsNone(actual)
                else:
                    job = jobs[expected]
                    self.assertEqual(actual['payload'], job['payload'])
                    self.assertEqual(actual['expires_at'], now + duration)
                    job.update(state='leased', expiry=now + duration, version=job['version'] + 1)
                    receipts.append((expected, job['version'], actual['receipt']))
            elif receipts:
                identifier, version, receipt = rng.choice(receipts)
                job = jobs[identifier]
                expected = job['state'] == 'leased' and job['version'] == version and job['expiry'] > now
                self.assertIs(q.ack(receipt, now), expected)
                if expected:
                    job['state'] = 'done'


if __name__ == '__main__':
    unittest.main(verbosity=2)
