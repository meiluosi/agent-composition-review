"""Local contract checks; run with ./run_python test_contract.py."""
import copy
import random
import unittest

from app import ToolBatch, score


def success(call_id="a", version=1, values=None):
    return {"call_id": call_id, "version": version, "status": "success",
            "values": [3] if values is None else values}


def error(call_id="a", version=1, message=""):
    return {"call_id": call_id, "version": version, "status": "error",
            "message": message}


class ContractTests(unittest.TestCase):
    def batch(self):
        return ToolBatch([{"call_id": "a", "weight": -2},
                          {"call_id": "b", "weight": 0}])

    def test_score(self):
        for values in ([], [0], [-7, 2], [10**150, -2, 10**200]):
            for weight in (0, -3, 7, 10**100):
                before = values.copy()
                expected = 0
                for value in values:
                    expected += value * weight
                self.assertEqual(score(values, weight), expected)
                self.assertEqual(values, before)

    def test_invalid_specs(self):
        cases = [None, {}, (), "a", [None], [[]], [{}],
                 [{"call_id": "a"}], [{"weight": 1}],
                 [{"call_id": "a", "weight": 1, "extra": 0}],
                 [{"call_id": "a", "weight": 1}] * 2]
        for value in (None, False, 1, [], {}, ""):
            cases.append([{"call_id": value, "weight": 1}])
        for value in (None, True, False, 1.0, "1", [], {}):
            cases.append([{"call_id": "a", "weight": value}])
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                ToolBatch(case)

    def test_invalid_updates_and_atomic_recovery(self):
        cases = [None, 1, [], {}, {"call_id": "a"}]
        for sample in (success(), error()):
            for key in sample:
                invalid = sample.copy()
                del invalid[key]
                cases.append(invalid)
            cases.append(dict(sample, extra=0))
            for key, values in {
                "call_id": [None, True, 1, [], {}, "", "unknown"],
                "version": [None, True, False, 0, -1, 1.0, "1", [], {}],
                "status": [None, True, 1, [], {}, "pending", "Success"],
            }.items():
                cases.extend(dict(sample, **{key: value}) for value in values)
        for value in (None, 1, True, (), "", {}, [True], [False],
                      [1.0], ["1"], [None], [[1]], [{}]):
            cases.append(dict(success(), values=value))
        for value in (None, True, 1, [], {}, b"error"):
            cases.append(dict(error(), message=value))
        cases.extend([dict(success(), message=""), dict(error(), values=[])])
        for invalid in cases:
            with self.subTest(invalid=invalid):
                batch = self.batch()
                batch.ingest([success(version=10)])
                before = batch.snapshot()
                # Invalid version-1 updates are stale and must still be checked.
                updates = [success("b"), invalid]
                original = copy.deepcopy(updates)
                with self.assertRaises(ValueError):
                    batch.ingest(updates)
                self.assertEqual(batch.snapshot(), before)
                self.assertEqual(updates, original)
                batch.ingest([error("b")])
                self.assertEqual(batch.finalize()[1]["status"], "error")
        for invalid in (None, {}, (), "", 1, True):
            with self.subTest(container=invalid):
                batch = self.batch()
                before = batch.snapshot()
                with self.assertRaises(ValueError):
                    batch.ingest(invalid)
                self.assertEqual(batch.snapshot(), before)

    def test_versions_conflicts_and_order(self):
        batch = self.batch()
        batch.ingest([success(version=3), success(version=3),
                      error(version=2), error(version=9, message="failed"),
                      success(version=3, values=[999])])
        self.assertEqual(batch.snapshot()[0], {
            "call_id": "a", "version": 9, "status": "error",
            "value": None, "error": "failed"})
        before = batch.snapshot()
        with self.assertRaises(ValueError):
            batch.ingest([success("b"), error(version=9, message="changed")])
        self.assertEqual(batch.snapshot(), before)
        with self.assertRaises(ValueError):
            batch.ingest([success(version=10), error(version=10)])
        self.assertEqual(batch.snapshot(), before)
        # Equal scores do not imply identical payloads.
        batch.ingest([success(version=10, values=[1, 2])])
        with self.assertRaises(ValueError):
            batch.ingest([success(version=10, values=[3])])
        batch.ingest([success(version=10, values=[1, 2])])
        self.assertEqual(batch.snapshot()[0]["value"], -6)
        before = batch.snapshot()
        with self.assertRaises(ValueError):
            batch.ingest([error(version=10), error(version=11)])
        self.assertEqual(batch.snapshot(), before)
        batch.ingest([error(version=11), error(version=10)])
        batch.ingest([success(version=10**100, values=[])])
        self.assertEqual(batch.snapshot()[0]["value"], 0)

    def test_lifecycle_and_exact_rows(self):
        batch = self.batch()
        expected = [{"call_id": key, "version": None, "status": "pending",
                     "value": None, "error": None} for key in ("a", "b")]
        self.assertEqual(batch.snapshot(), expected)
        batch.ingest([])
        with self.assertRaises(ValueError):
            batch.finalize()
        batch.ingest([error("b", message="")])
        with self.assertRaises(ValueError):
            batch.finalize()
        batch.ingest([success(values=[-7, 2])])
        expected[0].update(version=1, status="success", value=10)
        expected[1].update(version=1, status="error", error="")
        self.assertEqual(batch.finalize(), expected)
        for _ in range(3):
            self.assertEqual(batch.finalize(), expected)
            self.assertEqual(batch.snapshot(), expected)
        for updates in ([], [success(version=2)], None, {}):
            with self.assertRaises(RuntimeError):
                batch.ingest(updates)
        empty = ToolBatch([])
        empty.ingest([])
        self.assertEqual(empty.finalize(), [])
        self.assertEqual(empty.finalize(), [])
        with self.assertRaises(RuntimeError):
            empty.ingest([])

    def test_no_mutable_aliasing(self):
        specs = [{"call_id": "a", "weight": 2}]
        batch = ToolBatch(specs)
        specs[0].update(call_id="other", weight=100)
        specs.clear()
        values = [3, -1]
        update = success(values=values)
        updates = [update]
        before = copy.deepcopy(updates)
        batch.ingest(updates)
        self.assertEqual(updates, before)
        values.append(999)
        update.update(version=100, values=[], call_id="other")
        updates.clear()
        expected = [{"call_id": "a", "version": 1, "status": "success",
                     "value": 4, "error": None}]
        for read in (batch.snapshot, batch.finalize, batch.finalize, batch.snapshot):
            first, second = read(), read()
            self.assertEqual(first, expected)
            self.assertIsNot(first, second)
            self.assertIsNot(first[0], second[0])
            first[0].update(call_id="changed", value=999, error=[])
            first.append({})
            self.assertEqual(read(), expected)

    def test_randomized_transaction_model(self):
        rng = random.Random(20261004)
        specs = [{"call_id": key, "weight": weight}
                 for key, weight in zip("abcd", [-7, 0, 5, 10**60])]
        for trial in range(100):
            batch = ToolBatch(specs)
            # Reference retains all accepted history and scans it per update.
            history = []
            for step in range(30):
                updates = []
                for _ in range(rng.randrange(7)):
                    if history and rng.randrange(4) == 0:
                        updates.append(copy.deepcopy(rng.choice(history)))
                    else:
                        key, version = rng.choice("abcd"), rng.randrange(1, 12)
                        updates.append(success(key, version, [rng.randrange(-9, 10)
                                       for _ in range(rng.randrange(4))])
                                       if rng.randrange(2) else
                                       error(key, version, rng.choice(["", "x", "错误"])))
                candidate = copy.deepcopy(history)
                rejected = False
                for update in updates:
                    matching = [entry for entry in candidate
                                if entry["call_id"] == update["call_id"]]
                    latest = max(matching, key=lambda entry: entry["version"], default=None)
                    if (latest is not None and latest["version"] == update["version"]
                            and latest != update):
                        rejected = True
                        break
                    candidate.append(copy.deepcopy(update))
                if rejected:
                    with self.assertRaises(ValueError):
                        batch.ingest(updates)
                else:
                    batch.ingest(updates)
                    history = candidate
                expected = []
                for spec in specs:
                    matching = [entry for entry in history
                                if entry["call_id"] == spec["call_id"]]
                    latest = max(matching, key=lambda entry: entry["version"], default=None)
                    row = dict(call_id=spec["call_id"], version=None,
                               status="pending", value=None, error=None)
                    if latest is not None:
                        row.update(version=latest["version"], status=latest["status"])
                        if latest["status"] == "success":
                            row["value"] = sum(value * spec["weight"]
                                               for value in latest["values"])
                        else:
                            row["error"] = latest["message"]
                    expected.append(row)
                self.assertEqual(batch.snapshot(), expected, (trial, step))


if __name__ == "__main__":
    unittest.main(verbosity=2)
