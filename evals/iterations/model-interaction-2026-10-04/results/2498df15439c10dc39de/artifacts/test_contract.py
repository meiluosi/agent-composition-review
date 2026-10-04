"""Local behavioral checks for task.md; optional argument selects the module."""
import copy
import importlib
import itertools
import sys
import unittest

implementation = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else "app")


def success(call="a", version=1, values=None):
    return dict(call_id=call, version=version, status="success",
                values=[3] if values is None else values)


def error(call="a", version=1, message=""):
    return dict(call_id=call, version=version, status="error", message=message)


class ContractTests(unittest.TestCase):
    def batch(self):
        return implementation.ToolBatch([
            dict(call_id="a", weight=-2), dict(call_id="b", weight=0)])

    def test_score(self):
        for values, weight, expected in [([], -8, 0), ([3, -7, 2], -4, 8),
                                         ([10**100, -1], 10**50,
                                          (10**100 - 1) * 10**50)]:
            self.assertEqual(implementation.score(values, weight), expected)

    def test_specs_validation(self):
        invalid = [None, {}, (), [None], [{}],
                   [dict(call_id="", weight=1)], [dict(call_id=3, weight=1)],
                   [dict(call_id="a", weight=True)], [dict(call_id="a", weight=1.0)],
                   [dict(call_id="a", weight=1, extra=2)],
                   [dict(call_id="a", weight=1), dict(call_id="a", weight=2)]]
        for specs in invalid:
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                implementation.ToolBatch(specs)

    def test_pending_order_and_success_error(self):
        batch = self.batch()
        self.assertEqual(batch.snapshot(), [
            dict(call_id=c, version=None, status="pending", value=None, error=None)
            for c in ["a", "b"]])
        batch.ingest([error("b", 7, "failed"), success(values=[4, -9])])
        self.assertEqual(batch.snapshot(), [
            dict(call_id="a", version=1, status="success", value=10, error=None),
            dict(call_id="b", version=7, status="error", value=None, error="failed")])

    def test_stale_and_old_conflict(self):
        batch = self.batch()
        batch.ingest([success(version=2)])
        expected = batch.snapshot()
        batch.ingest([error(version=1), success(version=1, values=[8])])
        self.assertEqual(batch.snapshot(), expected)

    def test_replacements_and_idempotence(self):
        batch = self.batch()
        for update in [error(), success(version=4, values=[]), error(version=9)]:
            batch.ingest([update])
            expected = batch.snapshot()
            batch.ingest([copy.deepcopy(update)])
            self.assertEqual(batch.snapshot(), expected)
        self.assertEqual(batch.snapshot()[0], dict(
            call_id="a", version=9, status="error", value=None, error=""))

    def test_conflicts_compare_payload_not_score(self):
        for first, second in [(success(values=[1, 2]), success(values=[3])),
                              (success(), error()),
                              (error(message="one"), error(message="two"))]:
            batch = self.batch()
            batch.ingest([first])
            before = batch.snapshot()
            with self.assertRaises(ValueError):
                batch.ingest([second])
            self.assertEqual(batch.snapshot(), before)

    def test_atomic_invalid_and_conflict(self):
        for last in [dict(call_id="unknown"), success(version=1, values=[999])]:
            batch = self.batch()
            batch.ingest([success()])
            before = batch.snapshot()
            with self.assertRaises(ValueError):
                batch.ingest([error("b"), last])
            self.assertEqual(batch.snapshot(), before)
            with self.assertRaises(ValueError):
                batch.finalize()
            batch.ingest([error("b")])
            batch.finalize()

    def test_atomic_conflict_within_new_batch(self):
        batch = self.batch()
        before = batch.snapshot()
        with self.assertRaises(ValueError):
            batch.ingest([success(version=7), error(version=7)])
        self.assertEqual(batch.snapshot(), before)

    def test_update_validation_including_stale(self):
        invalid = [None, [], {}, success(call="unknown"), success(call=[]),
                   success(version=True), success(version=0), success(version=-1),
                   success(version=1.0), success(values=[True]), success(values=[1.0]),
                   success(values=[[]]), success(values=()), error(message=None),
                   dict(success(), status="pending"), dict(success(), extra=1),
                   dict(error(), values=[])]
        for update in [success(), error()]:
            invalid.extend({k: v for k, v in update.items() if k != missing}
                           for missing in update)
        for update in invalid:
            with self.subTest(update=update):
                batch = self.batch()
                batch.ingest([success(version=10)])
                before = batch.snapshot()
                with self.assertRaises(ValueError):
                    batch.ingest([error("b"), update])
                self.assertEqual(batch.snapshot(), before)
        for updates in [None, {}, (), ""]:
            with self.assertRaises(ValueError):
                self.batch().ingest(updates)

    def test_finalize_lifecycle(self):
        batch = self.batch()
        batch.ingest([])
        with self.assertRaises(ValueError):
            batch.finalize()
        batch.ingest([success(), error("b")])
        expected = batch.snapshot()
        result = batch.finalize()
        self.assertEqual(result, expected)
        result[0]["value"] = 999
        result.clear()
        self.assertEqual(batch.finalize(), expected)
        self.assertEqual(batch.snapshot(), expected)
        for updates in [[], [success(version=2)], None]:
            with self.assertRaises(RuntimeError):
                batch.ingest(updates)

    def test_empty_batch(self):
        batch = implementation.ToolBatch([])
        batch.ingest([])
        first = batch.finalize()
        self.assertEqual(first, [])
        self.assertIsNot(first, batch.finalize())
        self.assertIsNot(first, batch.snapshot())
        with self.assertRaises(RuntimeError):
            batch.ingest([])

    def test_input_and_output_isolation(self):
        specs = [dict(call_id="a", weight=2)]
        batch = implementation.ToolBatch(specs)
        specs[0]["weight"] = 999
        specs[0]["call_id"] = "changed"
        specs.clear()
        updates = [success(values=[1, -4])]
        original_updates = copy.deepcopy(updates)
        batch.ingest(updates)
        self.assertEqual(updates, original_updates)
        updates[0]["values"].append(999)
        updates[0]["version"] = 99
        updates.clear()
        expected = [dict(call_id="a", version=1, status="success", value=-6, error=None)]
        result = batch.snapshot()
        result[0]["call_id"] = "changed"
        result.append({})
        self.assertEqual(batch.snapshot(), expected)
        self.assertEqual(batch.finalize(), expected)

    def test_exhaustive_ordered_transitions(self):
        # Independent history oracle: compare only payloads at the maximum
        # version in the accepted prefix, discarding the whole trial on failure.
        choices = [success(version=1), error(version=1),
                   success(version=3, values=[]), error(version=3), error("b", 2)]
        for sequence in itertools.product(choices, repeat=4):
            for split in [0, 1, 2, 3, 4]:
                batch = self.batch()
                history = []
                for chunk in [sequence[:split], sequence[split:]]:
                    trial = history[:]
                    rejected = False
                    for update in chunk:
                        relevant = [x for x in trial if x["call_id"] == update["call_id"]]
                        if relevant:
                            current = max(relevant, key=lambda x: x["version"])
                            if current["version"] == update["version"] and current != update:
                                rejected = True
                                break
                        trial.append(update)
                    if rejected:
                        with self.assertRaises(ValueError):
                            batch.ingest(list(chunk))
                    else:
                        batch.ingest(list(chunk))
                        history = trial
                    expected = []
                    for call, weight in [("a", -2), ("b", 0)]:
                        relevant = [x for x in history if x["call_id"] == call]
                        current = max(relevant, key=lambda x: x["version"]) if relevant else None
                        expected.append(dict(
                            call_id=call, version=current["version"] if current else None,
                            status=current["status"] if current else "pending",
                            value=sum(current["values"]) * weight
                            if current and current["status"] == "success" else None,
                            error=current["message"]
                            if current and current["status"] == "error" else None))
                    self.assertEqual(batch.snapshot(), expected, (sequence, split))


if __name__ == "__main__":
    unittest.main()
