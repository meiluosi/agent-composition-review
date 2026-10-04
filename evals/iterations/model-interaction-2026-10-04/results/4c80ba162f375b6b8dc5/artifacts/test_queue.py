"""Contract checks. Run with ./run_python test_queue.py [app.py|original.py]."""
import concurrent.futures
import importlib.util
import json
import math
import pathlib
import random
import sqlite3
import sys
import tempfile
import threading
import unittest


def load_module(path):
    spec = importlib.util.spec_from_file_location(pathlib.Path(path).stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


implementation = load_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app.py')


class QueueContract(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir='.')
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / 'queue.db'
        self.q = implementation.Queue(self.path)

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return db.execute('SELECT * FROM jobs ORDER BY seq').fetchall()

    def test_payload_values_and_isolation(self):
        values = [None, True, False, '', '汉字\x00\ud800', 0, 10**100,
                  -10**100, -0.0, 1.25, 5e-324, [], {},
                  {'nested': [[], {}, None, {'key': ['value']}] }]
        expected = json.loads(json.dumps(values))
        for i, value in enumerate(values):
            self.q.put(str(i), value)
        values[-1]['nested'].clear()
        for i, value in enumerate(expected):
            delivery = self.q.claim('worker', i, 0.5)
            self.assertEqual(set(delivery), {'payload', 'expires_at', 'receipt'})
            self.assertEqual(delivery['payload'], value)
            self.assertTrue(self.q.ack(delivery['receipt'], i))
        self.assertIsNone(self.q.claim('worker', 20, 1))

        self.q.put('mutable', {'list': [1, {'x': 2}]})
        first = self.q.claim('worker', 21, 1)
        first['payload']['list'][1]['x'] = 99
        first['receipt']['generation'] = -1
        second = self.q.claim('worker', 22, 1)
        self.assertEqual(second['payload'], {'list': [1, {'x': 2}]})
        self.assertTrue(self.q.ack(second['receipt'], 22))

    def test_invalid_payloads_leave_state_unchanged(self):
        self.q.put('existing', {'safe': []})
        self.q.claim('worker', 0, 1)
        cycle = []
        cycle.append(cycle)
        deep = None
        # The C JSON encoder's depth bound need not equal sys.getrecursionlimit().
        for _ in range(20):
            for _ in range(1000):
                deep = [deep]
            try:
                json.dumps(deep, allow_nan=False)
            except RecursionError:
                break
        else:
            self.fail('Could not construct an input exceeding the JSON encoder depth')
        invalid = [object(), b'bytes', {1, 2}, {'nested': [float('nan')]},
                   float('inf'), -float('inf'), cycle, deep, {('tuple',): 1}]
        for payload in invalid:
            for job_id in ('new', 'existing'):
                with self.subTest(payload_type=type(payload), job_id=job_id):
                    before = self.snapshot()
                    with self.assertRaises((TypeError, ValueError, RecursionError)):
                        self.q.put(job_id, payload)
                    self.assertEqual(before, self.snapshot())
        self.q.put('new', 'valid after rejection')
        self.assertEqual(self.q.claim('worker', 1, 1)['payload'], {'safe': []})

    def test_invalid_durations_leave_state_unchanged(self):
        self.q.put('one', 1)
        for duration in (True, False, 0, -0.0, -1, -0.25,
                         float('nan'), float('inf'), -float('inf')):
            with self.subTest(duration=duration):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.q.claim('worker', 0, duration)
                self.assertEqual(before, self.snapshot())
        self.q.claim('worker', 0, 1)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.q.claim('worker', 1, True)
        self.assertEqual(before, self.snapshot())

    def test_fifo_receipts_expiry_reopen_and_duplicates(self):
        other = implementation.Queue(self.path)
        self.q.put('a', 'oldest')
        other.put('b', 'second')
        first = self.q.claim('same-worker', 0.25, 0.5)
        second = other.claim('same-worker', 0.25, 10)
        self.assertEqual(first['payload'], 'oldest')
        self.assertEqual(second['payload'], 'second')
        self.assertIsNone(other.claim('same-worker', 0.5, 1))
        self.assertFalse(other.ack(first['receipt'], 0.75))
        replacement = implementation.Queue(self.path).claim('same-worker', 0.75, 1)
        self.assertEqual(replacement['payload'], 'oldest')
        self.assertNotEqual(first['receipt'], replacement['receipt'])
        before = self.snapshot()
        self.assertFalse(other.ack(first['receipt'], 0.75))
        self.assertEqual(before, self.snapshot())
        self.assertTrue(other.ack(replacement['receipt'], 0.75))
        self.assertFalse(self.q.ack(replacement['receipt'], 0.75))
        self.assertTrue(self.q.ack(second['receipt'], 0.75))
        for job_id in ('a', 'b'):
            before = self.snapshot()
            with self.assertRaises(ValueError):
                implementation.Queue(self.path).put(job_id, 'duplicate')
            self.assertEqual(before, self.snapshot())
        self.assertIsNone(other.claim('worker', 100, 1))

    def test_numeric_boundaries(self):
        cases = [(0, 5e-324), (0.1, 0.2), (-10**12, 0.125),
                 (10**12, 5e-324), (10**12, 10**12), (-10**12, 10**12)]
        for i, (now, duration) in enumerate(cases):
            with self.subTest(now=now, duration=duration):
                queue = implementation.Queue(pathlib.Path(self.directory.name) / f'numeric{i}.db')
                queue.put('job', None)
                first = queue.claim('worker', now, duration)
                expiry = now + duration
                self.assertEqual(first['expires_at'], expiry)
                if expiry > now:
                    self.assertIsNone(queue.claim('worker', now, duration))
                # All API times must remain inside the specified bound.
                if abs(expiry) <= 10**12:
                    before = math.nextafter(float(expiry), -math.inf)
                    if before >= now:
                        self.assertIsNone(queue.claim('worker', before, duration))
                    self.assertFalse(queue.ack(first['receipt'], expiry))
                    replacement = queue.claim('worker', expiry, duration)
                    self.assertIsNotNone(replacement)
                    self.assertFalse(queue.ack(first['receipt'], expiry))

    def test_all_python_strings_as_identifiers(self):
        for i, identifier in enumerate(('普通', '\x00', '\ud800', '\udfff',
                                        'a\ud800b', '\ud800\udfff', '\U000103ff')):
            with self.subTest(identifier=ascii(identifier)):
                self.q.put(identifier, i)
                delivery = self.q.claim(identifier, i, 1)
                self.assertEqual(delivery['payload'], i)
                other = implementation.Queue(self.path)
                self.assertTrue(other.ack(delivery['receipt'], i))
                with self.assertRaises(ValueError):
                    other.put(identifier, 'duplicate')

    def test_surrogate_payload_values_and_distinct_keys(self):
        payload = {'\ud800\udfff': ['\ud800\udfff'], '\U000103ff': ['\U000103ff']}
        self.q.put('unicode', payload)
        delivery = implementation.Queue(self.path).claim('worker', 0, 1)
        self.assertEqual(delivery['payload'], payload)
        delivery['payload'].clear()
        self.assertEqual(self.q.claim('worker', 1, 1)['payload'], payload)

    def test_deep_supported_payload(self):
        payload = 'leaf'
        for _ in range(500):
            payload = [payload]
        self.q.put('deep', payload)
        delivery = self.q.claim('worker', 0, 1)
        value = delivery['payload']
        for _ in range(500):
            self.assertIsInstance(value, list)
            self.assertEqual(len(value), 1)
            value = value[0]
        self.assertEqual(value, 'leaf')
        self.assertTrue(self.q.ack(delivery['receipt'], 0))

    def test_existing_database_and_receipt_compatibility(self):
        old = load_module('original.py').Queue(self.path)
        old.put('old', ['existing'])
        receipt = old.claim('worker', 0, 5)['receipt']
        reopened = implementation.Queue(self.path)
        self.assertTrue(reopened.ack(receipt, 1))
        reopened.put('new', {'new': True})
        current = reopened.claim('worker', 2, 5)
        self.assertTrue(old.ack(current['receipt'], 3))

    def test_concurrent_claim_and_ack(self):
        queues = [implementation.Queue(self.path) for _ in range(12)]
        self.q.put('one', 'one')

        def together(action):
            barrier = threading.Barrier(len(queues))
            def run(q):
                barrier.wait(timeout=10)
                return action(q)
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(queues)) as pool:
                return list(pool.map(run, queues))

        winners = [x for x in together(lambda q: q.claim('same', 0, 1)) if x is not None]
        self.assertEqual(len(winners), 1)
        expired_receipt = winners[0]['receipt']
        winners = [x for x in together(lambda q: q.claim('same', 1, 1)) if x is not None]
        self.assertEqual(len(winners), 1)
        self.assertFalse(self.q.ack(expired_receipt, 1))
        results = together(lambda q: q.ack(winners[0]['receipt'], 1))
        self.assertEqual(results.count(True), 1)
        for i in range(len(queues)):
            self.q.put(str(i), i)
        deliveries = together(lambda q: q.claim('same', 2, 1))
        self.assertEqual({x['payload'] for x in deliveries}, set(range(len(queues))))

    def test_seeded_state_machine(self):
        rng = random.Random(7331)
        jobs = []
        receipts = []
        now = -10.0
        for _ in range(600):
            now += rng.choice([0, 0.125, 0.5, 1])
            queue = implementation.Queue(self.path)
            action = rng.choice(['put', 'claim', 'claim', 'ack'])
            if action == 'put':
                job_id = str(rng.randrange(70))
                if any(job['id'] == job_id for job in jobs):
                    with self.assertRaises(ValueError):
                        queue.put(job_id, {'id': job_id})
                else:
                    queue.put(job_id, {'id': job_id})
                    jobs.append(dict(id=job_id, state='ready', expiry=None, receipt=None))
            elif action == 'claim':
                worker = rng.choice(['a', 'b', 'c'])
                duration = rng.choice([0.125, 0.5, 1, 10])
                expected = next((j for j in jobs if j['state'] == 'ready' or
                                 (j['state'] == 'leased' and j['expiry'] <= now)), None)
                actual = queue.claim(worker, now, duration)
                if expected is None:
                    self.assertIsNone(actual)
                else:
                    self.assertEqual(actual['payload'], {'id': expected['id']})
                    self.assertEqual(actual['expires_at'], now + duration)
                    expected.update(state='leased', expiry=now + duration, receipt=actual['receipt'])
                    receipts.append((expected, actual['receipt']))
            elif receipts:
                job, receipt = rng.choice(receipts)
                expected = (job['state'] == 'leased' and job['expiry'] > now
                            and job['receipt'] == receipt)
                before = self.snapshot()
                self.assertEqual(queue.ack(receipt, now), expected)
                if expected:
                    job['state'] = 'done'
                else:
                    self.assertEqual(before, self.snapshot())


if __name__ == '__main__':
    unittest.main(verbosity=2)
