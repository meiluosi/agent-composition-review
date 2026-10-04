"""Contract regression tests; optionally set QUEUE_MODULE=original for comparison."""
import concurrent.futures
import importlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import unittest


Queue = importlib.import_module(os.environ.get("QUEUE_MODULE", "app")).Queue


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "queue.db"
        self.queue = Queue(self.path)

    def test_fractional_lease_and_exact_expiry(self):
        self.queue.put("job", {"value": 1})
        first = self.queue.claim("worker", 10, 0.5)
        self.assertEqual(set(first), {"payload", "expires_at", "receipt"})
        self.assertEqual(first["expires_at"], 10.5)
        self.assertIsNone(self.queue.claim("worker", 10.25, 2))
        self.assertFalse(self.queue.ack(first["receipt"], 10.5))
        second = self.queue.claim("worker", 10.5, 1.25)
        self.assertEqual(second["expires_at"], 11.75)
        self.assertFalse(self.queue.ack(first["receipt"], 10.5))
        self.assertTrue(self.queue.ack(second["receipt"], 11.5))
        self.assertFalse(self.queue.ack(second["receipt"], 11.5))

    def test_fractional_ack_before_expiry(self):
        self.queue.put("job", None)
        delivery = self.queue.claim("worker", -0.25, 0.75)
        self.assertEqual(delivery["expires_at"], 0.5)
        self.assertTrue(self.queue.ack(delivery["receipt"], 0.25))

    def test_invalid_durations_do_not_change_jobs(self):
        invalid = [True, False, 0, 0.0, -0.5, -10**12,
                   float("nan"), float("inf"), -float("inf")]
        self.queue.put("job", "payload")
        for duration in invalid:
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    self.queue.claim("worker", 0, duration)
        delivery = self.queue.claim("worker", 0, 10)
        self.assertIsNotNone(delivery)
        for duration in invalid:
            with self.subTest(leased_duration=duration):
                with self.assertRaises(ValueError):
                    self.queue.claim("other", 1, duration)
        self.assertTrue(self.queue.ack(delivery["receipt"], 2))

    def test_fifo_including_expired_and_completed(self):
        self.assertIsNone(self.queue.claim("worker", 0, 1))
        for job in ["first", "second", "third"]:
            self.queue.put(job, job)
        first = self.queue.claim("worker", 0, 2)
        second = self.queue.claim("worker", 0, 10)
        self.assertEqual(first["payload"], "first")
        self.assertEqual(second["payload"], "second")
        self.assertTrue(self.queue.ack(second["receipt"], 1))
        reclaimed = self.queue.claim("worker", 2, 10)
        self.assertEqual(reclaimed["payload"], "first")
        self.assertFalse(self.queue.ack(first["receipt"], 2))
        self.assertTrue(self.queue.ack(reclaimed["receipt"], 2))
        third = self.queue.claim("other", 2, 10)
        self.assertEqual(third["payload"], "third")
        self.assertIsNone(self.queue.claim("other", 2, 10))
        self.assertTrue(self.queue.ack(third["receipt"], 3))
        self.assertIsNone(self.queue.claim("worker", 12, 10))

    def test_persistence_cross_instance_receipts_and_duplicates(self):
        self.queue.put("job", {"nested": [1]})
        with self.assertRaises(ValueError):
            Queue(self.path).put("job", "replacement")
        delivery = Queue(self.path).claim("worker", 0, 10)
        self.assertEqual(delivery["payload"], {"nested": [1]})
        self.assertIsNone(Queue(self.path).claim("worker", 1, 10))
        self.assertTrue(Queue(self.path).ack(delivery["receipt"], 2))
        self.assertFalse(self.queue.ack(delivery["receipt"], 2))
        with self.assertRaises(ValueError):
            Queue(self.path).put("job", "replacement")
        self.assertIsNone(Queue(self.path).claim("worker", 10, 1))

    def test_payload_snapshot_and_returned_objects_are_independent(self):
        payload = {"items": [{"empty": []}], "dict": {}}
        self.queue.put("job", payload)
        payload["items"][0]["empty"].append("input mutation")
        first = self.queue.claim("worker", 0, 1)
        expected = {"items": [{"empty": []}], "dict": {}}
        self.assertEqual(first["payload"], expected)
        first["payload"]["items"][0]["empty"].append("output mutation")
        self.assertFalse(self.queue.ack(first["receipt"], 1))
        second = Queue(self.path).claim("worker", 1, 2)
        self.assertEqual(second["payload"], expected)
        first["receipt"].clear()
        self.assertTrue(self.queue.ack(second["receipt"], 2))

    def test_json_values_round_trip(self):
        values = [None, True, False, "", "中文\u0000", 0, -42, 10**100,
                  1.25, -0.0, [], {}, [[], {}, [False]],
                  {"": None, "nested": {"items": [1, 2.5, "x"]}}]
        for index, value in enumerate(values):
            self.queue.put(str(index), value)
        for value in values:
            delivery = self.queue.claim("worker", 0, 1)
            self.assertEqual(delivery["payload"], value)
            self.assertIs(type(delivery["payload"]), type(value))
            self.assertTrue(self.queue.ack(delivery["receipt"], 0))

    def test_rejected_payloads_do_not_insert_or_change_existing_jobs(self):
        circular = []
        circular.append(circular)
        deep = None
        for _ in range(10000):
            deep = [deep]
        # C JSON encoder depth limits need not equal Python's recursion limit.
        with self.assertRaises(RecursionError):
            json.dumps(deep, allow_nan=False)
        invalid = [object(), {1, 2}, b"bytes", float("nan"), float("inf"),
                   -float("inf"), {"nested": [float("nan")]}, circular, deep]
        self.queue.put("existing", "preserved")
        active = self.queue.claim("worker", 0, 10)
        for index, value in enumerate(invalid):
            with self.subTest(index=index):
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.queue.put("existing", value)
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.queue.put(str(index), value)
                self.queue.put(str(index), index)
        self.assertTrue(self.queue.ack(active["receipt"], 1))
        for index in range(len(invalid)):
            delivery = self.queue.claim("worker", 1, 1)
            self.assertEqual(delivery["payload"], index)
            self.assertTrue(self.queue.ack(delivery["receipt"], 1))
        self.assertIsNone(self.queue.claim("worker", 1, 1))

    def test_numeric_expiry_matches_python_arithmetic(self):
        cases = [(-10**12, 10**12), (10**12, 10**12),
                 (10**12, 0.125), (10**12, math.ulp(0.0)),
                 (0, math.ulp(0.0)), (-0.5, 0.25), (1.25, 2.75)]
        for index, (now, duration) in enumerate(cases):
            with self.subTest(now=now, duration=duration):
                q = Queue(Path(self.directory.name) / (str(index) + ".db"))
                q.put("job", None)
                delivery = q.claim("worker", now, duration)
                expiry = now + duration
                self.assertEqual(delivery["expires_at"], expiry)
                self.assertEqual(q.ack(delivery["receipt"], now), expiry > now)

    def test_separate_connections_claim_and_ack_atomically(self):
        count = 8
        queues = [Queue(self.path) for _ in range(count)]
        self.queue.put("job", "only job")

        def race(operation):
            barrier = threading.Barrier(count)

            def run(index):
                barrier.wait(timeout=10)
                return operation(queues[index], index)

            with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
                return list(pool.map(run, range(count)))

        for now in (0, 10):
            deliveries = race(lambda q, i: q.claim(str(i), now, 10))
            active = [delivery for delivery in deliveries if delivery is not None]
            self.assertEqual(len(active), 1)
            if now == 0:
                old_receipt = active[0]["receipt"]
        self.assertFalse(self.queue.ack(old_receipt, 10))
        acknowledgements = race(lambda q, i: q.ack(active[0]["receipt"], 11))
        self.assertEqual(acknowledgements.count(True), 1)
        self.assertIsNone(self.queue.claim("worker", 20, 1))


if __name__ == "__main__":
    unittest.main()
