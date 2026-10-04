"""Contract regressions; run with ./run_python test_queue.py [module.py]."""
import concurrent.futures
import importlib.util
import json
import math
import pathlib
import sys
import tempfile
import threading
import unittest


module_path = sys.argv.pop(1) if len(sys.argv) > 1 else "app.py"
spec = importlib.util.spec_from_file_location("queue_under_test", module_path)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class QueueContract(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / "queue.db"
        self.queue = app.Queue(self.path)

    def test_same_worker_stale_receipt(self):
        self.queue.put("job", {})
        old = self.queue.claim("worker", 0, 1)
        current = app.Queue(self.path).claim("worker", 1, 4)
        self.assertFalse(self.queue.ack(old["receipt"], 2))
        self.assertTrue(app.Queue(self.path).ack(current["receipt"], 2))
        self.assertFalse(self.queue.ack(current["receipt"], 2))

    def test_worker_reuse_after_other_worker(self):
        self.queue.put("job", [])
        first = self.queue.claim("A", 0, 1)
        second = self.queue.claim("B", 1, 1)
        third = self.queue.claim("A", 2, 1)
        self.assertFalse(self.queue.ack(first["receipt"], 2))
        self.assertFalse(self.queue.ack(second["receipt"], 2))
        self.assertTrue(self.queue.ack(third["receipt"], 2))

    def test_invalid_durations_are_atomic(self):
        for i, duration in enumerate((True, False, 0, -0.0, -1, -0.25,
                                      float("nan"), float("inf"), -float("inf"))):
            with self.subTest(duration=duration):
                self.queue = app.Queue(self.path.with_name(f"duration{i}.db"))
                self.queue.put("job", "saved")
                with self.assertRaises(ValueError):
                    self.queue.claim("bad", 0, duration)
                delivery = self.queue.claim("good", 0, 1)
                self.assertIsNotNone(delivery)
                self.assertEqual(delivery["payload"], "saved")
                self.assertTrue(self.queue.ack(delivery["receipt"], 0))

    def test_invalid_duration_preserves_active_lease(self):
        self.queue.put("job", 1)
        delivery = self.queue.claim("A", 0, 4)
        with self.assertRaises(ValueError):
            self.queue.claim("B", 1, float("nan"))
        self.assertIsNone(self.queue.claim("B", 1, 1))
        self.assertTrue(self.queue.ack(delivery["receipt"], 1))

    def test_fractional_expiry_and_oldest_eligible(self):
        self.queue.put("first", 1)
        self.queue.put("second", 2)
        first = self.queue.claim("A", -0.5, 0.75)
        self.assertEqual(set(first), {"payload", "expires_at", "receipt"})
        self.assertEqual(first["expires_at"], 0.25)
        second = self.queue.claim("A", -0.5, 2)
        self.assertEqual(second["payload"], 2)
        self.assertIsNone(self.queue.claim("A", math.nextafter(0.25, -math.inf), 1))
        self.assertFalse(self.queue.ack(first["receipt"], 0.25))
        again = self.queue.claim("B", 0.25, 0.125)
        self.assertEqual(again["payload"], 1)
        self.assertEqual(again["expires_at"], 0.375)
        self.assertFalse(self.queue.ack(first["receipt"], 0.25))
        self.assertTrue(self.queue.ack(again["receipt"], 0.25))
        self.assertTrue(self.queue.ack(second["receipt"], 0.25))
        self.assertIsNone(self.queue.claim("A", 100, 1))

    def test_expired_old_job_precedes_unclaimed_new_job(self):
        self.queue.put("old", "old")
        self.queue.claim("A", 0, 1)
        self.queue.put("new", "new")
        self.assertEqual(self.queue.claim("B", 1, 2)["payload"], "old")
        self.assertEqual(self.queue.claim("B", 1, 2)["payload"], "new")

    def test_python_expiry_arithmetic(self):
        for i, (now, duration) in enumerate(((10**12, 10**12),
                                           (-10**12, 0.125),
                                           (10**12, 5e-324),
                                           (0, 5e-324))):
            with self.subTest(now=now, duration=duration):
                queue = app.Queue(self.path.with_name(f"numeric{i}.db"))
                queue.put("job", None)
                delivery = queue.claim("A", now, duration)
                expiry = now + duration
                self.assertEqual(delivery["expires_at"], expiry)
                self.assertEqual(queue.ack(delivery["receipt"], now), expiry > now)
                if expiry <= now:
                    self.assertIsNotNone(queue.claim("A", now, duration))

    def test_persistence_duplicates_and_receipt_portability(self):
        self.queue.put("job", [1, {}])
        other = app.Queue(self.path)
        with self.assertRaises(ValueError):
            other.put("job", "replacement")
        delivery = other.claim("A", 0, 3)
        with self.assertRaises(ValueError):
            self.queue.put("job", "replacement")
        self.assertIsNone(app.Queue(self.path).claim("A", 1, 1))
        self.assertTrue(self.queue.ack(json.loads(json.dumps(delivery["receipt"])), 1))
        with self.assertRaises(ValueError):
            app.Queue(self.path).put("job", "replacement")
        self.assertIsNone(other.claim("B", 10, 1))

    def test_payload_domain_and_isolation(self):
        values = [None, True, False, "\x00\ud800中文", 123, -3.25,
                  10**100, [], {}, {"nested": [[], {}, {"x": [1]}]}]
        for i, value in enumerate(values):
            self.queue.put(str(i), value)
        values[-1]["nested"][2]["x"].append(2)
        for i, value in enumerate(values):
            delivery = self.queue.claim("A", 0, 1)
            expected = {"nested": [[], {}, {"x": [1]}]} if i == len(values)-1 else value
            self.assertEqual(delivery["payload"], expected)
            if isinstance(delivery["payload"], dict):
                delivery["payload"]["changed"] = True
            elif isinstance(delivery["payload"], list):
                delivery["payload"].append("changed")
        # Every first-round lease expires together, preserving insertion order.
        for i, value in enumerate(values):
            delivery = app.Queue(self.path).claim("B", 1, 1)
            expected = {"nested": [[], {}, {"x": [1]}]} if i == len(values)-1 else value
            self.assertEqual(delivery["payload"], expected)
            self.assertTrue(self.queue.ack(delivery["receipt"], 1))

    def test_encoder_rejection_leaves_jobs_unchanged(self):
        cycle = []
        cycle.append(cycle)
        deep = None
        for _ in range(max(10000, sys.getrecursionlimit() * 10)):
            deep = [deep]
        with self.assertRaises(RecursionError):
            json.dumps(deep, allow_nan=False)
        invalid = [object(), {1, 2}, b"bytes", float("nan"),
                   {"nested": [float("inf")]}, cycle, deep]
        self.queue.put("existing", {"ok": []})
        active = self.queue.claim("A", 0, 10)
        for i, payload in enumerate(invalid):
            with self.subTest(index=i):
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.queue.put(f"bad{i}", payload)
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.queue.put("existing", payload)
                self.assertIsNone(self.queue.claim("B", 0, 1))
                self.queue.put(f"bad{i}", i)
                delivery = self.queue.claim("B", 0, 1)
                self.assertEqual(delivery["payload"], i)
                self.assertTrue(self.queue.ack(delivery["receipt"], 0))
        self.assertTrue(self.queue.ack(active["receipt"], 0))

    def test_concurrent_claim_and_ack(self):
        self.queue.put("job", {"value": 1})
        for now in (0, 1):
            queues = [app.Queue(self.path) for _ in range(8)]
            barrier = threading.Barrier(len(queues))

            def claim(queue):
                barrier.wait()
                return queue.claim("same_worker", now, 1)

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                deliveries = list(pool.map(claim, queues))
            claimed = [delivery for delivery in deliveries if delivery is not None]
            self.assertEqual(len(claimed), 1)
            if now == 0:
                stale = claimed[0]["receipt"]
            else:
                self.assertFalse(self.queue.ack(stale, now))
                barrier = threading.Barrier(len(queues))

                def ack(queue):
                    barrier.wait()
                    return queue.ack(claimed[0]["receipt"], now)

                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(ack, queues))
                self.assertEqual(results.count(True), 1)


if __name__ == "__main__":
    unittest.main()
