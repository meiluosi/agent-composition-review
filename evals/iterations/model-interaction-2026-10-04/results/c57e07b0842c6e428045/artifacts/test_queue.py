"""Contract checks; optionally load original.py instead of app.py."""
import concurrent.futures
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


module_path = sys.argv.pop(1) if len(sys.argv) > 1 and sys.argv[1].endswith('.py') else 'app.py'
spec = importlib.util.spec_from_file_location('queue_under_test', module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Queue = module.Queue


class QueueContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'queue.db'
        self.queue = Queue(self.path)

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return db.execute('SELECT * FROM jobs ORDER BY seq').fetchall()

    def test_stale_receipt_after_same_worker_reclaim(self):
        self.queue.put('one', {'value': 1})
        old = self.queue.claim('worker', 0, 1)
        current = Queue(self.path).claim('worker', 1, 5)
        before = self.snapshot()
        self.assertIs(self.queue.ack(old['receipt'], 2), False)
        self.assertEqual(self.snapshot(), before)
        self.assertIs(self.queue.ack(current['receipt'], 2), True)
        self.assertIs(self.queue.ack(current['receipt'], 2), False)

    def test_stale_receipt_after_worker_cycles(self):
        self.queue.put('one', None)
        receipts = [self.queue.claim(worker, now, 1)['receipt']
                    for now, worker in enumerate(['a', 'b', 'a', 'a'])]
        for receipt in receipts[:-1]:
            self.assertIs(self.queue.ack(receipt, 3), False)
        self.assertIs(Queue(self.path).ack(receipts[-1], 3), True)

    def test_fifo_eligibility_and_exact_expiry(self):
        for index in range(3):
            self.queue.put(str(index), index)
        first = self.queue.claim('a', 0.25, 0.5)
        self.assertEqual(set(first), {'payload', 'expires_at', 'receipt'})
        self.assertEqual(first['payload'], 0)
        self.assertEqual(first['expires_at'], 0.75)
        self.assertEqual(self.queue.claim('a', 0.5, 4)['payload'], 1)
        self.assertIs(self.queue.ack(first['receipt'], 0.75), False)
        reclaimed = self.queue.claim('a', 0.75, 4)
        self.assertEqual(reclaimed['payload'], 0)
        self.assertEqual(self.queue.claim('b', 0.75, 4)['payload'], 2)
        self.assertIsNone(self.queue.claim('a', 0.75, 4))

    def test_ack_once_and_completed_ids_persist(self):
        self.queue.put('one', [])
        claim = self.queue.claim('worker', 0, 10)
        reopened = Queue(self.path)
        self.assertIs(reopened.ack(claim['receipt'], 1), True)
        self.assertIs(reopened.ack(claim['receipt'], 1), False)
        with self.assertRaises(ValueError):
            reopened.put('one', {'replacement': True})
        self.assertIsNone(Queue(self.path).claim('worker', 100, 1))

    def test_duplicate_preserves_ready_and_leased_jobs(self):
        self.queue.put('one', {'original': True})
        for now in [0, 1]:
            before = self.snapshot()
            with self.assertRaises(ValueError):
                self.queue.put('one', {'replacement': True})
            self.assertEqual(self.snapshot(), before)
            if now == 0:
                self.queue.claim('worker', 0, 5)

    def test_supported_payloads_and_snapshot_isolation(self):
        values = [None, False, True, '', '汉字\ud800', 0, -(10**100),
                  1.25, -0.0, 1e300, [], {}, [[], {'x': [None, True]}],
                  {'': {}, 'nested': [1, {'a': []}]}]
        expected = copy.deepcopy(values)
        for index, value in enumerate(values):
            self.queue.put(str(index), value)
        values[-1]['nested'][1]['a'].append('input mutation')
        for index, value in enumerate(expected):
            claim = Queue(self.path).claim('worker', 0, 1)
            self.assertEqual(claim['payload'], value)
            self.assertEqual(type(claim['payload']), type(value))
            self.assertIs(self.queue.ack(claim['receipt'], 0), True)

    def test_returned_payload_and_receipt_do_not_alias(self):
        self.queue.put('one', {'items': [[]]})
        first = self.queue.claim('worker', 0, 1)
        first['payload']['items'][0].append('changed')
        first['receipt']['worker'] = 'edited'
        second = Queue(self.path).claim('worker', 1, 1)
        self.assertEqual(second['payload'], {'items': [[]]})
        self.assertEqual(second['receipt']['worker'], 'worker')
        self.assertIs(self.queue.ack(second['receipt'], 1), True)

    def test_invalid_payloads_are_atomic(self):
        self.queue.put('existing', {'ok': True})
        self.queue.claim('worker', 0, 5)
        cyclic = []
        cyclic.append(cyclic)
        deep = []
        # Python 3.12's C encoder has a separate nesting limit.
        for _ in range(20000):
            deep = [deep]
        invalid = [object(), {1, 2}, b'bytes', complex(1, 2),
                   float('nan'), float('inf'), -float('inf'),
                   {'nested': [float('nan')]}, {object(): 1}, cyclic, deep]
        for value in invalid:
            with self.assertRaises((TypeError, ValueError, RecursionError)):
                json.dumps(value, allow_nan=False)
            for job_id in ['new', 'existing']:
                before = self.snapshot()
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.queue.put(job_id, value)
                self.assertEqual(self.snapshot(), before)
        self.queue.put('new', 'valid now')

    def test_invalid_durations_are_atomic_even_when_empty(self):
        invalid = [0, 0.0, -0.0, -1, -1e12, float('nan'),
                   float('inf'), -float('inf')]
        for populated in [False, True]:
            if populated:
                self.queue.put('one', None)
            for duration in invalid:
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.queue.claim('worker', 0, duration)
                self.assertEqual(self.snapshot(), before)

    def test_numeric_boundaries_follow_python_arithmetic(self):
        cases = [(-10**12, 10**12), (10**12, 10**12),
                 (10**12, 0.00001), (0.0, math.ulp(0.0)),
                 (-0.3, 0.1), (0.1, 0.2)]
        for index, (now, duration) in enumerate(cases):
            queue = Queue(Path(self.directory.name) / f'numeric-{index}.db')
            queue.put('one', None)
            claim = queue.claim('worker', now, duration)
            expiry = now + duration
            self.assertEqual(claim['expires_at'], expiry)
            self.assertIs(queue.ack(claim['receipt'], expiry), False)
            self.assertIsNotNone(queue.claim('worker', expiry, 1))

    def test_concurrent_claims_and_acknowledgements(self):
        callers = [Queue(self.path) for _ in range(8)]
        self.queue.put('one', None)
        barrier = threading.Barrier(len(callers))

        def claim(queue):
            barrier.wait(timeout=10)
            return queue.claim('same-worker', 0, 10)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(claim, callers))
        successful = [value for value in claims if value is not None]
        self.assertEqual(len(successful), 1)
        receipt = successful[0]['receipt']

        def ack(queue):
            barrier.wait(timeout=10)
            return queue.ack(receipt, 1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(ack, callers)).count(True), 1)

    def test_randomized_state_machine(self):
        rng = random.Random(20261004)
        jobs = {}
        receipts = []
        now = 0
        for step in range(600):
            now += rng.choice([0, 0.125, 0.5])
            queue = Queue(self.path)
            operation = rng.choice(['put', 'claim', 'claim', 'ack'])
            if operation == 'put':
                job_id = str(rng.randrange(50))
                if job_id in jobs:
                    with self.assertRaises(ValueError):
                        queue.put(job_id, {'id': job_id})
                else:
                    queue.put(job_id, {'id': job_id})
                    jobs[job_id] = {'done': False, 'expiry': None, 'token': None}
            elif operation == 'claim':
                duration = rng.choice([0.125, 1, 5])
                eligible = next((key for key, job in jobs.items()
                                 if not job['done'] and
                                 (job['expiry'] is None or job['expiry'] <= now)), None)
                claim = queue.claim(rng.choice(['a', 'b']), now, duration)
                if eligible is None:
                    self.assertIsNone(claim)
                else:
                    self.assertEqual(claim['payload'], {'id': eligible})
                    self.assertEqual(claim['expires_at'], now + duration)
                    jobs[eligible].update(expiry=now + duration, token=step)
                    receipts.append((claim['receipt'], eligible, step))
            elif receipts:
                receipt, job_id, token = rng.choice(receipts)
                job = jobs[job_id]
                expected = not job['done'] and job['token'] == token and now < job['expiry']
                self.assertIs(queue.ack(receipt, now), expected)
                if expected:
                    job['done'] = True


if __name__ == '__main__':
    unittest.main()
