"""Business-contract checks for both the delivered and original implementations."""
import copy
import itertools
import random
import unittest

import app
import original


def success(call_id="a", version=1, values=None):
    return {"call_id": call_id, "version": version, "status": "success",
            "values": [2, -5] if values is None else values}


def error(call_id="a", version=1, message=""):
    return {"call_id": call_id, "version": version, "status": "error",
            "message": message}


class AppContract(unittest.TestCase):
    module = app

    def batch(self):
        return self.module.ToolBatch([
            {"call_id": "b", "weight": -3}, {"call_id": "a", "weight": 2}])

    def test_score(self):
        for values, weight, expected in [([], -999, 0), ([4, -7], -3, 9),
                                         ([1, -1], 10**100, 0),
                                         ([10**100, -3], 10**90,
                                          10**190 - 3 * 10**90), ([99], 0, 0)]:
            before = values[:]
            self.assertEqual(self.module.score(values, weight), expected)
            self.assertEqual(values, before)

    def test_spec_validation(self):
        invalid = [None, (), {}, "", [None], [[]], [{}],
                   [{"call_id": "a"}], [{"weight": 2}],
                   [{"call_id": "a", "weight": 2, "extra": None}],
                   [{"call_id": "a", "weight": 2}] * 2]
        invalid += [[{"call_id": value, "weight": 2}]
                    for value in ("", None, True, 1, [], {})]
        invalid += [[{"call_id": "a", "weight": value}]
                    for value in (None, True, False, 1.0, "2", [], {})]
        for specs in invalid:
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                self.module.ToolBatch(specs)
        for weight in (0, -5, 10**100):
            batch = self.module.ToolBatch([{"call_id": " 空 ", "weight": weight}])
            batch.ingest([success(" 空 ", values=[-2])])
            self.assertEqual(batch.finalize()[0]["value"], -2 * weight)

    def test_pending_and_order(self):
        batch = self.batch()
        self.assertEqual(batch.snapshot(), [
            {"call_id": key, "version": None, "status": "pending",
             "value": None, "error": None} for key in ("b", "a")])
        batch.ingest([success("a"), error("b", message="failed")])
        self.assertEqual(batch.snapshot(), [
            {"call_id": "b", "version": 1, "status": "error", "value": None,
             "error": "failed"},
            {"call_id": "a", "version": 1, "status": "success", "value": -6,
             "error": None}])

    def test_empty_batch(self):
        batch = self.module.ToolBatch([])
        batch.ingest([])
        first = batch.finalize()
        self.assertEqual(first, [])
        self.assertIsNot(first, batch.finalize())
        self.assertIsNot(first, batch.snapshot())
        for updates in ([], [success()], None):
            with self.assertRaises(RuntimeError):
                batch.ingest(updates)

    def test_finalize_failure_recovery_and_sealing(self):
        batch = self.batch()
        for _ in range(2):
            with self.assertRaises(ValueError):
                batch.finalize()
            batch.ingest([])
        batch.ingest([error("a")])
        before = batch.snapshot()
        with self.assertRaises(ValueError):
            batch.finalize()
        self.assertEqual(batch.snapshot(), before)
        batch.ingest([success("b", values=[])])
        expected = batch.snapshot()
        for _ in range(2):
            self.assertEqual(batch.finalize(), expected)
        for updates in ([], [success("a", 99)], [None], None, {}):
            with self.assertRaises(RuntimeError):
                batch.ingest(updates)
            self.assertEqual(batch.snapshot(), expected)

    def test_update_validation_atomic_even_when_stale(self):
        invalid = [None, [], "", {},
                   success("unknown"), success(call_id=[]),
                   success(call_id=None), success(call_id=True)]
        for good in (success(), error()):
            for key in good:
                item = dict(good)
                del item[key]
                invalid.append(item)
            invalid.append(dict(good, extra=1))
            for version in (None, 0, -1, True, False, 1.0, "1", [], {}):
                invalid.append(dict(good, version=version))
            for status in (None, "pending", "Success", True, 1, [], {}):
                invalid.append(dict(good, status=status))
        for values in (None, (), "", {}, [True], [False], [1.0], ["1"],
                       [None], [[1]], [1, {}]):
            invalid.append(dict(success(), values=values))
        for message in (None, True, 1, [], {}):
            invalid.append(dict(error(), message=message))
        invalid.extend([dict(success(), message=""), dict(error(), values=[])])
        for latest in (None, 10):
            for item in invalid:
                with self.subTest(latest=latest, item=item):
                    batch = self.batch()
                    if latest:
                        batch.ingest([success(version=latest)])
                    before = batch.snapshot()
                    updates = [success("b", 20), item]
                    untouched = copy.deepcopy(updates)
                    with self.assertRaises(ValueError):
                        batch.ingest(updates)
                    self.assertEqual(batch.snapshot(), before)
                    self.assertEqual(updates, untouched)
                    batch.ingest([success("b", 20), error("a", 30)])
                    batch.finalize()
        for invalid_list in (None, {}, (), "", 1, True):
            batch = self.batch()
            before = batch.snapshot()
            with self.assertRaises(ValueError):
                batch.ingest(invalid_list)
            self.assertEqual(batch.snapshot(), before)

    def test_version_changes_and_payload_identity(self):
        batch = self.batch()
        batch.ingest([success(values=[1, 2])])
        before = batch.snapshot()
        # Equal scores do not imply identical payloads.
        for conflict in (success(values=[3]), success(values=[2, 1]), error()):
            with self.assertRaises(ValueError):
                batch.ingest([success("b"), conflict])
            self.assertEqual(batch.snapshot(), before)
        batch.ingest([success(values=[1, 2]), error(version=4, message="retry")])
        self.assertEqual(batch.snapshot()[1]["error"], "retry")
        with self.assertRaises(ValueError):
            batch.ingest([error(version=4, message="different")])
        batch.ingest([success(version=20, values=[]), error(version=4),
                      success(version=1, values=[999])])
        self.assertEqual(batch.snapshot()[1], {
            "call_id": "a", "version": 20, "status": "success", "value": 0,
            "error": None})

    def test_conflicts_within_batch_rollback(self):
        batch = self.batch()
        before = batch.snapshot()
        with self.assertRaises(ValueError):
            batch.ingest([success("a"), success("b"), error("a")])
        self.assertEqual(batch.snapshot(), before)
        # The conflict happens before the higher version, so the batch fails.
        with self.assertRaises(ValueError):
            batch.ingest([success("a"), error("a"), success("a", 2)])
        self.assertEqual(batch.snapshot(), before)
        batch.ingest([success("a"), success("a", 2), error("a")])
        self.assertEqual(batch.snapshot()[1]["version"], 2)

    def test_ownership(self):
        specs = [{"call_id": "a", "weight": -2}]
        batch = self.module.ToolBatch(specs)
        self.assertEqual(specs, [{"call_id": "a", "weight": -2}])
        specs[0].update(call_id="changed", weight=99)
        specs.clear()
        updates = [success(values=[1, -4])]
        before = copy.deepcopy(updates)
        batch.ingest(updates)
        self.assertEqual(updates, before)
        updates[0]["values"].append(1000)
        updates[0].update(call_id="changed", version=99)
        updates.clear()
        expected = [{"call_id": "a", "version": 1, "status": "success",
                     "value": 6, "error": None}]
        for read in (batch.snapshot, batch.snapshot, batch.finalize, batch.finalize):
            result = read()
            self.assertEqual(result, expected)
            result[0].clear()
            result.append({"injected": []})
            self.assertEqual(batch.snapshot(), expected)

    def test_arbitrary_completion_order(self):
        events = [success("a", 1), error("a", 3, "latest"),
                  error("b", 2), success("b", 100, [-1, 9])]
        for ordering in itertools.permutations(events):
            for split in range(5):
                batch = self.batch()
                batch.ingest(list(ordering[:split]))
                batch.ingest(list(ordering[split:]))
                self.assertEqual(batch.finalize(), [
                    {"call_id": "b", "version": 100, "status": "success",
                     "value": -24, "error": None},
                    {"call_id": "a", "version": 3, "status": "error",
                     "value": None, "error": "latest"}])

    def test_generated_transaction_sequences(self):
        # Independent history oracle: committed history determines current maximum.
        rng = random.Random(47021)
        for trial in range(80):
            batch = self.batch()
            committed = []
            for step in range(40):
                updates = []
                for _ in range(rng.randrange(5)):
                    key, version = rng.choice(("a", "b")), rng.choice((1, 2, 5, 20))
                    updates.append(success(key, version, rng.choice(([], [1], [-1, 2])))
                                   if rng.randrange(2) else
                                   error(key, version, rng.choice(("", "err"))))
                candidate = copy.deepcopy(committed)
                conflict = False
                for item in updates:
                    history = [x for x in candidate if x["call_id"] == item["call_id"]]
                    current = max(history, key=lambda x: x["version"]) if history else None
                    if current and current["version"] == item["version"] and current != item:
                        conflict = True
                        break
                    candidate.append(copy.deepcopy(item))
                with self.subTest(trial=trial, step=step):
                    if conflict:
                        with self.assertRaises(ValueError):
                            batch.ingest(updates)
                    else:
                        batch.ingest(updates)
                        committed = candidate
                    expected = []
                    for key, weight in (("b", -3), ("a", 2)):
                        history = [x for x in committed if x["call_id"] == key]
                        item = max(history, key=lambda x: x["version"]) if history else None
                        value = None
                        if item and item["status"] == "success":
                            value = 0
                            for number in item["values"]:
                                value += number * weight
                        expected.append({"call_id": key,
                                         "version": item["version"] if item else None,
                                         "status": item["status"] if item else "pending",
                                         "value": value,
                                         "error": item.get("message") if item else None})
                    self.assertEqual(batch.snapshot(), expected)


class OriginalContract(AppContract):
    module = original


if __name__ == "__main__":
    unittest.main()
