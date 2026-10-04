"""Contract regression tests; accepts app.py or original.py as its first argument."""
import copy
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
from concurrent.futures import ThreadPoolExecutor


source = sys.argv.pop(1) if len(sys.argv) > 1 else "app.py"
spec = importlib.util.spec_from_file_location("queue_under_test", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Queue = module.Queue


class QueueContract(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=pathlib.Path.cwd())
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / "queue.db"
        self.q = Queue(self.path)

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT * FROM jobs ORDER BY seq").fetchall()

    def test_stale_receipt_same_worker(self):
        self.q.put("a", {"n": 1})
        first = self.q.claim("worker", 0, 1)
        second = Queue(self.path).claim("worker", 1, 10)
        before = self.snapshot()
        self.assertFalse(self.q.ack(first["receipt"], 1))
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(Queue(self.path).ack(second["receipt"], 2))
        self.assertFalse(self.q.ack(second["receipt"], 2))

    def test_old_receipt_after_worker_cycles(self):
        self.q.put("a", 1)
        first = self.q.claim("a", 0, 1)
        second = self.q.claim("b", 1, 1)
        third = self.q.claim("a", 2, 1)
        self.assertFalse(self.q.ack(first["receipt"], 2))
        self.assertFalse(self.q.ack(second["receipt"], 2))
        self.assertTrue(self.q.ack(third["receipt"], 2))

    def test_invalid_durations_leave_state_unchanged(self):
        self.q.put("a", [])
        for value in (True, False, 0, -0.0, -1, -0.5, math.inf, -math.inf, math.nan):
            with self.subTest(duration=value):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.q.claim("worker", 0, value)
                self.assertEqual(self.snapshot(), before)

    def test_fractional_expiry_and_fifo(self):
        self.q.put("first", 1)
        self.q.put("second", 2)
        first = self.q.claim("w", -1.5, 0.25)
        self.assertEqual(set(first), {"payload", "expires_at", "receipt"})
        self.assertEqual(first["expires_at"], -1.25)
        second = self.q.claim("w", -1.5, 10)
        self.assertEqual(second["payload"], 2)
        self.assertIsNone(self.q.claim("w", -1.3, 1))
        self.assertFalse(self.q.ack(first["receipt"], -1.25))
        retry = self.q.claim("other", -1.25, 0.5)
        self.assertEqual(retry["payload"], 1)
        self.assertTrue(self.q.ack(retry["receipt"], -1.0))
        self.assertTrue(self.q.ack(second["receipt"], -1.0))
        self.assertIsNone(self.q.claim("w", 100, 1))

    def test_python_expiry_arithmetic(self):
        cases = [(0, math.ulp(0.0)), (10**12, math.ulp(0.0)),
                 (-10**12, 0.125), (0.1, 0.2), (10**12, 10**12)]
        for index, (now, duration) in enumerate(cases):
            with self.subTest(now=now, duration=duration):
                q = Queue(pathlib.Path(self.directory.name) / f"time{index}.db")
                q.put("a", None)
                item = q.claim("w", now, duration)
                expiry = now + duration
                self.assertEqual(item["expires_at"], expiry)
                self.assertEqual(q.ack(item["receipt"], now), expiry > now)
                if expiry == now:
                    self.assertIsNotNone(q.claim("w", now, 1))

    def test_payload_roundtrip_and_isolation(self):
        payloads = [None, False, True, "", "中文\x00\ud800", 10**100, -7,
                    -0.0, 0.125, [], {}, [[], {}, [1]],
                    {"a": [{"b": [1, None, True]}], "": {}}]
        for i, payload in enumerate(payloads):
            self.q.put(str(i), payload)
        for i, payload in enumerate(payloads):
            item = Queue(self.path).claim("w", i, 1)
            self.assertEqual(item["payload"], payload)
            self.assertTrue(self.q.ack(item["receipt"], i))
        source_payload = {"a": [1, {"b": []}]}
        expected = copy.deepcopy(source_payload)
        self.q.put("isolated", source_payload)
        source_payload["a"][1]["b"].append(2)
        first = self.q.claim("w", 20, 1)
        first["payload"]["a"].append(3)
        saved_receipt = copy.deepcopy(first["receipt"])
        first["receipt"].clear()
        self.assertTrue(self.q.ack(saved_receipt, 20))
        self.q.put("isolated-again", expected)
        first = self.q.claim("w", 21, 1)
        first["payload"]["a"].clear()
        self.assertEqual(self.q.claim("w", 22, 1)["payload"], expected)

    def test_invalid_payloads_are_atomic(self):
        self.q.put("existing", {"saved": True})
        cycle = []
        cycle.append(cycle)
        deep = []
        # CPython's C JSON encoder has its own nesting limit, which need not
        # equal sys.getrecursionlimit(). Verify this fixture is rejected.
        for _ in range(10000):
            deep = [deep]
        with self.assertRaises(RecursionError):
            json.dumps(deep, allow_nan=False)
        invalid = [object(), {1, 2}, b"bytes", float("nan"),
                   float("inf"), {"nested": [-float("inf")]}, cycle, deep]
        for i, payload in enumerate(invalid):
            for job_id in ("existing", f"invalid{i}"):
                with self.subTest(job_id=job_id):
                    before = self.snapshot()
                    with self.assertRaises((TypeError, ValueError, RecursionError)):
                        self.q.put(job_id, payload)
                    self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.q.claim("w", 0, 1)["payload"], {"saved": True})

    def test_duplicates_and_reopen(self):
        self.q.put("a", [1])
        for phase in ("ready", "leased", "done"):
            with self.subTest(phase=phase):
                if phase == "leased":
                    item = Queue(self.path).claim("w", 0, 5)
                if phase == "done":
                    self.assertTrue(Queue(self.path).ack(item["receipt"], 1))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    Queue(self.path).put("a", [2])
                self.assertEqual(self.snapshot(), before)
        self.assertIsNone(Queue(self.path).claim("w", 100, 1))

    def test_string_identifiers(self):
        for index, identifier in enumerate(("normal", "中文", "nul\x00", "\ud800")):
            with self.subTest(identifier=repr(identifier)):
                self.q.put(identifier, index)
                item = self.q.claim(identifier, index, 1)
                self.assertEqual(item["payload"], index)
                self.assertTrue(Queue(self.path).ack(item["receipt"], index))
                with self.assertRaises(ValueError):
                    self.q.put(identifier, index)
        self.q.put("surrogate-worker", "payload")
        old = self.q.claim("\udfff", 4, 1)
        current = Queue(self.path).claim("\udfff", 5, 1)
        self.assertFalse(self.q.ack(old["receipt"], 5))
        self.assertTrue(self.q.ack(current["receipt"], 5))

    def test_existing_original_database_and_receipts(self):
        import original
        old = original.Queue(self.path)
        old.put("completed", 0)
        finished = old.claim("w", 0, 1)
        self.assertTrue(old.ack(finished["receipt"], 0))
        old.put("active", 1)
        active = old.claim("w", 0, 1)
        old.put("ready", 2)
        reopened = Queue(self.path)
        self.assertTrue(reopened.ack(active["receipt"], 0))
        self.assertEqual(reopened.claim("w", 1, 1)["payload"], 2)
        with self.assertRaises(ValueError):
            reopened.put("completed", 3)

    def test_separate_connections_claim_and_ack_atomically(self):
        self.q.put("a", [])
        queues = [Queue(self.path) for _ in range(8)]
        barrier = threading.Barrier(len(queues))
        def claim(q):
            barrier.wait()
            return q.claim("same-worker", 0, 1)
        with ThreadPoolExecutor(len(queues)) as pool:
            results = list(pool.map(claim, queues))
        winners = [item for item in results if item is not None]
        self.assertEqual(len(winners), 1)
        receipt = winners[0]["receipt"]
        def ack(q):
            barrier.wait()
            return q.ack(receipt, 0)
        with ThreadPoolExecutor(len(queues)) as pool:
            acknowledgments = list(pool.map(ack, queues))
        self.assertEqual(sum(acknowledgments), 1)

    def test_concurrent_reclaims_at_expiry(self):
        self.q.put("a", "value")
        old = self.q.claim("same-worker", 0, 0.5)
        queues = [Queue(self.path) for _ in range(8)]
        barrier = threading.Barrier(len(queues))
        def reclaim(q):
            barrier.wait()
            return q.claim("same-worker", 0.5, 0.5)
        with ThreadPoolExecutor(len(queues)) as pool:
            results = list(pool.map(reclaim, queues))
        winners = [item for item in results if item is not None]
        self.assertEqual(len(winners), 1)
        self.assertFalse(self.q.ack(old["receipt"], 0.5))
        self.assertTrue(self.q.ack(winners[0]["receipt"], 0.5))

    def test_randomized_reference_model(self):
        rng = random.Random(4921)
        jobs = {}
        receipts = []
        now = -10.0
        for _ in range(600):
            now += rng.choice((0, 0.125, 1))
            action = rng.choice(("put", "claim", "claim", "ack"))
            q = Queue(self.path)
            if action == "put":
                job_id = str(rng.randrange(60))
                if job_id in jobs:
                    with self.assertRaises(ValueError):
                        q.put(job_id, {"id": job_id})
                else:
                    q.put(job_id, {"id": job_id})
                    jobs[job_id] = {"state": "ready", "generation": 0}
            elif action == "claim":
                worker = rng.choice(("a", "b"))
                duration = rng.choice((0.125, 1, 5))
                eligible = next((job_id for job_id, job in jobs.items()
                                 if job["state"] == "ready" or
                                 (job["state"] == "leased" and job["expiry"] <= now)), None)
                item = q.claim(worker, now, duration)
                if eligible is None:
                    self.assertIsNone(item)
                else:
                    job = jobs[eligible]
                    job.update(state="leased", worker=worker, expiry=now + duration,
                               generation=job["generation"] + 1)
                    self.assertEqual(item["payload"], {"id": eligible})
                    self.assertEqual(item["expires_at"], now + duration)
                    receipts.append((eligible, job["generation"], item["receipt"]))
            elif receipts:
                job_id, generation, receipt = rng.choice(receipts)
                job = jobs[job_id]
                expected = (job["state"] == "leased" and
                            job["generation"] == generation and job["expiry"] > now)
                self.assertEqual(q.ack(receipt, now), expected)
                if expected:
                    job["state"] = "done"


if __name__ == "__main__":
    unittest.main()
