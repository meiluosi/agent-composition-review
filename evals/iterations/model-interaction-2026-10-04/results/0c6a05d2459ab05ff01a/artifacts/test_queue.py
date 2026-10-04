"""Contract regression tests; optionally select the module with QUEUE_MODULE."""
import copy
import importlib
import json
import os
from pathlib import Path
import random
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor


Queue = importlib.import_module(os.environ.get("QUEUE_MODULE", "app")).Queue


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "queue.db"
        self.queue = Queue(self.path)

    def test_same_worker_stale_receipt(self):
        self.queue.put("one", {"items": []})
        first = self.queue.claim("worker", 0, 0.5)
        second = Queue(self.path).claim("worker", 0.5, 2)
        self.assertFalse(self.queue.ack(first["receipt"], 0.75))
        self.assertIsNone(self.queue.claim("other", 0.75, 2))
        self.assertTrue(Queue(self.path).ack(second["receipt"], 0.75))
        self.assertFalse(self.queue.ack(second["receipt"], 0.75))

    def test_expiry_and_worker_replacement(self):
        self.queue.put("one", None)
        first = self.queue.claim("a", -2, 0.25)
        self.assertEqual(first["expires_at"], -1.75)
        self.assertIsNone(self.queue.claim("a", -1.8, 1))
        self.assertIsNone(self.queue.claim("b", -1.8, 1))
        self.assertFalse(self.queue.ack(first["receipt"], -1.75))
        second = self.queue.claim("b", -1.75, 0.5)
        self.assertFalse(self.queue.ack(first["receipt"], -1.75))
        self.assertTrue(self.queue.ack(second["receipt"], -1.5))
        self.assertIsNone(self.queue.claim("c", 10, 1))

    def test_fifo_and_reopen(self):
        self.assertIsNone(self.queue.claim("w", 0, 1))
        for job_id in ("z", "a", "m"):
            self.queue.put(job_id, job_id)
        first = self.queue.claim("w", 0, 2)
        self.assertEqual(first["payload"], "z")
        second = Queue(self.path).claim("w", 0, 10)
        self.assertEqual(second["payload"], "a")
        again = Queue(self.path).claim("w", 2, 10)
        self.assertEqual(again["payload"], "z")
        self.assertTrue(self.queue.ack(again["receipt"], 2))
        self.assertTrue(self.queue.ack(second["receipt"], 2))
        last = self.queue.claim("w", 2, 1)
        self.assertEqual(last["payload"], "m")
        self.assertTrue(self.queue.ack(last["receipt"], 2))
        for job_id in ("z", "a", "m"):
            with self.assertRaises(ValueError):
                Queue(self.path).put(job_id, "replacement")

    def test_payload_values_and_isolation(self):
        nested = []
        for _ in range(100):
            nested = {"list": [nested]}
        values = [None, True, False, "\ud800中文", 0, 10**100, -1.25,
                  [], {}, [[], {}], {"a": [None, True, {"b": []}]}, nested]
        for i, value in enumerate(values):
            with self.subTest(value_index=i):
                expected = copy.deepcopy(value)
                self.queue.put(str(i), value)
                if isinstance(value, list):
                    value.append("input mutation")
                elif isinstance(value, dict):
                    value["mutation"] = True
                result = self.queue.claim("worker", i * 2, 1)
                self.assertEqual(set(result), {"payload", "receipt", "expires_at"})
                self.assertEqual(result["payload"], expected)
                if isinstance(result["payload"], (dict, list)):
                    result["payload"].clear()
                result["receipt"]["worker"] = "changed locally"
                redelivery = Queue(self.path).claim("worker", i * 2 + 1, 1)
                self.assertEqual(redelivery["payload"], expected)
                self.assertTrue(self.queue.ack(redelivery["receipt"], i * 2 + 1))

    def test_encoder_failures_are_atomic(self):
        cyclic = []
        cyclic.append(cyclic)
        deep = []
        for _ in range(10000):
            deep = [deep]
        # Verify rejection by the standard encoder itself; Python's recursion
        # limit does not describe the C encoder's nesting limit in Python 3.12.
        with self.assertRaises(RecursionError):
            json.dumps(deep, allow_nan=False)
        invalid = [object(), b"bytes", {1, 2}, 1j, float("nan"),
                   float("inf"), -float("inf"), {"x": [float("nan")]}, cyclic, deep]
        self.queue.put("existing", {"intact": True})
        for payload in invalid:
            for job_id in ("existing", "new"):
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.queue.put(job_id, payload)
        self.queue.put("new", "valid")
        first = self.queue.claim("w", 0, 1)
        self.assertEqual(first["payload"], {"intact": True})
        self.assertTrue(self.queue.ack(first["receipt"], 0))
        second = self.queue.claim("w", 0, 1)
        self.assertEqual(second["payload"], "valid")
        self.assertTrue(self.queue.ack(second["receipt"], 0))
        self.assertIsNone(self.queue.claim("w", 0, 1))

    def test_invalid_duration_is_atomic(self):
        invalid = [0, -0.0, -1, -0.25, float("nan"), float("inf"), -float("inf")]
        for duration in invalid:
            with self.assertRaises(ValueError):
                self.queue.claim("w", 0, duration)
        self.queue.put("one", None)
        for duration in invalid:
            with self.assertRaises(ValueError):
                self.queue.claim("w", 0, duration)
        result = self.queue.claim("w", 0, 1)
        for duration in invalid:
            with self.assertRaises(ValueError):
                self.queue.claim("other", 0, duration)
        self.assertTrue(self.queue.ack(result["receipt"], 0))

    def test_boolean_duration_rejected(self):
        self.queue.put("one", None)
        for duration in (True, False):
            with self.assertRaises(ValueError):
                self.queue.claim("w", 0, duration)
        self.assertIsNotNone(self.queue.claim("w", 0, 1))

    def test_numeric_extremes_follow_python_arithmetic(self):
        for i, (now, duration) in enumerate([
            (-10**12, 10**12), (0, 5e-324), (0.1, 0.2),
            (10**12, 1e-20), (10**12, 10**12),
        ]):
            queue = Queue(Path(self.directory.name) / (str(i) + ".db"))
            queue.put("one", i)
            result = queue.claim("w", now, duration)
            self.assertEqual(result["expires_at"], now + duration)
            self.assertFalse(queue.ack(result["receipt"], now + duration))
            self.assertIsNotNone(queue.claim("w", now + duration, 1))

    def test_concurrent_claims_and_acknowledgments(self):
        self.queue.put("one", {"value": 1})
        queues = [Queue(self.path) for _ in range(8)]
        barrier = threading.Barrier(len(queues))

        def claim(queue):
            barrier.wait()
            return queue.claim("same-worker", 0, 1)

        with ThreadPoolExecutor(max_workers=len(queues)) as pool:
            results = list(pool.map(claim, queues))
        deliveries = [result for result in results if result is not None]
        self.assertEqual(len(deliveries), 1)
        receipt = deliveries[0]["receipt"]

        def ack(queue):
            barrier.wait()
            return queue.ack(receipt, 0.5)

        with ThreadPoolExecutor(max_workers=len(queues)) as pool:
            self.assertEqual(sum(pool.map(ack, queues)), 1)

    def test_randomized_reference_scenario(self):
        rng = random.Random(194)
        jobs = []
        receipts = []
        now = 0
        for _ in range(400):
            now += rng.choice([0, 0.125, 0.5, 1])
            queue = Queue(self.path)
            action = rng.choice(["put", "claim", "claim", "ack"])
            if action == "put":
                job_id = str(len(jobs))
                queue.put(job_id, {"id": job_id})
                jobs.append({"id": job_id, "done": False, "generation": 0, "expires": now})
            elif action == "claim":
                worker = rng.choice(["a", "b"])
                duration = rng.choice([0.125, 0.5, 2])
                eligible = next((job for job in jobs if not job["done"] and
                                 (job["generation"] == 0 or job["expires"] <= now)), None)
                result = queue.claim(worker, now, duration)
                if eligible is None:
                    self.assertIsNone(result)
                else:
                    eligible["generation"] += 1
                    eligible["expires"] = now + duration
                    self.assertEqual(result["payload"], {"id": eligible["id"]})
                    self.assertEqual(result["expires_at"], now + duration)
                    receipts.append((result["receipt"], eligible, eligible["generation"]))
            elif receipts:
                receipt, job, generation = rng.choice(receipts)
                expected = not job["done"] and job["generation"] == generation and job["expires"] > now
                self.assertIs(queue.ack(receipt, now), expected)
                if expected:
                    job["done"] = True


if __name__ == "__main__":
    unittest.main(verbosity=2)
