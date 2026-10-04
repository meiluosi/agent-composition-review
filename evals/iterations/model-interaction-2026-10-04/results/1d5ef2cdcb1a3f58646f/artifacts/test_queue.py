"""Contract regressions; optionally pass original.py as the first argument."""
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


module_path = sys.argv.pop(1) if len(sys.argv) > 1 else "app.py"
spec = importlib.util.spec_from_file_location("queue_under_test", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Queue = module.Queue


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / "queue.db"
        self.q = Queue(self.path)

    def snapshot(self):
        db = sqlite3.connect(self.path)
        try:
            return db.execute("SELECT * FROM jobs ORDER BY seq").fetchall()
        finally:
            db.close()

    def test_payload_values_and_snapshot(self):
        values = [None, False, True, "", "\x00\ud800中文", "\ud83d\ude00",
                  {"\ud83d\ude00": "pair", "\U0001f600": "code point"}, 10**100, -2.5,
                  [], {}, [[], {}, {"nested": [None, True, 3.25]}]]
        for i, value in enumerate(values):
            self.q.put(str(i), value)
        mutable = {"items": [{"x": [1]}]}
        self.q.put("mutable", mutable)
        mutable["items"][0]["x"].append(2)
        values.append({"items": [{"x": [1]}]})
        for value in values:
            delivery = Queue(self.path).claim("worker", 0, 1.5)
            self.assertEqual(set(delivery), {"payload", "expires_at", "receipt"})
            self.assertEqual(delivery["payload"], value)
            self.assertEqual(delivery["expires_at"], 1.5)
            self.assertTrue(self.q.ack(delivery["receipt"], 0.25))
        self.assertIsNone(self.q.claim("worker", 0.25, 1))

    def test_expiry_stale_receipts_and_order(self):
        self.q.put("first", {"items": [1]})
        self.q.put("second", [])
        first = self.q.claim("same", -1, 0.5)
        first["payload"]["items"].append(2)
        second = self.q.claim("same", -1, 5)
        self.assertEqual(second["payload"], [])
        self.assertIsNone(self.q.claim("same", -0.75, 1))
        before = self.snapshot()
        self.assertFalse(self.q.ack(first["receipt"], -0.5))
        self.assertEqual(self.snapshot(), before)
        replacement = Queue(self.path).claim("same", -0.5, 1.25)
        self.assertEqual(replacement["payload"], {"items": [1]})
        before = self.snapshot()
        self.assertFalse(self.q.ack(first["receipt"], -0.5))
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.q.ack(replacement["receipt"], 0))
        self.assertFalse(self.q.ack(replacement["receipt"], 0))
        self.assertTrue(Queue(self.path).ack(second["receipt"], 0))
        self.assertIsNone(self.q.claim("same", 10, 1))
        with self.assertRaises(ValueError):
            self.q.put("first", "duplicate completed ID")

    def test_failed_put_and_claim_preserve_state(self):
        self.q.put("existing", [1])
        active = self.q.claim("w", 0, 5)
        cycle = []
        cycle.append(cycle)
        deep = None
        for _ in range(20000):
            deep = [deep]
        invalid = [object(), {1, 2}, b"bytes", complex(1, 2), float("nan"),
                   [float("inf")], {"x": -float("inf")}, cycle, deep]
        for value in invalid:
            with self.assertRaises((TypeError, ValueError, RecursionError)):
                json.dumps(value, allow_nan=False)
            for job_id in ("invalid", "existing"):
                before = self.snapshot()
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.q.put(job_id, value)
                self.assertEqual(self.snapshot(), before)
        for duration in (0, 0.0, -0.0, -1, -1e12, math.nan, math.inf, -math.inf):
            before = self.snapshot()
            with self.assertRaises(ValueError):
                self.q.claim("w", 1, duration)
            self.assertEqual(self.snapshot(), before)
        with self.assertRaises(ValueError):
            self.q.put("existing", "duplicate")
        self.assertTrue(self.q.ack(active["receipt"], 1))
        self.assertIsNone(self.q.claim("w", 1, 1))

    def test_nonempty_string_identifiers(self):
        identifiers = ["ordinary", "\x00", "中文", "\U0001f600", "\ud800",
                       "\udfff", "\ud83d\ude00", "prefix\ud800\x00suffix"]
        for index, identifier in enumerate(identifiers):
            with self.subTest(identifier=ascii(identifier)):
                now = index * 2
                self.q.put(identifier, identifier)
                with self.assertRaises(ValueError):
                    Queue(self.path).put(identifier, "duplicate")
                first = Queue(self.path).claim(identifier, now, 0.25)
                self.assertEqual(first["payload"], identifier)
                receipt = json.loads(json.dumps(first["receipt"], ensure_ascii=False))
                self.assertEqual(receipt, first["receipt"])
                self.assertIsNone(self.q.claim(identifier, now, 0.25))
                replacement = self.q.claim(identifier, now + 0.25, 0.5)
                before = self.snapshot()
                self.assertFalse(Queue(self.path).ack(receipt, now + 0.25))
                self.assertEqual(self.snapshot(), before)
                self.assertTrue(Queue(self.path).ack(replacement["receipt"], now + 0.5))
                self.assertFalse(self.q.ack(replacement["receipt"], now + 0.5))
                with self.assertRaises(ValueError):
                    self.q.put(identifier, "completed duplicate")

    def test_existing_database_compatibility(self):
        original_spec = importlib.util.spec_from_file_location("legacy", "original.py")
        legacy = importlib.util.module_from_spec(original_spec)
        original_spec.loader.exec_module(legacy)
        old = legacy.Queue(self.path)
        old.put("old", {"kept": True})
        receipt = old.claim("worker", 0, 5)["receipt"]
        self.assertTrue(Queue(self.path).ack(receipt, 1))
        self.q.put("new", [2])
        delivery = old.claim("worker", 1, 5)
        self.assertEqual(delivery["payload"], [2])
        self.assertTrue(self.q.ack(delivery["receipt"], 1))

    def test_numeric_expiry_arithmetic(self):
        for i, (now, duration) in enumerate([
            (-1e12, 1e12), (1e12, 1e12), (0, 5e-324),
            (1e12, 5e-324), (-0.25, 0.125), (0.1, 0.2),
        ]):
            self.q = Queue(pathlib.Path(self.directory.name) / f"numeric-{i}.db")
            self.q.put(str(i), i)
            delivery = self.q.claim("w", now, duration)
            expiry = now + duration
            self.assertEqual(delivery["expires_at"], expiry)
            if expiry > now:
                self.assertIsNone(self.q.claim("w", now, duration))
            self.assertFalse(self.q.ack(delivery["receipt"], expiry))
            replacement = self.q.claim("w", expiry, 1)
            self.assertTrue(self.q.ack(replacement["receipt"], expiry))

    def test_deep_supported_payload(self):
        payload = "\ud83d\ude00"
        for _ in range(1500):
            payload = [payload]
        self.q.put("deep", payload)
        delivery = Queue(self.path).claim("w", 0, 1)
        returned = delivery["payload"]
        for _ in range(1500):
            self.assertIsInstance(returned, list)
            self.assertEqual(len(returned), 1)
            returned = returned[0]
        self.assertEqual(returned, "\ud83d\ude00")
        self.assertTrue(self.q.ack(delivery["receipt"], 0))

    def test_atomic_claims_and_acks(self):
        self.q.put("one", {})
        queues = [Queue(self.path) for _ in range(8)]
        barrier = threading.Barrier(len(queues))
        def claim(q):
            barrier.wait()
            return q.claim("same", 0, 5)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, queues))
        deliveries = [r for r in results if r is not None]
        self.assertEqual(len(deliveries), 1)
        receipt = deliveries[0]["receipt"]
        def ack(q):
            barrier.wait()
            return q.ack(receipt, 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(ack, queues)), 1)

    def test_seeded_state_machine(self):
        rng = random.Random(3701)
        jobs, receipts = [], []
        now = 0
        for _ in range(500):
            now += rng.choice([0, 0.125, 0.5])
            operation = rng.choice(["put", "claim", "claim", "ack"])
            if operation == "put":
                job_id = str(len(jobs))
                payload = {"id": job_id, "nested": []}
                self.q.put(job_id, payload)
                jobs.append(dict(payload=payload, state="ready", expires=0, active=None))
            elif operation == "claim":
                candidate = next((j for j in jobs if j["state"] == "ready" or
                                  (j["state"] == "leased" and j["expires"] <= now)), None)
                duration = rng.choice([0.125, 0.5, 3])
                result = Queue(self.path).claim(rng.choice(["a", "b"]), now, duration)
                if candidate is None:
                    self.assertIsNone(result)
                else:
                    self.assertEqual(result["payload"], candidate["payload"])
                    self.assertEqual(result["expires_at"], now + duration)
                    candidate.update(state="leased", expires=now + duration, active=len(receipts))
                    receipts.append((result["receipt"], candidate))
                    result["payload"]["nested"].append("caller mutation")
            elif receipts:
                index = rng.randrange(len(receipts))
                receipt, job = receipts[index]
                expected = (job["state"] == "leased" and job["expires"] > now
                            and job["active"] == index)
                self.assertEqual(Queue(self.path).ack(receipt, now), expected)
                if expected:
                    job["state"] = "done"


if __name__ == "__main__":
    unittest.main(verbosity=2)
