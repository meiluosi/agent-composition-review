"""Local contract tests; run with ./run_python test_contract.py [app.py]."""
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


target = pathlib.Path(sys.argv.pop(1) if len(sys.argv) > 1 else "app.py")
spec = importlib.util.spec_from_file_location("queue_under_test", target)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QueueContract(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / "queue.db"
        self.queue = module.Queue(self.path)

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT * FROM jobs ORDER BY seq").fetchall()

    def test_fractional_lease_blocks_claim_until_exact_expiry(self):
        self.queue.put("one", {"items": []})
        claim = self.queue.claim("worker", 10.25, 0.5)
        self.assertEqual(set(claim), {"payload", "expires_at", "receipt"})
        self.assertEqual(claim["expires_at"], 10.75)
        self.assertIsNone(module.Queue(self.path).claim("worker", 10.5, 1))
        self.assertFalse(self.queue.ack(claim["receipt"], 10.75))
        replacement = self.queue.claim("worker", 10.75, 1.25)
        self.assertNotEqual(claim["receipt"], replacement["receipt"])
        self.assertFalse(self.queue.ack(claim["receipt"], 10.8))
        self.assertTrue(module.Queue(self.path).ack(replacement["receipt"], 11.9))
        self.assertFalse(self.queue.ack(replacement["receipt"], 11.9))

    def test_fractional_ack_before_expiry(self):
        self.queue.put("one", None)
        claim = self.queue.claim("worker", 0, 1.75)
        self.assertTrue(self.queue.ack(claim["receipt"], 1.5))

    def test_python_expiry_arithmetic(self):
        cases = [(0, 0.1), (-10.5, 1.75), (-10**12, 10**12),
                 (10**12, 10**12), (0, math.ulp(0.0)),
                 (10**12, math.ulp(0.0)), (0.1, 0.2)]
        for index, (now, duration) in enumerate(cases):
            with self.subTest(now=now, duration=duration):
                queue = module.Queue(pathlib.Path(self.directory.name) / f"{index}.db")
                queue.put("one", index)
                claim = queue.claim("w", now, duration)
                self.assertEqual(claim["expires_at"], now + duration)
                self.assertEqual(queue.ack(claim["receipt"], now), now < now + duration)

    def test_invalid_duration_leaves_state_unchanged(self):
        self.queue.put("one", [])
        self.queue.put("two", {})
        self.queue.claim("w", 0, 10)
        before = self.snapshot()
        for duration in [0, 0.0, -0.0, -1, -0.25, float("inf"),
                         float("-inf"), float("nan"), True, False]:
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    self.queue.claim("other", 1, duration)
                self.assertEqual(self.snapshot(), before)

    def test_fifo_expired_jobs_keep_enqueue_order(self):
        for job in ["a", "b", "c"]:
            self.queue.put(job, job)
        a = self.queue.claim("w", 0, 3)
        b = self.queue.claim("w", 1, 10)
        self.assertEqual([a["payload"], b["payload"]], ["a", "b"])
        a2 = self.queue.claim("w", 3, 5)
        self.assertEqual(a2["payload"], "a")
        self.assertFalse(self.queue.ack(a["receipt"], 3))
        self.assertTrue(self.queue.ack(a2["receipt"], 3))
        self.assertEqual(self.queue.claim("v", 3, 5)["payload"], "c")
        self.assertIsNone(self.queue.claim("w", 3, 5))

    def test_reopening_and_duplicate_ids_in_all_states(self):
        self.queue.put("one", {"x": 1})
        for state in ["ready", "leased", "done"]:
            queue = module.Queue(self.path)
            before = self.snapshot()
            with self.assertRaises(ValueError):
                queue.put("one", {"x": 2})
            self.assertEqual(self.snapshot(), before)
            if state == "ready":
                claim = queue.claim("w", 0, 10)
                self.assertEqual(claim["payload"], {"x": 1})
            elif state == "leased":
                self.assertTrue(queue.ack(claim["receipt"], 1))
        self.assertIsNone(module.Queue(self.path).claim("w", 2, 10))

    def test_payload_roundtrip_and_no_aliasing(self):
        values = [None, True, False, "", "中文\u0000\ud800", 10**100,
                  -1, 0.0, -0.0, 1.5, [], {}, {"": [[], {}, None]}]
        for index, value in enumerate(values):
            self.queue.put(str(index), value)
        for value in values:
            claim = self.queue.claim("w", 0, 10)
            self.assertEqual(claim["payload"], value)
            self.assertTrue(self.queue.ack(claim["receipt"], 0))
        payload = {"nested": [{"x": [1]}]}
        self.queue.put("mutable", payload)
        payload["nested"][0]["x"].append(2)
        claim = self.queue.claim("w", 1, 1)
        claim["payload"]["nested"][0]["x"].append(3)
        replacement = self.queue.claim("w", 2, 10)
        self.assertEqual(replacement["payload"], {"nested": [{"x": [1]}]})
        self.assertFalse(self.queue.ack(claim["receipt"], 2))
        self.assertTrue(self.queue.ack(replacement["receipt"], 2))

    def test_encoder_rejections_are_atomic(self):
        self.queue.put("existing", {"keep": []})
        active = self.queue.claim("w", 0, 10)
        before = self.snapshot()
        cycle = []
        cycle.append(cycle)
        deep = []
        for _ in range(10000):
            deep = [deep]
        # The C JSON encoder's depth limit is not sys.getrecursionlimit().
        with self.assertRaises((TypeError, ValueError, RecursionError)):
            json.dumps(deep, allow_nan=False)
        invalid = [object(), b"bytes", {1, 2}, complex(1, 2), cycle, deep,
                   float("nan"), {"x": [float("inf")]}, float("-inf")]
        for index, payload in enumerate(invalid):
            with self.subTest(index=index):
                with self.assertRaises((TypeError, ValueError, RecursionError)):
                    self.queue.put(f"invalid-{index}", payload)
                self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.queue.ack(active["receipt"], 1))
        self.assertIsNone(self.queue.claim("w", 1, 10))

    def test_parallel_claim_and_ack(self):
        self.queue.put("one", {})
        count = 8
        queues = [module.Queue(self.path) for _ in range(count)]
        barrier = threading.Barrier(count)

        def claim(index):
            barrier.wait(timeout=10)
            return queues[index].claim("same-worker", 0, 10)

        with ThreadPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(claim, range(count)))
        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1)
        receipt = winners[0]["receipt"]

        def ack(index):
            barrier.wait(timeout=10)
            return queues[index].ack(receipt, 1)

        with ThreadPoolExecutor(max_workers=count) as pool:
            self.assertEqual(sum(pool.map(ack, range(count))), 1)

    def test_generated_sequences_against_reference_state_machine(self):
        rng = random.Random(62471)
        model = []
        receipts = []
        now = -10.0
        for step in range(500):
            now += rng.choice([0, 0.125, 0.5, 1])
            action = rng.choice(["put", "claim", "claim", "ack", "reopen"])
            if action == "put":
                value = {"step": step, "data": [None, []]}
                self.queue.put(str(step), value)
                model.append({"value": value, "state": "ready", "expiry": None,
                              "generation": 0})
            elif action == "claim":
                duration = rng.choice([0.125, 0.75, 1, 2.5, 10])
                expected = next((job for job in model if job["state"] == "ready"
                                 or (job["state"] == "leased" and job["expiry"] <= now)), None)
                result = self.queue.claim(rng.choice(["a", "b"]), now, duration)
                if expected is None:
                    self.assertIsNone(result)
                else:
                    self.assertIsNotNone(result)
                    self.assertEqual(result["payload"], expected["value"])
                    self.assertEqual(result["expires_at"], now + duration)
                    expected.update(state="leased", expiry=now + duration,
                                    generation=expected["generation"] + 1)
                    receipts.append((result["receipt"], expected, expected["generation"]))
            elif action == "ack" and receipts:
                receipt, job, generation = rng.choice(receipts)
                expected = (job["state"] == "leased" and job["expiry"] > now
                            and job["generation"] == generation)
                self.assertEqual(self.queue.ack(receipt, now), expected)
                if expected:
                    job["state"] = "done"
            elif action == "reopen":
                self.queue = module.Queue(self.path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
