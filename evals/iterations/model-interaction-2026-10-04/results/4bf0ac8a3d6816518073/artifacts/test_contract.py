"""Business-contract regression tests; optional module argument selects original."""
import importlib
import itertools
import sys
import unittest
from copy import deepcopy

subject = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else "app")


def success(call="a", version=1, values=None):
    return dict(call_id=call, version=version, status="success",
                values=[2] if values is None else values)


def error(call="a", version=1, message=""):
    return dict(call_id=call, version=version, status="error", message=message)


class ContractTests(unittest.TestCase):
    def batch(self):
        return subject.ToolBatch([dict(call_id="a", weight=2),
                                  dict(call_id="b", weight=-3)])

    def test_signed_score(self):
        for values in ([], [0], [-3], [-3, 1], [4, -4], [10**100, -2]):
            for weight in (0, 1, -2, 10**100):
                with self.subTest(values=values, weight=weight):
                    before = values[:]
                    self.assertEqual(subject.score(values, weight), sum(values) * weight)
                    self.assertEqual(values, before)

    def test_snapshot_and_finalize(self):
        batch = self.batch()
        self.assertEqual(batch.snapshot(), [
            dict(call_id=c, version=None, status="pending", value=None, error=None)
            for c in ("a", "b")])
        batch.ingest([success("b", 7, [-5, 2]), error("a", 4, "failure")])
        expected = [dict(call_id="a", version=4, status="error", value=None, error="failure"),
                    dict(call_id="b", version=7, status="success", value=9, error=None)]
        self.assertEqual(batch.snapshot(), expected)
        self.assertEqual(batch.finalize(), expected)
        for read in (batch.snapshot, batch.finalize):
            result = read()
            self.assertEqual(result, expected)
            result[0]["error"] = "changed"
            result.clear()
            self.assertEqual(read(), expected)
        for updates in ([], [success()], None):
            with self.assertRaises(RuntimeError):
                batch.ingest(updates)

    def test_invalid_specs(self):
        invalid = [None, {}, (), [None], [{}],
                   [dict(call_id="", weight=1)], [dict(call_id=1, weight=1)],
                   [dict(call_id="a", weight=1, extra=2)],
                   [dict(call_id="a", weight=1), dict(call_id="a", weight=2)]]
        invalid += [[dict(call_id="a", weight=w)] for w in (True, False, 1.0, "1", None)]
        for specs in invalid:
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                subject.ToolBatch(specs)
        for weight in (0, -1, 10**100):
            batch = subject.ToolBatch([dict(call_id="a", weight=weight)])
            batch.ingest([success(values=[])])
            self.assertEqual(batch.finalize()[0]["value"], 0)

    def test_invalid_updates_including_stale_are_atomic(self):
        invalid = [None, [], {}, success("unknown"), error(message=3)]
        for valid in (success(), error()):
            for field in valid:
                item = valid.copy()
                del item[field]
                invalid.append(item)
            invalid.append(dict(valid, extra=1))
            for call in (None, 1, [], ""):
                invalid.append(dict(valid, call_id=call))
            for version in (True, False, 0, -1, 1.0, "1", None):
                invalid.append(dict(valid, version=version))
            for status in (None, "pending", "SUCCESS", 0, []):
                invalid.append(dict(valid, status=status))
        for values in (None, (), "", [True], [1.0], ["1"], [[]]):
            invalid.append(success(values=[]) | {"values": values})
        for item in invalid:
            with self.subTest(item=item):
                batch = self.batch()
                batch.ingest([success(version=10)])
                before = batch.snapshot()
                with self.assertRaises(ValueError):
                    batch.ingest([success("b"), item])
                self.assertEqual(batch.snapshot(), before)
                batch.ingest([error("b")])
                batch.finalize()
        for updates in (None, {}, (), ""):
            with self.assertRaises(ValueError):
                self.batch().ingest(updates)

    def test_conflict_rollback_and_order(self):
        batch = self.batch()
        batch.ingest([success()])
        before = batch.snapshot()
        for items in ([success("b"), error()],
                      [error(version=2), success(version=2)],
                      [success("b"), success(values=[1, 1])]):
            with self.assertRaises(ValueError):
                batch.ingest(items)
            self.assertEqual(batch.snapshot(), before)
        batch.ingest([error(version=3), success(version=1, values=[999]),
                      error(version=3), success(version=8, values=[])])
        self.assertEqual(batch.snapshot()[0]["value"], 0)
        batch.ingest([error(version=9)])
        self.assertEqual(batch.snapshot()[0]["status"], "error")
        # A conflict happens before a later higher version can supersede it.
        with self.assertRaises(ValueError):
            batch.ingest([success(version=9), success(version=10)])
        self.assertEqual(batch.snapshot()[0]["version"], 9)

    def test_finalize_lifecycle(self):
        batch = self.batch()
        batch.ingest([])
        with self.assertRaises(ValueError):
            batch.finalize()
        batch.ingest([error()])
        with self.assertRaises(ValueError):
            batch.finalize()
        batch.ingest([error("b")])
        batch.finalize()
        empty = subject.ToolBatch([])
        empty.ingest([])
        self.assertEqual(empty.finalize(), [])
        self.assertEqual(empty.finalize(), [])
        with self.assertRaises(RuntimeError):
            empty.ingest([])

    def test_input_isolation(self):
        specs = [dict(call_id="a", weight=2)]
        before_specs = deepcopy(specs)
        batch = subject.ToolBatch(specs)
        self.assertEqual(specs, before_specs)
        specs[0].update(call_id="changed", weight=99)
        specs.clear()
        updates = [success(values=[2, 3])]
        before_updates = deepcopy(updates)
        batch.ingest(updates)
        self.assertEqual(updates, before_updates)
        updates[0]["values"].append(100)
        updates[0]["version"] = 999
        updates.clear()
        self.assertEqual(batch.finalize(), [
            dict(call_id="a", version=1, status="success", value=10, error=None)])

    def test_finite_version_histories(self):
        # Independent small state machine: enumerate orderings, repeats, conflicts,
        # and transaction boundaries for valid payloads.
        events = []
        for version in (1, 3):
            events.extend([success(version=version, values=[version]),
                           error(version=version, message=str(version))])
        for history in itertools.product(events, repeat=4):
            for split in range(5):
                batch = subject.ToolBatch([dict(call_id="a", weight=2)])
                state = None
                for updates in (list(history[:split]), list(history[split:])):
                    candidate = state
                    conflict = False
                    for event in updates:
                        if candidate is None or event["version"] > candidate["version"]:
                            candidate = event
                        elif event["version"] == candidate["version"] and event != candidate:
                            conflict = True
                            break
                    if conflict:
                        with self.assertRaises(ValueError):
                            batch.ingest(updates)
                    else:
                        batch.ingest(updates)
                        state = candidate
                    row = batch.snapshot()[0]
                    expected = dict(call_id="a", version=None, status="pending", value=None, error=None)
                    if state is not None:
                        expected.update(version=state["version"], status=state["status"])
                        if state["status"] == "success":
                            expected["value"] = sum(state["values"]) * 2
                        else:
                            expected["error"] = state["message"]
                    self.assertEqual(row, expected)


if __name__ == "__main__":
    unittest.main()
