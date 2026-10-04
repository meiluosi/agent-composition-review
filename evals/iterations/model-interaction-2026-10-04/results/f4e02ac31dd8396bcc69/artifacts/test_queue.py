"""Contract regressions; optional argument selects the implementation module."""
import concurrent.futures
import importlib
import json
import math
from pathlib import Path
import random
import sys
import tempfile
import threading
import unittest

implementation = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app')


class QueueContract(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'queue.db'
        self.q = implementation.Queue(self.path)

    def reopen(self):
        return implementation.Queue(self.path)

    def test_stale_same_worker_receipt(self):
        self.q.put('job', {'value': 1})
        old = self.q.claim('worker', 0, 0.5)
        current = self.reopen().claim('worker', 0.5, 2)
        self.assertFalse(self.reopen().ack(old['receipt'], 0.5))
        self.assertTrue(self.q.ack(current['receipt'], 0.5))
        self.assertFalse(self.reopen().ack(current['receipt'], 0.5))

    def test_stale_other_worker_receipt(self):
        self.q.put('job', None)
        old = self.q.claim('a', -1, 0.5)
        self.assertIsNone(self.reopen().claim('a', -0.75, 1))
        self.assertIsNone(self.reopen().claim('b', -0.5 - 0.01, 1))
        self.assertFalse(self.q.ack(old['receipt'], -0.5))
        current = self.q.claim('b', -0.5, 0.25)
        self.assertFalse(self.q.ack(old['receipt'], -0.5))
        self.assertTrue(self.reopen().ack(current['receipt'], -0.5))

    def test_invalid_durations_leave_ready_and_leased_jobs_intact(self):
        self.q.put('job', [1])
        invalid = [True, False, 0, 0.0, -0.0, -1, -0.1,
                   float('nan'), float('inf'), -float('inf')]
        for duration in invalid:
            with self.subTest(state='ready', duration=duration):
                with self.assertRaises(ValueError):
                    self.q.claim('worker', 0, duration)
        current = self.q.claim('worker', 0, 1)
        for duration in invalid:
            with self.subTest(state='leased', duration=duration):
                with self.assertRaises(ValueError):
                    self.q.claim('other', 0, duration)
        self.assertTrue(self.reopen().ack(current['receipt'], 0))
        with self.assertRaises(ValueError):
            self.q.claim('worker', 1, True)

    def test_fifo_reclaims_and_completed_ids(self):
        for job_id in ['a', 'b', 'c']:
            self.q.put(job_id, job_id)
        first = self.q.claim('worker', 0, 1)
        second = self.q.claim('worker', 0, 10)
        self.assertEqual([first['payload'], second['payload']], ['a', 'b'])
        for job_id in ['a', 'b', 'c']:
            with self.assertRaises(ValueError):
                self.reopen().put(job_id, 'changed')
        reclaimed = self.reopen().claim('worker', 1, 2)
        self.assertEqual(reclaimed['payload'], 'a')
        self.assertTrue(self.q.ack(reclaimed['receipt'], 1))
        with self.assertRaises(ValueError):
            self.reopen().put('a', 'replacement')
        self.assertEqual(self.q.claim('worker', 1, 2)['payload'], 'c')
        self.assertIsNone(self.q.claim('worker', 1, 2))

    def test_payload_values_and_isolation(self):
        values = [None, True, False, '', '文字\x00\ud800', 0, -42, 2**100,
                  -0.0, 0.125, 1e200, [], {},
                  {'': [], 'nested': [None, {'x': [1, 2]}]},
                  {'\ud800\udfff': 'surrogate pair', '\U000103ff': 'code point'}]
        for index, value in enumerate(values):
            self.q.put(str(index), value)
        for index, value in enumerate(values):
            result = self.reopen().claim('worker', 0, 1)
            self.assertEqual(set(result), {'payload', 'expires_at', 'receipt'})
            self.assertEqual(result['payload'], value)
            if isinstance(value, float) and value == 0:
                self.assertEqual(math.copysign(1, result['payload']), -1)
            self.assertTrue(self.q.ack(result['receipt'], 0))
        payload = {'nested': [{'values': [1, 2]}]}
        self.q.put('snapshot', payload)
        payload['nested'][0]['values'].append(3)
        first = self.q.claim('worker', 0, 1)
        first['payload']['nested'][0]['values'].clear()
        first['receipt']['worker'] = 'mutated locally'
        next_claim = self.reopen().claim('worker', 1, 1)
        self.assertEqual(next_claim['payload'], {'nested': [{'values': [1, 2]}]})
        self.assertTrue(self.reopen().ack(next_claim['receipt'], 1))

    def test_encoder_errors_are_atomic(self):
        cycle = []
        cycle.append(cycle)
        deep = []
        for _ in range(10000):
            deep = [deep]
        # Python 3.12's C encoder depth limit differs from getrecursionlimit().
        with self.assertRaises(RecursionError):
            json.dumps(deep, allow_nan=False)
        invalid = [object(), b'bytes', {1, 2}, {('key',): 1}, cycle, deep,
                   float('nan'), float('inf'), -float('inf'),
                   {'nested': [float('nan')]}]
        self.q.put('existing', ['original'])
        for value in invalid:
            for job_id in ['bad', 'existing']:
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.q.put(job_id, value)
        result = self.reopen().claim('worker', 0, 1)
        self.assertEqual(result['payload'], ['original'])
        self.assertTrue(self.q.ack(result['receipt'], 0))
        self.assertIsNone(self.q.claim('worker', 0, 1))
        self.reopen().put('bad', 'valid now')
        self.assertEqual(self.q.claim('worker', 0, 1)['payload'], 'valid now')

    def test_python_expiry_arithmetic(self):
        cases = [(0.1, 0.2), (-10**12, 10**12), (10**12, 10**12),
                 (10**12, 0.000001), (-0.25, 0.125)]
        for index, (now, duration) in enumerate(cases):
            # Independent queues keep each scenario's time nondecreasing.
            q = implementation.Queue(Path(self.directory.name) / f'numeric{index}.db')
            q.put('job', None)
            claim = q.claim('worker', now, duration)
            expiry = now + duration
            self.assertEqual(claim['expires_at'], expiry)
            self.assertFalse(q.ack(claim['receipt'], expiry))
            self.assertIsNotNone(q.claim('worker', expiry, 1))

    def test_deep_payload_within_encoder_limit(self):
        value = 'leaf'
        for _ in range(1100):
            value = [value]
        json.dumps(value, allow_nan=False)
        self.q.put('deep', value)
        result = self.reopen().claim('worker', 0, 1)
        value = result['payload']
        for _ in range(1100):
            self.assertEqual(len(value), 1)
            value = value[0]
        self.assertEqual(value, 'leaf')
        self.assertTrue(self.q.ack(result['receipt'], 0))

    def test_all_python_strings_for_identifiers(self):
        names = ['普通', 'embedded\x00nul', '\ud800', '\udfff', '\ud800\udfff']
        for name in names:
            self.q.put(name, {'id': name})
        for name in names:
            result = self.reopen().claim(name, 0, 1)
            self.assertEqual(result['payload'], {'id': name})
            self.assertTrue(self.reopen().ack(result['receipt'], 0))
            with self.assertRaises(ValueError):
                self.q.put(name, None)

    def test_existing_database_and_receipt_compatibility(self):
        original = importlib.import_module('original')
        old = original.Queue(self.path)
        old.put('legacy', {'legacy': True})
        receipt = old.claim('worker', 0, 2)['receipt']
        self.assertTrue(self.reopen().ack(receipt, 1))
        self.assertIsNone(self.reopen().claim('worker', 2, 1))
        with self.assertRaises(ValueError):
            self.reopen().put('legacy', None)

    def test_atomic_concurrent_claims_and_acks(self):
        workers = 12
        barrier = threading.Barrier(workers)
        self.q.put('one', 1)
        def claim(_):
            q = self.reopen()
            barrier.wait(timeout=10)
            return q.claim('shared-worker', 0, 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(claim, range(workers)))
        active = [result for result in results if result is not None]
        self.assertEqual(len(active), 1)
        barrier = threading.Barrier(workers)
        def ack(_):
            q = self.reopen()
            barrier.wait(timeout=10)
            return q.ack(active[0]['receipt'], 0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(ack, range(workers)))
        self.assertEqual(results.count(True), 1)
        self.assertIsNone(self.reopen().claim('worker', 1, 1))

    def test_deterministic_model_sequence(self):
        rng = random.Random(6021)
        jobs = []
        receipts = []
        now = -10.0
        for step in range(600):
            now += rng.choice([0, 0.125, 0.5])
            q = self.reopen()
            operation = rng.choice(['put', 'claim', 'claim', 'ack'])
            if operation == 'put':
                job = {'id': str(step), 'done': False, 'generation': 0, 'expiry': None}
                q.put(job['id'], {'id': job['id']})
                jobs.append(job)
            elif operation == 'claim':
                duration = rng.choice([0.125, 0.5, 2])
                worker = rng.choice(['same', 'other'])
                expected = next((j for j in jobs if not j['done'] and
                                 (j['expiry'] is None or j['expiry'] <= now)), None)
                result = q.claim(worker, now, duration)
                if expected is None:
                    self.assertIsNone(result)
                else:
                    self.assertEqual(result['payload'], {'id': expected['id']})
                    self.assertEqual(result['expires_at'], now + duration)
                    expected['generation'] += 1
                    expected['expiry'] = now + duration
                    receipts.append((result['receipt'], expected, expected['generation']))
            elif receipts:
                receipt, job, generation = rng.choice(receipts)
                expected = (not job['done'] and generation == job['generation']
                            and now < job['expiry'])
                self.assertEqual(q.ack(receipt, now), expected)
                if expected:
                    job['done'] = True


if __name__ == '__main__':
    unittest.main(verbosity=2)
