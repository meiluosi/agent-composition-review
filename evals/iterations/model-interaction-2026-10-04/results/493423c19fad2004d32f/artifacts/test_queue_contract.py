"""Local contract checks; run with ./run_python test_queue_contract.py [module.py]."""
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


module_path = pathlib.Path(sys.argv.pop(1) if len(sys.argv) > 1 else "app.py")
spec = importlib.util.spec_from_file_location("queue_under_test", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Queue = module.Queue


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / "queue.db"
        self.queue = Queue(self.path)

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return list(db.iterdump())

    def test_payload_values_and_enqueue_snapshot(self):
        payload = [None, False, True, "", "中文\x00\ud800", 10**100, -0.0,
                   1.25, [], {}, {"": [[], {"x": "initial"}]}]
        expected = json.loads(json.dumps(payload))
        self.queue.put("id", payload)
        payload[-1][""][1]["x"] = "changed"
        delivery = Queue(self.path).claim("w", 0, 0.5)
        self.assertEqual(set(delivery), {"payload", "expires_at", "receipt"})
        self.assertEqual(delivery["payload"], expected)
        self.assertEqual(math.copysign(1, delivery["payload"][6]), -1)
        delivery["payload"][-1][""][1]["x"] = "changed again"
        retry = self.queue.claim("w", 0.5, 0.5)
        self.assertEqual(retry["payload"], expected)
        self.assertNotEqual(retry["receipt"], delivery["receipt"])

    def test_each_json_scalar_and_empty_container(self):
        values = [None, True, False, "", "hello", 0, -25, 1.5, [], {}]
        for index, payload in enumerate(values):
            self.queue.put(str(index), payload)
        for payload in values:
            delivery = self.queue.claim("w", 0, 1)
            self.assertEqual(delivery["payload"], payload)
            self.assertIs(type(delivery["payload"]), type(payload))
            self.assertTrue(self.queue.ack(delivery["receipt"], 0))
        self.assertIsNone(self.queue.claim("w", 0, 1))

    def test_invalid_payloads_preserve_state_and_id(self):
        self.queue.put("existing", {"safe": []})
        active = self.queue.claim("w", 0, 5)
        cycle = []
        cycle.append(cycle)
        invalid = [object(), {1, 2}, b"bytes", complex(1, 2), cycle,
                   float("nan"), float("inf"), -float("inf"),
                   {"nested": [float("nan")]}, {"nested": {1, 2}}]
        for index, value in enumerate(invalid):
            before = self.snapshot()
            with self.assertRaises((TypeError, ValueError, RecursionError)):
                self.queue.put(str(index), value)
            self.assertEqual(self.snapshot(), before)
            self.queue.put(str(index), "valid replacement")
        self.assertTrue(Queue(self.path).ack(active["receipt"], 1))

    def test_duplicate_id_before_and_after_completion(self):
        self.queue.put("id", "original")
        for state in ("ready", "leased", "done"):
            if state == "leased":
                delivery = self.queue.claim("w", 0, 5)
            elif state == "done":
                self.assertTrue(self.queue.ack(delivery["receipt"], 1))
            before = self.snapshot()
            with self.assertRaises(ValueError):
                Queue(self.path).put("id", "replacement")
            self.assertEqual(self.snapshot(), before)

    def test_oldest_eligible_and_exact_expiry(self):
        for key in ("first", "second", "third"):
            self.queue.put(key, key)
        first = self.queue.claim("same", -1, 0.25)
        second = self.queue.claim("same", -1, 2)
        self.assertEqual(first["payload"], "first")
        self.assertEqual(second["payload"], "second")
        self.assertEqual(self.queue.claim("other", -0.75, 0.5)["payload"], "first")
        self.assertFalse(self.queue.ack(first["receipt"], -0.75))
        self.assertEqual(self.queue.claim("same", -0.75, 1)["payload"], "third")
        self.assertIsNone(self.queue.claim("same", -0.75, 1))

    def test_stale_receipts_and_cross_instance_ack(self):
        self.queue.put("id", "value")
        first = self.queue.claim("same", 0, 0.25)
        before = self.snapshot()
        self.assertIsNone(Queue(self.path).claim("same", 0.125, 10))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.queue.ack(first["receipt"], 0.25))
        self.assertEqual(self.snapshot(), before)
        second = Queue(self.path).claim("same", 0.25, 1)
        before = self.snapshot()
        self.assertFalse(self.queue.ack(first["receipt"], 0.5))
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(Queue(self.path).ack(second["receipt"], 0.5))
        self.assertFalse(self.queue.ack(second["receipt"], 0.5))
        self.assertIsNone(Queue(self.path).claim("w", 100, 1))

    def test_invalid_durations_do_not_change_state(self):
        self.queue.put("id", [])
        for duration in (0, 0.0, -0.0, -1, -0.25, float("nan"),
                         float("inf"), -float("inf")):
            before = self.snapshot()
            with self.assertRaises(ValueError):
                self.queue.claim("w", 0, duration)
            self.assertEqual(self.snapshot(), before)
        delivery = self.queue.claim("w", 0, 1)
        self.assertTrue(self.queue.ack(delivery["receipt"], 0))

    def test_fractional_rounding_and_numeric_bounds(self):
        for index, (now, duration) in enumerate([
            (-10**12, 10**12), (10**12, 10**12), (0.1, 0.2),
            (10**12, 5e-324), (0, 5e-324), (-0.5, 0.25),
        ]):
            # Each pair is an independent queue/time scenario.
            q = Queue(pathlib.Path(self.directory.name) / f"bounds{index}.db")
            q.put("id", None)
            delivery = q.claim("w", now, duration)
            expiry = now + duration
            self.assertEqual(delivery["expires_at"], expiry)
            if now < expiry:
                self.assertIsNone(q.claim("w", now, duration))
            self.assertFalse(q.ack(delivery["receipt"], expiry))
            self.assertIsNotNone(q.claim("w", expiry, duration))

    def test_concurrent_claim_and_ack(self):
        for index in range(12):
            self.queue.put(str(index), index)
        queues = [Queue(self.path) for _ in range(24)]
        barrier = threading.Barrier(len(queues))

        def claim(q):
            barrier.wait(timeout=10)
            return q.claim("same", 0, 10)

        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
            deliveries = [value for value in pool.map(claim, queues) if value is not None]
        self.assertEqual(sorted(value["payload"] for value in deliveries), list(range(12)))
        receipt = deliveries[0]["receipt"]
        barrier = threading.Barrier(len(queues))

        def ack(q):
            barrier.wait(timeout=10)
            return q.ack(receipt, 1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
            self.assertEqual(sum(pool.map(ack, queues)), 1)

    def test_deep_payload_roundtrip(self):
        value = None
        for _ in range(8000):
            value = [value]
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            before = self.snapshot()
            with self.assertRaises((TypeError, ValueError, RecursionError)):
                self.queue.put("deep", value)
            self.assertEqual(self.snapshot(), before)
        else:
            self.queue.put("deep", value)
            actual = self.queue.claim("w", 0, 1)["payload"]
            for _ in range(8000):
                self.assertIsInstance(actual, list)
                self.assertEqual(len(actual), 1)
                actual = actual[0]
            self.assertIsNone(actual)

    def test_random_sequences_against_reference(self):
        rng = random.Random(70431)
        queues = [self.queue, Queue(self.path)]
        jobs = {}
        receipts = []
        now = -10.0
        for _ in range(1000):
            now += rng.choice([0, 0.125, 0.5, 1])
            q = rng.choice(queues)
            operation = rng.choice(["put", "claim", "claim", "ack"])
            if operation == "put":
                key = str(rng.randrange(100))
                if key in jobs:
                    with self.assertRaises(ValueError):
                        q.put(key, [key])
                else:
                    q.put(key, [key])
                    jobs[key] = {"state": "ready", "version": 0}
            elif operation == "claim":
                duration = rng.choice([0.125, 0.5, 1, 10])
                eligible = next((key for key, job in jobs.items()
                                 if job["state"] == "ready" or
                                 (job["state"] == "leased" and job["expiry"] <= now)), None)
                result = q.claim(rng.choice(["a", "b"]), now, duration)
                if eligible is None:
                    self.assertIsNone(result)
                else:
                    self.assertEqual(result["payload"], [eligible])
                    self.assertEqual(result["expires_at"], now + duration)
                    job = jobs[eligible]
                    job.update(state="leased", expiry=now + duration, version=job["version"] + 1)
                    receipts.append((eligible, job["version"], result["receipt"]))
            elif receipts:
                key, version, receipt = rng.choice(receipts)
                job = jobs[key]
                expected = job["state"] == "leased" and job["version"] == version and now < job["expiry"]
                self.assertIs(q.ack(receipt, now), expected)
                if expected:
                    job["state"] = "done"


if __name__ == "__main__":
    unittest.main(verbosity=2)
