"""Local contract checks; optionally pass original to check the original copy."""
import copy
import importlib
import itertools
import sys
import unittest


module_name = sys.argv.pop(1) if len(sys.argv) > 1 else "app"
app = importlib.import_module(module_name)


def success(call_id="a", version=1, values=None):
    return dict(call_id=call_id, version=version, status="success",
                values=[3] if values is None else values)


def error(call_id="a", version=1, message=""):
    return dict(call_id=call_id, version=version, status="error", message=message)


class ContractTests(unittest.TestCase):
    def batch(self):
        return app.ToolBatch([dict(call_id="a", weight=2),
                              dict(call_id="b", weight=-3)])

    def test_signed_score(self):
        for values in ([], [0], [-3, 2], [-7, -4], [10**100, -10**99]):
            for weight in (0, 1, -2, 10**100):
                with self.subTest(values=values, weight=weight):
                    before = values[:]
                    self.assertEqual(app.score(values, weight), sum(values) * weight)
                    self.assertEqual(values, before)

    def test_spec_validation(self):
        bad = [None, {}, (), "", [None], [{}],
               [dict(call_id="", weight=1)], [dict(call_id=1, weight=1)],
               [dict(call_id="a", weight=True)], [dict(call_id="a", weight=1.0)],
               [dict(call_id="a", weight="1")], [dict(call_id="a")],
               [dict(call_id="a", weight=1, extra=0)],
               [dict(call_id="a", weight=1), dict(call_id="a", weight=2)]]
        for specs in bad:
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                app.ToolBatch(specs)
        for weight in (0, -1, 10**100):
            app.ToolBatch([dict(call_id="a", weight=weight)])

    def test_snapshot_order_and_shape(self):
        batch = self.batch()
        pending = [dict(call_id=c, version=None, status="pending", value=None,
                        error=None) for c in ("a", "b")]
        self.assertEqual(batch.snapshot(), pending)
        batch.ingest([error("b", 7, "failed"), success("a", 4, [-5, 2])])
        self.assertEqual(batch.snapshot(), [
            dict(call_id="a", version=4, status="success", value=-6, error=None),
            dict(call_id="b", version=7, status="error", value=None, error="failed")])

    def test_update_validation_and_atomicity(self):
        bad = [None, [], {}, dict(call_id="unknown", version=1, status="error", message="")]
        for valid in (success(), error()):
            for field in valid:
                missing = valid.copy()
                del missing[field]
                bad.append(missing)
            bad.append(dict(valid, extra=1))
            for field, values in {
                "call_id": [None, [], 1, ""],
                "version": [None, False, True, 0, -1, 1.5, "1"],
                "status": [None, [], {}, 1, "pending", "SUCCESS"],
            }.items():
                bad.extend(dict(valid, **{field: value}) for value in values)
        bad.extend(dict(success(), values=v)
                   for v in (None, (), "", {}, [True], [False], [1.0], ["1"], [[]]))
        bad.extend(dict(error(), message=v) for v in (None, [], {}, 1, True))
        bad.extend([dict(success(), message=""), dict(error(), values=[])])
        for item in bad:
            with self.subTest(item=item):
                batch = self.batch()
                batch.ingest([success("a", 10)])
                before = batch.snapshot()
                with self.assertRaises(ValueError):
                    batch.ingest([success("b"), item])
                self.assertEqual(batch.snapshot(), before)
                batch.ingest([error("b")])
                batch.finalize()
        for updates in (None, {}, (), "", iter([])):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.batch().ingest(updates)

    def test_version_transitions(self):
        batch = self.batch()
        batch.ingest([success("a", 1), error("a", 10, "failed"),
                      success("a", 2, [99]), error("a", 10, "failed")])
        self.assertEqual(batch.snapshot()[0]["error"], "failed")
        batch.ingest([success("a", 100, []), success("a", 1, [123]),
                      error("b", 50)])
        self.assertEqual(batch.finalize(), [
            dict(call_id="a", version=100, status="success", value=0, error=None),
            dict(call_id="b", version=50, status="error", value=None, error="")])

    def test_current_conflicts_rollback(self):
        pairs = [(success(values=[1, 2]), success(values=[2, 1])),
                 (success(values=[1, 2]), success(values=[3])),
                 (success(), error()), (error(message="a"), error(message="b"))]
        for first, second in pairs:
            for same_ingest in (False, True):
                with self.subTest(first=first, second=second, same_ingest=same_ingest):
                    batch = self.batch()
                    if not same_ingest:
                        batch.ingest([first])
                    before = batch.snapshot()
                    with self.assertRaises(ValueError):
                        batch.ingest([success("b"), first, second])
                    self.assertEqual(batch.snapshot(), before)
                    batch.ingest([success("a", 2), error("b")])
                    batch.finalize()

    def test_list_order_controls_current_conflicts(self):
        batch = self.batch()
        batch.ingest([success("a", 5)])
        before = batch.snapshot()
        with self.assertRaises(ValueError):
            batch.ingest([error("a", 5), success("a", 6)])
        self.assertEqual(batch.snapshot(), before)
        batch.ingest([success("a", 6), error("a", 5)])
        self.assertEqual(batch.snapshot()[0]["version"], 6)

    def test_arbitrary_completion_order(self):
        updates = [success("a", 1, [-4]), error("a", 3, "retry"),
                   success("a", 20, [-7, 2]), error("b", 5)]
        expected = [
            dict(call_id="a", version=20, status="success", value=-10, error=None),
            dict(call_id="b", version=5, status="error", value=None, error="")]
        for permutation in itertools.permutations(updates):
            for split in range(5):
                batch = self.batch()
                batch.ingest(list(permutation[:split]))
                batch.ingest(list(permutation[split:]))
                self.assertEqual(batch.finalize(), expected)

    def test_idempotence(self):
        for item in (success(values=[]), success(values=[-1, 2]), error(message="")):
            batch = self.batch()
            batch.ingest([item, copy.deepcopy(item)])
            before = batch.snapshot()
            batch.ingest([copy.deepcopy(item)])
            self.assertEqual(batch.snapshot(), before)

    def test_finalization_lifecycle(self):
        batch = self.batch()
        before = batch.snapshot()
        batch.ingest([])
        with self.assertRaises(ValueError):
            batch.finalize()
        self.assertEqual(batch.snapshot(), before)
        batch.ingest([error("b")])
        with self.assertRaises(ValueError):
            batch.finalize()
        batch.ingest([success("a")])
        result = batch.finalize()
        self.assertEqual(batch.finalize(), result)
        self.assertEqual(batch.snapshot(), result)
        for updates in ([], [success()], None):
            with self.assertRaises(RuntimeError):
                batch.ingest(updates)
        self.assertEqual(batch.snapshot(), result)

    def test_empty_batch(self):
        batch = app.ToolBatch([])
        batch.ingest([])
        self.assertEqual(batch.snapshot(), [])
        self.assertEqual(batch.finalize(), [])
        self.assertEqual(batch.finalize(), [])
        with self.assertRaises(RuntimeError):
            batch.ingest([])

    def test_no_shared_mutable_state(self):
        specs = [dict(call_id="a", weight=2)]
        original_specs = copy.deepcopy(specs)
        batch = app.ToolBatch(specs)
        self.assertEqual(specs, original_specs)
        specs[0].update(call_id="changed", weight=99)
        specs.clear()
        item = success(values=[2, 4])
        updates = [item]
        original_updates = copy.deepcopy(updates)
        batch.ingest(updates)
        self.assertEqual(updates, original_updates)
        item["values"].append(99)
        item.update(call_id="changed", version=99)
        updates.clear()
        expected = [dict(call_id="a", version=1, status="success", value=12, error=None)]
        self.assertEqual(batch.snapshot(), expected)
        for read in (batch.snapshot, batch.finalize, batch.finalize, batch.snapshot):
            first, second = read(), read()
            self.assertIsNot(first, second)
            self.assertIsNot(first[0], second[0])
            first[0].update(value=999, error="changed", status="error")
            first.clear()
            self.assertEqual(second, expected)
            self.assertEqual(batch.snapshot(), expected)


if __name__ == "__main__":
    unittest.main()
