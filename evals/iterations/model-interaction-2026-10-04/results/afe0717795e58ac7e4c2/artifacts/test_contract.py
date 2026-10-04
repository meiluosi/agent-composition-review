"""Business-contract tests; accepts app or original as its first argument."""
import copy
import importlib
import itertools
import sys
import unittest

module = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else "app")


def success(key="a", version=1, values=None):
    return {"call_id": key, "version": version, "status": "success",
            "values": [3] if values is None else values}


def error(key="a", version=1, message=""):
    return {"call_id": key, "version": version, "status": "error", "message": message}


class ContractTests(unittest.TestCase):
    def batch(self):
        return module.ToolBatch([{"call_id": "a", "weight": -2},
                                 {"call_id": "b", "weight": 0}])

    def test_score(self):
        for values in ([], [0], [-5, 2], [10**100, -3, 8]):
            for weight in (0, -7, 1, 10**100):
                with self.subTest(values=values, weight=weight):
                    before = values[:]
                    self.assertEqual(module.score(values, weight), sum(values) * weight)
                    self.assertEqual(values, before)

    def test_specs_validation(self):
        invalid = [None, {}, (), "a", [None], [{}],
                   [{"call_id": "", "weight": 1}],
                   [{"call_id": 1, "weight": 1}],
                   [{"call_id": [], "weight": 1}],
                   [{"call_id": "a"}],
                   [{"call_id": "a", "weight": 1, "extra": 0}],
                   [{"call_id": "a", "weight": 1}, {"call_id": "a", "weight": 2}]]
        invalid.extend([{"call_id": "a", "weight": w}]
                       for w in (True, False, 1.0, "1", None, []))
        for specs in invalid:
            with self.subTest(specs=specs):
                with self.assertRaises(ValueError):
                    module.ToolBatch(specs)
        for weight in (0, -1, 10**100):
            module.ToolBatch([{"call_id": "a", "weight": weight}])

    def test_stale_versions(self):
        batch = self.batch()
        batch.ingest([success(version=8)])
        expected = batch.snapshot()
        batch.ingest([error(version=2), success(version=1, values=[99])])
        self.assertEqual(batch.snapshot(), expected)

    def test_atomic_invalid(self):
        batch = self.batch()
        batch.ingest([error(version=1)])
        before = batch.snapshot()
        with self.assertRaises(ValueError):
            batch.ingest([success(version=2), success("b"), success("unknown")])
        self.assertEqual(batch.snapshot(), before)
        with self.assertRaises(ValueError):
            batch.finalize()
        batch.ingest([success("b")])
        self.assertEqual(batch.finalize()[0]["status"], "error")

    def test_atomic_conflict(self):
        batch = self.batch()
        before = batch.snapshot()
        with self.assertRaises(ValueError):
            batch.ingest([success(), success("b"), error()])
        self.assertEqual(batch.snapshot(), before)
        batch.ingest([error(), error("b")])
        self.assertEqual([r["status"] for r in batch.finalize()], ["error", "error"])

    def test_retry_and_replacement(self):
        batch = self.batch()
        batch.ingest([success(), success()])
        with self.assertRaises(ValueError):
            batch.ingest([success(values=[1, 2])])  # Equal score, different payload.
        with self.assertRaises(ValueError):
            batch.ingest([error()])
        batch.ingest([error(version=5), error(version=5)])
        batch.ingest([success(version=12, values=[]), error(version=5, message="old")])
        self.assertEqual(batch.snapshot()[0], {"call_id": "a", "version": 12,
                         "status": "success", "value": 0, "error": None})

    def test_updates_validation_including_stale(self):
        invalid = [None, [], "a", {}, success("unknown"), success(key=[]),
                   success(version=True), success(version=False), success(version=0),
                   success(version=-1), success(version=1.0), success(version="1"),
                   success(values=()), success(values=[True]), success(values=[1.0]),
                   success(values=[None]), error(message=None), error(message=[]),
                   dict(success(), extra=1), dict(error(), values=[]),
                   dict(success(), status="pending"), dict(success(), status=None)]
        for payload in (success(), error()):
            for field in payload:
                missing = payload.copy()
                del missing[field]
                invalid.append(missing)
        for item in invalid:
            with self.subTest(item=item):
                batch = self.batch()
                batch.ingest([success(version=10)])
                before = batch.snapshot()
                with self.assertRaises(ValueError):
                    batch.ingest([success("b"), item])
                self.assertEqual(batch.snapshot(), before)
        for updates in (None, {}, (), "", iter([])):
            with self.subTest(container=updates):
                with self.assertRaises(ValueError):
                    self.batch().ingest(updates)

    def test_order_pending_and_empty(self):
        batch = self.batch()
        expected = [{"call_id": key, "version": None, "status": "pending",
                     "value": None, "error": None} for key in ("a", "b")]
        self.assertEqual(batch.snapshot(), expected)
        batch.ingest([])
        self.assertEqual(batch.snapshot(), expected)
        batch.ingest([error("b", message="failed"), success(values=[-9, 2])])
        self.assertEqual(batch.snapshot(), [
            {"call_id": "a", "version": 1, "status": "success", "value": 14, "error": None},
            {"call_id": "b", "version": 1, "status": "error", "value": None, "error": "failed"}])
        empty = module.ToolBatch([])
        self.assertEqual(empty.finalize(), [])
        self.assertEqual(empty.finalize(), [])
        self.assertEqual(empty.snapshot(), [])
        with self.assertRaises(RuntimeError):
            empty.ingest([])

    def test_finalize_lifecycle(self):
        batch = self.batch()
        for _ in range(2):
            with self.assertRaises(ValueError):
                batch.finalize()
        batch.ingest([error()])
        with self.assertRaises(ValueError):
            batch.finalize()
        batch.ingest([success("b")])
        expected = batch.snapshot()
        self.assertEqual(batch.finalize(), expected)
        for updates in ([], [success(version=2)], None):
            with self.assertRaises(RuntimeError):
                batch.ingest(updates)
        self.assertEqual(batch.finalize(), expected)
        self.assertEqual(batch.snapshot(), expected)

    def test_input_output_isolation(self):
        specs = [{"call_id": "a", "weight": 2}]
        original_specs = copy.deepcopy(specs)
        batch = module.ToolBatch(specs)
        self.assertEqual(specs, original_specs)
        specs[0].update(call_id="changed", weight=99)
        specs.clear()
        updates = [success(values=[-1, 4])]
        original_updates = copy.deepcopy(updates)
        batch.ingest(updates)
        self.assertEqual(updates, original_updates)
        updates[0]["values"].append(90)
        updates[0]["version"] = 9
        updates.clear()
        expected = [{"call_id": "a", "version": 1, "status": "success", "value": 6, "error": None}]
        for read in (batch.snapshot, batch.finalize, batch.finalize, batch.snapshot):
            result = read()
            self.assertEqual(result, expected)
            result[0].update(call_id="changed", value=-100)
            result.append({})
            self.assertEqual(batch.snapshot(), expected)

    def test_exhaustive_order_and_batch_boundaries(self):
        # Independent reducer: validate payload conflicts against the current
        # maximum version, and commit only once an entire group is accepted.
        options = [success(version=1), error(version=1), success(version=3),
                   error(version=3), success("b", version=2)]
        for sequence in itertools.product(options, repeat=4):
            for split in range(5):
                batch = self.batch()
                state = {}
                for group in (sequence[:split], sequence[split:]):
                    candidate = copy.deepcopy(state)
                    conflict = False
                    for update in group:
                        key = update["call_id"]
                        old = candidate.get(key)
                        if old is None or update["version"] > old["version"]:
                            candidate[key] = update
                        elif update["version"] == old["version"] and update != old:
                            conflict = True
                            break
                    if conflict:
                        with self.assertRaises(ValueError):
                            batch.ingest(list(group))
                    else:
                        batch.ingest(list(group))
                        state = candidate
                    expected = []
                    for key, weight in (("a", -2), ("b", 0)):
                        item = state.get(key)
                        expected.append({"call_id": key,
                            "version": item["version"] if item else None,
                            "status": item["status"] if item else "pending",
                            "value": sum(item["values"]) * weight if item and item["status"] == "success" else None,
                            "error": item["message"] if item and item["status"] == "error" else None})
                    self.assertEqual(batch.snapshot(), expected, (sequence, split, group))


if __name__ == "__main__":
    unittest.main()
