"""Contract regression tests; run with ./run_python test_queue.py [module.py]."""
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


module_path = sys.argv.pop(1) if len(sys.argv) > 1 else 'app.py'
spec = importlib.util.spec_from_file_location('queue_under_test', module_path)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / 'queue.db'
        self.queue = app.Queue(self.path)

    def test_same_worker_reclaim_and_stale_receipt(self):
        self.queue.put('a', {'value': 1})
        first = self.queue.claim('worker', 0, 0.5)
        self.assertIsNone(self.queue.claim('worker', 0.25, 2))
        second = app.Queue(self.path).claim('worker', 0.5, 2)
        self.assertFalse(self.queue.ack(first['receipt'], 0.5))
        self.assertIsNone(self.queue.claim('worker', 0.5, 2))
        self.assertTrue(app.Queue(self.path).ack(second['receipt'], 0.5))
        self.assertFalse(self.queue.ack(second['receipt'], 0.5))
        self.assertIsNone(self.queue.claim('worker', 10, 1))

    def test_different_worker_and_worker_name_reuse(self):
        self.queue.put('a', None)
        first = self.queue.claim('a', 0, 1)
        second = self.queue.claim('b', 1, 1)
        self.assertFalse(self.queue.ack(first['receipt'], 1))
        third = self.queue.claim('a', 2, 1)
        self.assertFalse(self.queue.ack(first['receipt'], 2))
        self.assertFalse(self.queue.ack(second['receipt'], 2))
        self.assertTrue(self.queue.ack(third['receipt'], 2))

    def test_expiry_boundaries_and_python_arithmetic(self):
        for index, (now, duration) in enumerate([
            (-10**12, 10**12), (0, 0.125), (0.1, 0.2),
            (10**12, 10**12), (10**12, 5e-324), (0, 5e-324),
        ]):
            with self.subTest(now=now, duration=duration):
                queue = app.Queue(pathlib.Path(self.directory.name) / f'{index}.db')
                queue.put('a', None)
                delivery = queue.claim('worker', now, duration)
                expiry = now + duration
                self.assertEqual(delivery['expires_at'], expiry)
                if expiry > now:
                    before = max(now, math.nextafter(expiry, -math.inf))
                    self.assertIsNone(queue.claim('worker', before, 1))
                self.assertFalse(queue.ack(delivery['receipt'], expiry))
                self.assertIsNotNone(queue.claim('worker', expiry, 1))

    def test_invalid_durations_do_not_modify_queue(self):
        self.queue.put('a', 'first')
        for duration in [True, False, 0, -0.0, -1, -0.25,
                         float('inf'), -float('inf'), float('nan')]:
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    self.queue.claim('bad', 0, duration)
        delivery = self.queue.claim('good', 0, 2)
        self.assertEqual(delivery['payload'], 'first')
        for duration in [True, False, 0, -1, float('nan')]:
            with self.assertRaises(ValueError):
                self.queue.claim('bad', 1, duration)
        self.assertTrue(self.queue.ack(delivery['receipt'], 1))

    def test_empty_queue_still_validates_duration(self):
        self.assertIsNone(self.queue.claim('worker', 0, 1))
        with self.assertRaises(ValueError):
            self.queue.claim('worker', 0, True)

    def test_fifo_skips_unexpired_and_completed(self):
        for name in ['first', 'second', 'third']:
            self.queue.put(name, name)
        first = self.queue.claim('worker', 0, 2)
        second = self.queue.claim('worker', 0, 1)
        self.assertEqual(second['payload'], 'second')
        self.assertTrue(self.queue.ack(second['receipt'], 0))
        third = self.queue.claim('worker', 0, 3)
        self.assertEqual(third['payload'], 'third')
        self.assertIsNone(self.queue.claim('worker', 1, 2))
        reclaimed = self.queue.claim('worker', 2, 2)
        self.assertEqual(first['payload'], 'first')
        self.assertEqual(reclaimed['payload'], 'first')

    def test_payload_values_and_no_aliasing(self):
        values = [None, True, False, '', '中文\x00\ud800', 0, -42, 2**100,
                  0.125, -0.0, 1e308, [], {}, [[], {}],
                  {'a': [1, None, {'b': []}]}]
        for index, value in enumerate(values):
            self.queue.put(str(index), value)
        for value in values:
            delivery = self.queue.claim('worker', 0, 1)
            self.assertEqual(set(delivery), {'payload', 'expires_at', 'receipt'})
            self.assertEqual(delivery['payload'], value)
            self.assertTrue(self.queue.ack(delivery['receipt'], 0))
        source = {'a': [{'b': [1]}]}
        self.queue.put('mutable', source)
        source['a'][0]['b'].append(2)
        first = self.queue.claim('worker', 0, 1)
        first['payload']['a'].clear()
        second = app.Queue(self.path).claim('worker', 1, 1)
        self.assertEqual(second['payload'], {'a': [{'b': [1]}]})
        self.assertFalse(self.queue.ack(first['receipt'], 1))
        self.assertEqual(second['payload'], {'a': [{'b': [1]}]})
        self.assertIsNot(first['receipt'], second['receipt'])
        self.assertTrue(self.queue.ack(second['receipt'], 1))

    def test_unencodable_payloads_are_atomic(self):
        circular = []
        circular.append(circular)
        deep = None
        for _ in range(10000):
            deep = [deep]
        invalid = [object(), b'bytes', {1, 2}, complex(1, 2),
                   {'bad': float('nan')}, [float('inf')],
                   {float('-inf'): 1}, circular, deep]
        self.queue.put('existing', {'saved': True})
        current = self.queue.claim('worker', 0, 10)
        for index, value in enumerate(invalid):
            with self.subTest(index=index):
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    json.dumps(value, allow_nan=False)
                for job_id in ['existing', f'invalid-{index}']:
                    with self.assertRaises((TypeError, ValueError, RecursionError)):
                        self.queue.put(job_id, value)
        self.assertIsNone(self.queue.claim('other', 1, 1))
        self.assertTrue(self.queue.ack(current['receipt'], 1))
        self.assertIsNone(self.queue.claim('other', 10, 1))
        for index in range(len(invalid)):
            self.queue.put(f'invalid-{index}', index)

    def test_duplicate_id_and_reopening(self):
        self.queue.put('id', {'original': True})
        with self.assertRaises(ValueError):
            app.Queue(self.path).put('id', 'replacement')
        delivery = app.Queue(self.path).claim('worker', 0, 3)
        self.assertEqual(delivery['payload'], {'original': True})
        with self.assertRaises(ValueError):
            self.queue.put('id', None)
        self.assertTrue(app.Queue(self.path).ack(delivery['receipt'], 1))
        with self.assertRaises(ValueError):
            app.Queue(self.path).put('id', None)
        self.assertIsNone(app.Queue(self.path).claim('worker', 3, 1))

    def test_atomic_claim_and_ack_across_connections(self):
        queues = [app.Queue(self.path) for _ in range(8)]
        self.queue.put('a', 'only job')
        barrier = threading.Barrier(len(queues))

        def claim(queue):
            barrier.wait()
            return queue.claim('same', 0, 1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(queues)) as pool:
            deliveries = [item for item in pool.map(claim, queues) if item is not None]
        self.assertEqual(len(deliveries), 1)
        receipt = deliveries[0]['receipt']

        def ack(queue):
            barrier.wait()
            return queue.ack(receipt, 0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(queues)) as pool:
            self.assertEqual(sum(pool.map(ack, queues)), 1)

    def test_deterministic_operation_sequences(self):
        rng = random.Random(127)
        queues = [self.queue, app.Queue(self.path)]
        jobs = {}
        receipts = []
        now = -10.0
        for step in range(600):
            now += rng.choice([0, 0.125, 0.5])
            queue = rng.choice(queues)
            operation = rng.randrange(3)
            if operation == 0:
                job_id = str(rng.randrange(45))
                if job_id in jobs:
                    with self.assertRaises(ValueError):
                        queue.put(job_id, job_id)
                else:
                    queue.put(job_id, job_id)
                    jobs[job_id] = {'state': 'ready', 'generation': 0}
            elif operation == 1:
                worker = rng.choice(['a', 'b'])
                duration = rng.choice([0.125, 0.5, 2])
                eligible = next((job_id for job_id, job in jobs.items()
                                 if job['state'] == 'ready' or
                                 (job['state'] == 'leased' and job['expiry'] <= now)), None)
                actual = queue.claim(worker, now, duration)
                if eligible is None:
                    self.assertIsNone(actual)
                else:
                    job = jobs[eligible]
                    job.update(state='leased', worker=worker, expiry=now + duration,
                               generation=job['generation'] + 1)
                    self.assertEqual(actual['payload'], eligible)
                    self.assertEqual(actual['expires_at'], job['expiry'])
                    receipts.append((actual['receipt'], eligible, job['generation']))
            elif receipts:
                receipt, job_id, generation = rng.choice(receipts)
                job = jobs[job_id]
                expected = (job['state'] == 'leased' and job['expiry'] > now
                            and job['generation'] == generation)
                self.assertEqual(queue.ack(receipt, now), expected, f'step {step}')
                if expected:
                    job['state'] = 'done'


if __name__ == '__main__':
    unittest.main()
