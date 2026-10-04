"""Contract checks; optionally test the original implementation instead."""
import copy
import importlib
import json
from pathlib import Path
import random
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor


module = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else "app")


class QueueContract(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "queue.db"
        self.queue = module.Queue(self.path)

    def test_fractional_expiry(self):
        for now, duration in [(0, 0.5), (1.25, 2.75), (-10.5, 0.125),
                              (10**12, 0.25), (0, 5e-324)]:
            with self.subTest(now=now, duration=duration):
                q = module.Queue(Path(self.directory.name) / f"{now}_{duration}.db")
                q.put("one", None)
                delivery = q.claim("w", now, duration)
                expiry = now + duration
                self.assertEqual(delivery["expires_at"], expiry)
                self.assertIsNone(q.claim("w", now, duration))
                self.assertFalse(q.ack(delivery["receipt"], expiry))
                replacement = q.claim("w", expiry, duration)
                self.assertIsNotNone(replacement)
                self.assertFalse(q.ack(delivery["receipt"], expiry))
                self.assertTrue(q.ack(replacement["receipt"], expiry))

    def test_python_rounding_can_expire_immediately(self):
        self.queue.put("one", [])
        delivery = self.queue.claim("w", 10**12, 5e-324)
        self.assertEqual(delivery["expires_at"], 10**12 + 5e-324)
        self.assertFalse(self.queue.ack(delivery["receipt"], 10**12))

    def test_invalid_durations_leave_lease_intact(self):
        self.queue.put("one", {"x": 1})
        delivery = self.queue.claim("w", 0, 10)
        for duration in [0, -1, -0.5, float("nan"), float("inf"), -float("inf")]:
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    self.queue.claim("other", 1, duration)
        self.assertIsNone(self.queue.claim("other", 1, 10))
        self.assertTrue(self.queue.ack(delivery["receipt"], 1))

    def test_fifo_reclaim_and_completion(self):
        for job in ["a", "b", "c"]:
            self.queue.put(job, job)
        first = self.queue.claim("w", 0, 2)
        second = self.queue.claim("w", 0, 10)
        self.assertEqual([first["payload"], second["payload"]], ["a", "b"])
        reclaimed = self.queue.claim("w", 2, 10)
        self.assertEqual(reclaimed["payload"], "a")
        self.assertFalse(self.queue.ack(first["receipt"], 2))
        self.assertTrue(self.queue.ack(reclaimed["receipt"], 2))
        self.assertFalse(self.queue.ack(reclaimed["receipt"], 2))
        self.assertTrue(self.queue.ack(second["receipt"], 2))
        third = self.queue.claim("other", 2, 10)
        self.assertEqual(third["payload"], "c")
        self.assertTrue(self.queue.ack(third["receipt"], 2))
        with self.assertRaises(ValueError):
            self.queue.put("a", "replacement")
        self.assertIsNone(self.queue.claim("w", 100, 1))

    def test_reopen_and_cross_instance_receipts(self):
        self.queue.put("one", {"nested": [1, None]})
        other = module.Queue(self.path)
        first = other.claim("w", 0, 2)
        self.assertIsNone(self.queue.claim("w", 1, 2))
        replacement = module.Queue(self.path).claim("w", 2, 2)
        self.assertFalse(other.ack(first["receipt"], 2))
        self.assertTrue(self.queue.ack(replacement["receipt"], 2))
        self.assertIsNone(module.Queue(self.path).claim("w", 10, 2))

    def test_original_database_compatibility(self):
        original = importlib.import_module("original")
        old = original.Queue(self.path)
        old.put("one", ["persisted"])
        delivery = old.claim("w", 0, 2)
        self.assertTrue(self.queue.ack(delivery["receipt"], 1))
        with self.assertRaises(ValueError):
            self.queue.put("one", [])

    def test_payload_domain_and_snapshot(self):
        payloads = [None, True, False, "", "中文\ud800", 0, -(10**100),
                    1.25, -0.0, [], {}, {"": [{"a": []}, {}, [None, False]]}]
        for index, value in enumerate(payloads):
            self.queue.put(str(index), value)
        for value in payloads:
            delivery = self.queue.claim("w", 0, 1)
            self.assertEqual(set(delivery), {"payload", "expires_at", "receipt"})
            self.assertIsInstance(delivery["receipt"], dict)
            self.assertEqual(delivery["payload"], value)
            self.assertTrue(self.queue.ack(delivery["receipt"], 0))
        source = {"items": [{"value": [1]}]}
        expected = copy.deepcopy(source)
        self.queue.put("snapshot", source)
        source["items"].clear()
        delivery = self.queue.claim("w", 1, 1)
        saved_receipt = copy.deepcopy(delivery["receipt"])
        delivery["payload"]["items"][0]["value"].append(2)
        delivery["receipt"].clear()
        replacement = self.queue.claim("w", 2, 1)
        self.assertEqual(replacement["payload"], expected)
        self.assertFalse(self.queue.ack(saved_receipt, 2))
        self.assertTrue(self.queue.ack(replacement["receipt"], 2))

    def test_rejected_payloads_and_duplicates_are_atomic(self):
        circular = []
        circular.append(circular)
        deep = None
        for _ in range(10000):
            deep = [deep]
        # Python 3.12's C JSON encoder has its own nesting limit.
        with self.assertRaises(RecursionError):
            json.dumps(deep, allow_nan=False)
        invalid = [object(), {1, 2}, b"bytes", float("nan"), float("inf"),
                   {"nested": [float("-inf")]}, circular, deep]
        self.queue.put("existing", ["unchanged"])
        for index, value in enumerate(invalid):
            with self.subTest(index=index):
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.queue.put("invalid", value)
        with self.assertRaises(ValueError):
            self.queue.put("existing", ["changed"])
        self.queue.put("invalid", "valid now")
        first = self.queue.claim("w", 0, 1)
        self.assertEqual(first["payload"], ["unchanged"])
        self.assertTrue(self.queue.ack(first["receipt"], 0))
        second = self.queue.claim("w", 0, 1)
        self.assertEqual(second["payload"], "valid now")
        self.assertTrue(self.queue.ack(second["receipt"], 0))
        self.assertIsNone(self.queue.claim("w", 0, 1))

    def test_concurrent_claim_and_ack(self):
        self.queue.put("one", "work")
        callers = [module.Queue(self.path) for _ in range(8)]
        barrier = threading.Barrier(len(callers))

        def claim(q):
            barrier.wait(timeout=10)
            return q.claim("same-worker", 0, 10)

        with ThreadPoolExecutor(max_workers=len(callers)) as pool:
            deliveries = list(pool.map(claim, callers))
        active = [delivery for delivery in deliveries if delivery is not None]
        self.assertEqual(len(active), 1)

        def ack(q):
            barrier.wait(timeout=10)
            return q.ack(active[0]["receipt"], 1)

        with ThreadPoolExecutor(max_workers=len(callers)) as pool:
            results = list(pool.map(ack, callers))
        self.assertEqual(results.count(True), 1)

    def test_generated_state_transitions(self):
        rng = random.Random(7823)
        jobs = []
        receipts = []
        now = 0
        for step in range(400):
            now += rng.choice([0, 0.125, 0.5, 1])
            action = rng.choice(["put", "claim", "claim", "ack"])
            q = module.Queue(self.path)
            if action == "put":
                q.put(str(step), {"id": step})
                jobs.append({"id": step, "done": False, "expiry": None, "claim": None})
            elif action == "claim":
                duration = rng.choice([0.125, 0.75, 1, 3.5])
                eligible = next((j for j in jobs if not j["done"] and
                                 (j["expiry"] is None or j["expiry"] <= now)), None)
                delivery = q.claim(rng.choice(["a", "b"]), now, duration)
                if eligible is None:
                    self.assertIsNone(delivery)
                else:
                    self.assertEqual(delivery["payload"], {"id": eligible["id"]})
                    self.assertEqual(delivery["expires_at"], now + duration)
                    token = len(receipts)
                    eligible.update(expiry=now + duration, claim=token)
                    receipts.append((delivery["receipt"], eligible, token))
            elif receipts:
                receipt, job, token = rng.choice(receipts)
                expected = not job["done"] and job["claim"] == token and job["expiry"] > now
                self.assertIs(q.ack(receipt, now), expected)
                if expected:
                    job["done"] = True


if __name__ == "__main__":
    unittest.main()
