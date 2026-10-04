"""Contract checks; accepts an optional implementation path."""
import concurrent.futures
import importlib.util
import json
import math
import pathlib
import sys
import tempfile
import threading
import unittest


source = sys.argv.pop(1) if len(sys.argv) > 1 else "app.py"
spec = importlib.util.spec_from_file_location("queue_under_test", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / "queue.db"
        self.q = module.Queue(self.path)

    def test_fractional_lease(self):
        self.q.put("one", [])
        first = self.q.claim("worker", 0.25, 0.5)
        self.assertEqual(set(first), {"payload", "expires_at", "receipt"})
        self.assertEqual(first["expires_at"], 0.75)
        self.assertIsNone(self.q.claim("worker", 0.25, 0.5))
        self.assertIsNone(self.q.claim("other", 0.5, 0.5))
        self.assertTrue(self.q.ack(first["receipt"], math.nextafter(0.75, -math.inf)))

    def test_expiry_and_same_worker_replacement(self):
        self.q.put("one", {})
        first = self.q.claim("worker", 0, 2)
        self.assertFalse(self.q.ack(first["receipt"], 2))
        reopened = module.Queue(self.path)
        second = reopened.claim("worker", 2, 2)
        self.assertNotEqual(first["receipt"], second["receipt"])
        self.assertFalse(self.q.ack(first["receipt"], 2))
        self.assertIsNone(self.q.claim("other", 2, 2))
        self.assertTrue(self.q.ack(second["receipt"], 3))
        self.assertFalse(reopened.ack(second["receipt"], 3))
        self.assertIsNone(reopened.claim("worker", 100, 2))
        with self.assertRaises(ValueError):
            reopened.put("one", "duplicate after completion")

    def test_fifo_among_eligible_jobs(self):
        for job_id in ("first", "second", "third"):
            self.q.put(job_id, job_id)
        self.assertEqual(self.q.claim("a", 0, 4)["payload"], "first")
        second = self.q.claim("b", 0, 10)
        self.assertEqual(second["payload"], "second")
        self.assertTrue(self.q.ack(second["receipt"], 1))
        self.assertEqual(self.q.claim("c", 4, 4)["payload"], "first")
        self.assertEqual(self.q.claim("d", 4, 4)["payload"], "third")
        self.assertIsNone(self.q.claim("e", 4, 4))

    def test_json_values_and_isolation(self):
        values = [None, True, False, "雪", "", 123, -42, 1.25, [], {},
                  {"nested": [[], {}, {"x": [1, None]}]}, 10**100]
        for index, value in enumerate(values):
            self.q.put(str(index), value)
        reopened = module.Queue(self.path)
        for value in values:
            delivery = reopened.claim("worker", 0, 1)
            self.assertEqual(delivery["payload"], value)
            self.assertTrue(self.q.ack(delivery["receipt"], 0))
        payload = {"nested": [{"x": []}]}
        self.q.put("mutable", payload)
        payload["nested"][0]["x"].append("changed input")
        delivery = self.q.claim("worker", 0, 1)
        self.assertEqual(delivery["payload"], {"nested": [{"x": []}]})
        delivery["payload"]["nested"].clear()
        delivery["receipt"].clear()
        self.assertIsNone(reopened.claim("other", 0, 1))
        self.assertEqual(reopened.claim("other", 1, 1)["payload"],
                         {"nested": [{"x": []}]})

    def test_rejected_payloads_have_no_effect(self):
        cycle = []
        cycle.append(cycle)
        deep = []
        for _ in range(10000):
            deep = [deep]
        with self.assertRaises(RecursionError):
            json.dumps(deep, allow_nan=False)
        invalid = [object(), {1, 2}, b"bytes", float("nan"),
                   float("inf"), float("-inf"), {"nested": [float("nan")]},
                   cycle, deep]
        self.q.put("existing", "unchanged")
        active = self.q.claim("worker", 0, 5)
        for index, value in enumerate(invalid):
            with self.assertRaises((TypeError, ValueError, RecursionError)):
                self.q.put(str(index), value)
            self.q.put(str(index), "valid")
        with self.assertRaises(ValueError):
            self.q.put("existing", "replacement")
        self.assertTrue(self.q.ack(active["receipt"], 1))
        for _ in invalid:
            delivery = self.q.claim("worker", 1, 5)
            self.assertEqual(delivery["payload"], "valid")
            self.assertTrue(self.q.ack(delivery["receipt"], 1))
        self.assertIsNone(self.q.claim("worker", 1, 5))

    def test_invalid_durations_have_no_effect(self):
        self.q.put("one", "unchanged")
        active = self.q.claim("worker", 0, 5)
        for duration in (0, 0.0, -0.0, -1, -0.25, float("nan"),
                         float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                self.q.claim("other", 1, duration)
            self.assertIsNone(self.q.claim("other", 1, 5))
        self.assertTrue(self.q.ack(active["receipt"], 1))
        with self.assertRaises(ValueError):
            self.q.claim("worker", 1, 0)

    def test_expiry_uses_python_arithmetic(self):
        for index, (now, duration) in enumerate([
            (-10**12, 0.5), (0, 1.75), (10**12, 10**12),
            (10**12, 1e-10), (0, 5e-324), (-1.25, 0.75),
        ]):
            with self.subTest(now=now, duration=duration):
                queue = module.Queue(pathlib.Path(self.directory.name) / f"case{index}.db")
                queue.put("one", None)
                delivery = queue.claim("worker", now, duration)
                self.assertEqual(delivery["expires_at"], now + duration)
                self.assertEqual(queue.ack(delivery["receipt"], now), now < now + duration)

    def test_atomic_claims_and_acknowledgements(self):
        self.q.put("one", "value")
        queues = [module.Queue(self.path) for _ in range(8)]
        barrier = threading.Barrier(len(queues))

        def claim(queue):
            barrier.wait()
            return queue.claim("same-worker", 0, 10)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            deliveries = list(pool.map(claim, queues))
        winners = [delivery for delivery in deliveries if delivery is not None]
        self.assertEqual(len(winners), 1)
        receipt = winners[0]["receipt"]

        def ack(queue):
            barrier.wait()
            return queue.ack(receipt, 1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(ack, queues)), 1)


if __name__ == "__main__":
    unittest.main()
