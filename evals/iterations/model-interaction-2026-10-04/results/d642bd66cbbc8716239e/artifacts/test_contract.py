"""Business-contract regression tests, independent of the supplied smoke test."""
import copy
import itertools
import random
import unittest

from app import ToolBatch, score


def success(call_id="a", version=1, values=None):
    return {"call_id": call_id, "version": version, "status": "success",
            "values": [3] if values is None else values}


def error(call_id="a", version=1, message="failed"):
    return {"call_id": call_id, "version": version, "status": "error",
            "message": message}


def row(call_id, version=None, status="pending", value=None, error=None):
    return dict(call_id=call_id, version=version, status=status,
                value=value, error=error)


class ContractTests(unittest.TestCase):
    def batch(self):
        return ToolBatch([{"call_id": "a", "weight": -2},
                          {"call_id": "b", "weight": 0}])

    def test_score(self):
        for values in ([], [0], [-1, -2], [1, -2, 3], [10**100, -7]):
            for weight in (0, 1, -9, 10**100):
                with self.subTest(values=values, weight=weight):
                    before = values[:]
                    expected = 0
                    for value in values:
                        expected += value * weight
                    self.assertEqual(score(values, weight), expected)
                    self.assertEqual(values, before)

    def test_spec_validation(self):
        invalid = [None, {}, (), "", 1, [None], [[]], [{}],
                   [{"call_id": "a"}], [{"weight": 1}],
                   [{"call_id": "a", "weight": 1, "extra": 0}],
                   [{"call_id": "a", "weight": 1}] * 2]
        invalid += [[{"call_id": key, "weight": 1}]
                    for key in (None, "", 1, True, [], {})]
        invalid += [[{"call_id": "a", "weight": weight}]
                    for weight in (None, True, False, 1.0, "1", [], {})]
        for specs in invalid:
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                ToolBatch(specs)
        for weight in (0, -1, 10**100):
            batch = ToolBatch([{"call_id": " ", "weight": weight}])
            batch.ingest([success(" ")])
            self.assertEqual(batch.finalize(), [row(" ", 1, "success", 3 * weight)])

    def test_order_and_pending(self):
        specs = [{"call_id": key, "weight": weight}
                 for key, weight in (("c", 2), ("a", -3), ("b", 0))]
        updates = [success("a", values=[-2, 4]), error("b", message=""),
                   success("c", values=[])]
        for order in itertools.permutations(updates):
            batch = ToolBatch(specs)
            self.assertEqual(batch.snapshot(), [row("c"), row("a"), row("b")])
            for update in order:
                batch.ingest([update])
            self.assertEqual(batch.finalize(), [row("c", 1, "success", 0),
                             row("a", 1, "success", -6), row("b", 1, "error", error="")])

    def test_versions_and_payload_identity(self):
        batch = self.batch()
        batch.ingest([error(version=4), success(version=10, values=[-5, 2]),
                      error(version=4, message="old conflict is irrelevant")])
        expected = [row("a", 10, "success", 6), row("b")]
        self.assertEqual(batch.snapshot(), expected)
        batch.ingest([success(version=10, values=[-5, 2])] * 2)
        self.assertEqual(batch.snapshot(), expected)
        # Equal scores do not make distinct payloads identical.
        for update in (success(version=10, values=[-3]),
                       success(version=10, values=[2, -5]), error(version=10)):
            with self.assertRaises(ValueError):
                batch.ingest([update])
            self.assertEqual(batch.snapshot(), expected)
        batch.ingest([error(version=11, message=""), error(version=11, message="")])
        self.assertEqual(batch.snapshot()[0], row("a", 11, "error", error=""))
        with self.assertRaises(ValueError):
            batch.ingest([error(version=11, message="different")])
        batch.ingest([success(version=10), success(version=10, values=[999])])
        self.assertEqual(batch.snapshot()[0], row("a", 11, "error", error=""))

    def test_list_order_controls_conflicts(self):
        batch = self.batch()
        batch.ingest([success(version=1), success(version=2), error(version=1)])
        self.assertEqual(batch.snapshot()[0], row("a", 2, "success", -6))
        before = batch.snapshot()
        with self.assertRaises(ValueError):
            batch.ingest([error(version=2), success(version=3)])
        self.assertEqual(batch.snapshot(), before)
        batch.ingest([success(version=3), error(version=2)])
        self.assertEqual(batch.snapshot()[0]["version"], 3)

    def test_invalid_updates_including_stale(self):
        invalid = [None, [], "", 3, {}, {"call_id": "a"}]
        for base in (success(), error()):
            invalid += [{key: value for key, value in base.items() if key != missing}
                        for missing in base]
            invalid += [dict(base, extra=True)]
            for key in (None, "", "unknown", 1, True, [], {}):
                invalid.append(dict(base, call_id=key))
            for version in (None, 0, -1, True, False, 1.0, "1", [], {}):
                invalid.append(dict(base, version=version))
            for status in (None, "pending", "SUCCESS", 1, True, [], {}):
                invalid.append(dict(base, status=status))
        invalid += [dict(success(), values=values)
                    for values in (None, (), "", 1, {}, [True], [False], [1.0],
                                   [None], [[]], [{}], ["1"])]
        invalid += [dict(error(), message=message)
                    for message in (None, True, 1, 1.0, [], {})]
        invalid += [dict(success(), message=""), dict(error(), values=[])]
        for stale in (False, True):
            for update in invalid:
                with self.subTest(stale=stale, update=update):
                    batch = self.batch()
                    if stale:
                        batch.ingest([success(version=100)])
                    before = batch.snapshot()
                    submitted = [success("b"), update]
                    inputs_before = copy.deepcopy(submitted)
                    with self.assertRaises(ValueError):
                        batch.ingest(submitted)
                    self.assertEqual(batch.snapshot(), before)
                    self.assertEqual(submitted, inputs_before)
                    batch.ingest([error("b")])
                    self.assertEqual(batch.snapshot()[1]["status"], "error")

    def test_invalid_update_containers(self):
        batch = self.batch()
        for updates in (None, {}, (), "", 1, True, iter([])):
            with self.assertRaises(ValueError):
                batch.ingest(updates)
            self.assertEqual(batch.snapshot(), [row("a"), row("b")])

    def test_atomic_conflicts_and_recovery(self):
        for initial in ([], [success(version=5)]):
            batch = self.batch()
            batch.ingest(initial)
            before = batch.snapshot()
            with self.assertRaises(ValueError):
                batch.ingest([success("b"), success(version=10), error(version=10)])
            self.assertEqual(batch.snapshot(), before)
            batch.ingest([error("b"), success(version=6)])
            self.assertEqual(batch.finalize(), [row("a", 6, "success", -6),
                                               row("b", 1, "error", error="failed")])

    def test_finalize_and_empty_batches(self):
        batch = self.batch()
        batch.ingest([])
        with self.assertRaises(ValueError):
            batch.finalize()
        batch.ingest([success()])
        with self.assertRaises(ValueError):
            batch.finalize()
        batch.ingest([])
        batch.ingest([error("b")])
        final = batch.finalize()
        self.assertEqual(batch.finalize(), final)
        self.assertEqual(batch.snapshot(), final)
        for updates in ([], [success(version=99)], None, {}):
            with self.assertRaises(RuntimeError):
                batch.ingest(updates)
            self.assertEqual(batch.snapshot(), final)
        empty = ToolBatch([])
        empty.ingest([])
        self.assertEqual(empty.finalize(), [])
        self.assertEqual(empty.finalize(), [])
        self.assertIsNot(empty.finalize(), empty.snapshot())
        with self.assertRaises(RuntimeError):
            empty.ingest([])

    def test_no_shared_mutable_state(self):
        specs = [{"call_id": "a", "weight": -2}, {"call_id": "b", "weight": 0}]
        original_specs = copy.deepcopy(specs)
        batch = ToolBatch(specs)
        self.assertEqual(specs, original_specs)
        specs[0].update(call_id="changed", weight=100)
        specs.reverse()
        specs.clear()
        updates = [success(values=[-5, 2]), error("b", message="")]
        original_updates = copy.deepcopy(updates)
        batch.ingest(updates)
        self.assertEqual(updates, original_updates)
        updates[0]["values"].append(999)
        updates[0].update(call_id="b", version=99, status="error")
        updates[1]["message"] = "changed"
        updates.clear()
        expected = [row("a", 1, "success", 6), row("b", 1, "error", error="")]
        for read in (batch.snapshot, batch.snapshot, batch.finalize, batch.finalize):
            result = read()
            self.assertEqual(result, expected)
            result[0].clear()
            result[1].update(call_id="changed", error="changed")
            result.reverse()
            result.clear()
            self.assertEqual(batch.snapshot(), expected)

    def test_generated_valid_update_sequences(self):
        # Independent tuple model checks retries, replacements and rollback.
        rng = random.Random(41026)
        keys = ["z", "a", "m"]
        weights = {"z": -5, "a": 0, "m": 10**50}
        batch = ToolBatch([{"call_id": key, "weight": weights[key]} for key in keys])
        model = {}
        accepted = rejected = 0
        for _ in range(2000):
            updates = []
            for _ in range(rng.randrange(6)):
                key = rng.choice(keys)
                version = rng.randrange(1, 16)
                if rng.randrange(2):
                    updates.append(success(key, version, [rng.randrange(-3, 4)
                                                          for _ in range(rng.randrange(4))]))
                else:
                    updates.append(error(key, version, rng.choice(["", "bad", "retry"])))
            candidate = model.copy()
            conflict = False
            for update in updates:
                key = update["call_id"]
                payload = (tuple(update["values"]) if update["status"] == "success"
                           else update["message"])
                new = (update["version"], update["status"], payload)
                old = candidate.get(key)
                if old is None or new[0] > old[0]:
                    candidate[key] = new
                elif new[0] == old[0] and new != old:
                    conflict = True
                    break
            if conflict:
                with self.assertRaises(ValueError):
                    batch.ingest(updates)
                rejected += 1
            else:
                batch.ingest(updates)
                model = candidate
                accepted += 1
            expected = []
            for key in keys:
                if key not in model:
                    expected.append(row(key))
                    continue
                version, status, payload = model[key]
                if status == "error":
                    expected.append(row(key, version, status, error=payload))
                else:
                    value = 0
                    for number in payload:
                        value += number * weights[key]
                    expected.append(row(key, version, status, value=value))
            self.assertEqual(batch.snapshot(), expected)
        self.assertGreater(accepted, 0)
        self.assertGreater(rejected, 0)
        self.assertEqual(batch.finalize(), expected)


if __name__ == "__main__":
    unittest.main()
