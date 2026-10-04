"""Contract checks runnable against app.py or its original copy."""
import concurrent.futures
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import unittest


MODULE = sys.argv.pop(1) if len(sys.argv) > 1 else "app.py"
spec = importlib.util.spec_from_file_location("queue_under_test", MODULE)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class QueueContract(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "queue.db"
        self.queue = app.Queue(self.path)

    def test_fractional_lease_and_exact_boundary(self):
        self.queue.put("job", {"value": 1})
        delivery = self.queue.claim("worker", -0.25, 0.5)
        self.assertEqual(set(delivery), {"payload", "expires_at", "receipt"})
        self.assertEqual(delivery["expires_at"], 0.25)
        self.assertIsNone(self.queue.claim("worker", 0, 1))
        self.assertIsNone(self.queue.claim("other", math.nextafter(0.25, -math.inf), 1))
        self.assertFalse(self.queue.ack(delivery["receipt"], 0.25))
        replacement = self.queue.claim("worker", 0.25, 1.75)
        self.assertEqual(replacement["expires_at"], 2)
        self.assertFalse(self.queue.ack(delivery["receipt"], 0.25))
        self.assertTrue(app.Queue(self.path).ack(replacement["receipt"], 1.9))
        self.assertFalse(self.queue.ack(replacement["receipt"], 1.9))
        self.assertIsNone(self.queue.claim("worker", 10, 1))

    def test_fractional_ack_before_expiry(self):
        self.queue.put("job", None)
        delivery = self.queue.claim("worker", 10.125, 1.75)
        self.assertEqual(delivery["expires_at"], 11.875)
        self.assertTrue(self.queue.ack(delivery["receipt"], 11.5))

    def test_numeric_arithmetic(self):
        for index, (now, duration) in enumerate([
            (-10**12, 10**12), (10**12, 10**12), (0, 5e-324),
            (10**12, 5e-324), (0.1, 0.2), (-1.25, 0.125),
        ]):
            with self.subTest(now=now, duration=duration):
                queue = app.Queue(Path(self.directory.name) / f"numeric-{index}.db")
                queue.put("job", [])
                delivery = queue.claim("worker", now, duration)
                expiry = now + duration
                self.assertEqual(delivery["expires_at"], expiry)
                self.assertFalse(queue.ack(delivery["receipt"], expiry))
                self.assertIsNotNone(queue.claim("worker", expiry, 1))

    def test_fifo_reopen_and_duplicate_after_completion(self):
        for identifier in ["z", "a", "m"]:
            self.queue.put(identifier, identifier)
        first = self.queue.claim("one", 0, 2)
        reopened = app.Queue(self.path)
        second = reopened.claim("two", 0, 10)
        self.assertEqual(first["payload"], "z")
        self.assertEqual(second["payload"], "a")
        with self.assertRaises(ValueError):
            reopened.put("z", "overwrite")
        reclaimed = reopened.claim("one", 2, 10)
        self.assertEqual(reclaimed["payload"], "z")
        self.assertFalse(self.queue.ack(first["receipt"], 2))
        self.assertTrue(self.queue.ack(reclaimed["receipt"], 2))
        with self.assertRaises(ValueError):
            reopened.put("z", "reinsert")
        self.assertTrue(reopened.ack(second["receipt"], 2))
        third = reopened.claim("three", 2, 10)
        self.assertEqual(third["payload"], "m")
        self.assertTrue(reopened.ack(third["receipt"], 2))
        self.assertIsNone(app.Queue(self.path).claim("one", 100, 1))

    def test_json_values_and_snapshot(self):
        values = [None, True, False, "中文\u0000", 123, -1.25, 5e-324,
                  [], {}, [[], {}, {"nested": [None, True, 7]}]]
        for index, value in enumerate(values):
            self.queue.put(str(index), value)
        for value in values:
            delivery = self.queue.claim("worker", 0, 1)
            self.assertEqual(delivery["payload"], value)
            self.assertTrue(self.queue.ack(delivery["receipt"], 0))
        payload = {"items": [{"value": 1}]}
        self.queue.put("snapshot", payload)
        payload["items"][0]["value"] = 2
        first = self.queue.claim("worker", 0, 1)
        self.assertEqual(first["payload"], {"items": [{"value": 1}]})
        first["payload"]["items"].clear()
        second = app.Queue(self.path).claim("worker", 1, 1)
        self.assertEqual(second["payload"], {"items": [{"value": 1}]})
        self.assertIsNot(first["receipt"], second["receipt"])
        self.assertNotEqual(first["receipt"], second["receipt"])
        first["receipt"].clear()
        self.assertTrue(self.queue.ack(second["receipt"], 1))

    def test_rejected_payloads_leave_no_changes(self):
        circular = []
        circular.append(circular)
        deep = None
        for _ in range(10000):
            deep = [deep]
        # The C encoder's nesting limit need not equal Python's recursion limit.
        with self.assertRaises((ValueError, RecursionError)):
            json.dumps(deep, allow_nan=False)
        invalid = [object(), {1, 2}, b"bytes", float("nan"), float("inf"),
                   -float("inf"), [float("nan")], {"bad": object()}, circular, deep]
        self.queue.put("existing", {"safe": True})
        active = self.queue.claim("worker", 0, 10)
        for index, payload in enumerate(invalid):
            for identifier in (f"invalid-{index}", "existing"):
                with self.subTest(index=index, identifier=identifier):
                    with self.assertRaises((TypeError, ValueError, RecursionError)):
                        self.queue.put(identifier, payload)
            self.assertIsNone(self.queue.claim("worker", 0, 10))
        for index in range(len(invalid)):
            self.queue.put(f"invalid-{index}", index)
        self.assertTrue(self.queue.ack(active["receipt"], 0))
        for index in range(len(invalid)):
            delivery = self.queue.claim("worker", 0, 1)
            self.assertEqual(delivery["payload"], index)
            self.assertTrue(self.queue.ack(delivery["receipt"], 0))
        self.assertIsNone(self.queue.claim("worker", 0, 1))

    def test_invalid_durations_do_not_change_claims(self):
        invalid = [0, 0.0, -0.0, -1, -0.125, float("nan"), float("inf"), -float("inf")]
        for duration in invalid:
            with self.assertRaises(ValueError):
                self.queue.claim("worker", 0, duration)
        self.queue.put("first", 1)
        self.queue.put("second", 2)
        for duration in invalid:
            with self.assertRaises(ValueError):
                self.queue.claim("worker", 0, duration)
        first = self.queue.claim("worker", 0, 1)
        self.assertEqual(first["payload"], 1)
        for duration in invalid:
            with self.assertRaises(ValueError):
                self.queue.claim("worker", 0, duration)
        self.assertTrue(self.queue.ack(first["receipt"], 0))
        self.assertEqual(self.queue.claim("worker", 0, 1)["payload"], 2)

    def race(self, operation, count=8):
        queues = [app.Queue(self.path) for _ in range(count)]
        barrier = threading.Barrier(count)
        def run(queue):
            barrier.wait(timeout=10)
            return operation(queue)
        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(run, queues))

    def test_atomic_claim_reclaim_and_ack(self):
        self.queue.put("job", 1)
        claims = self.race(lambda queue: queue.claim("same-worker", 0, 1))
        successful = [delivery for delivery in claims if delivery is not None]
        self.assertEqual(len(successful), 1)
        old_receipt = successful[0]["receipt"]
        claims = self.race(lambda queue: queue.claim("same-worker", 1, 1))
        successful = [delivery for delivery in claims if delivery is not None]
        self.assertEqual(len(successful), 1)
        self.assertFalse(self.queue.ack(old_receipt, 1))
        acknowledgements = self.race(lambda queue: queue.ack(successful[0]["receipt"], 1))
        self.assertEqual(acknowledgements.count(True), 1)

    def test_atomic_duplicate_put_and_multiple_jobs(self):
        def put(queue):
            try:
                queue.put("duplicate", "duplicate")
                return True
            except ValueError:
                return False
        self.assertEqual(self.race(put).count(True), 1)
        for index in range(7):
            self.queue.put(str(index), index)
        deliveries = self.race(lambda queue: queue.claim("worker", 0, 1))
        self.assertEqual({delivery["payload"] for delivery in deliveries},
                         {"duplicate", *range(7)})


if __name__ == "__main__":
    unittest.main()
