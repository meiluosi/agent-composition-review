"""Maintainer-side acceptance; run with the candidate app.py path."""
import contextlib
import importlib.util
import io
import json
import sys


def expect_error(kind, callback):
    try:
        callback()
    except kind:
        return
    raise AssertionError("expected " + kind.__name__)


def success(key, version=1, values=None):
    return {"call_id": key, "version": version, "status": "success", "values": [3] if values is None else values}


def error(key, version=1, message="oops"):
    return {"call_id": key, "version": version, "status": "error", "message": message}


def specs():
    return [{"call_id": "a", "weight": 2}, {"call_id": "b", "weight": 3}]


def row(key, version=None, status="pending", value=None, error=None):
    return {"call_id": key, "version": version, "status": status, "value": value, "error": error}


def order_and_pending(app):
    unsorted = [{"call_id": "z-last", "weight": 1}, {"call_id": "α-first", "weight": 2},
                {"call_id": "call/3", "weight": 3}]
    q = app.ToolBatch(unsorted)
    q.ingest([success("call/3"), success("z-last"), success("α-first")])
    assert q.finalize() == [row("z-last", 1, "success", 3), row("α-first", 1, "success", 6),
                            row("call/3", 1, "success", 9)], "expected order was not preserved"
    p = app.ToolBatch(specs())
    assert p.snapshot() == [row("a"), row("b")]
    p.ingest([success("b", 2, [4])])
    assert p.snapshot() == [row("a"), row("b", 2, "success", 12)]
    p.ingest([success("a", 1, [5])])
    assert p.finalize() == [row("a", 1, "success", 10), row("b", 2, "success", 12)]


def scores(app):
    for values, weight in [([], 7), ([1, -2, 4], 3), ([-5], -2), ([7], 0),
                           ([2**65, -(2**64), -3], 11), ([0, 0], -7)]:
        want = sum(values) * weight
        assert app.score(values, weight) == want
        p = app.ToolBatch([{"call_id": "x", "weight": weight}])
        p.ingest([success("x", 1, values)])
        assert p.finalize() == [row("x", 1, "success", want)]
        assert type(p.snapshot()[0]["value"]) is int


def stale_and_retry(app):
    p = app.ToolBatch(specs())
    p.ingest([success("a", 3, [7]), error("b", 4, "latest")])
    p.ingest([error("a", 1, "stale"), success("b", 2, [100])])
    assert p.snapshot() == [row("a", 3, "success", 14), row("b", 4, "error", error="latest")]
    p.ingest([success("a", 3, [7]), success("a", 9, [8]), success("a", 3, [999])])
    assert p.snapshot()[0] == row("a", 9, "success", 16)
    p.ingest([success("b", 8, []), error("a", 10, "new error")])
    assert p.finalize() == [row("a", 10, "error", error="new error"), row("b", 8, "success", 0)]


def duplicate_and_conflict(app):
    p = app.ToolBatch(specs())
    p.ingest([success("a", 2, [1, 2]), success("a", 2, [1, 2]), error("b", 1, "x")])
    before = p.snapshot()
    for item in [success("a", 2, [2, 1]), error("a", 2, "x"), error("b", 1, "different")]:
        expect_error(ValueError, lambda: p.ingest([item]))
        assert p.snapshot() == before
    p.ingest([success("a", 2, [1, 2]), error("b", 1, "x")])
    assert p.snapshot() == before


def atomic_conflicts(app):
    p = app.ToolBatch(specs())
    p.ingest([success("a", 2, [2])])
    before = p.snapshot()
    for updates in [[success("b", 1, [9]), error("a", 2, "conflict")],
                    [success("a", 3, [7]), success("a", 3, [8])],
                    [success("a", 5, [1]), success("b", 1, [2]), success("b", 1, [3])]]:
        expect_error(ValueError, lambda: p.ingest(updates))
        assert p.snapshot() == before, "partial update escaped rejected batch"
    p.ingest([success("b", 1, [4])])
    assert p.finalize() == [row("a", 2, "success", 4), row("b", 1, "success", 12)]


def atomic_validation(app):
    p = app.ToolBatch(specs())
    p.ingest([success("a", 4, [5])])
    before = p.snapshot()
    invalid = [success("unknown"), success("a", 0), success("a", True), success("a", 1, [True]),
               success("a", 1, (2,)), {**success("a"), "extra": 1},
               {"call_id": "a", "version": 1, "status": "unknown"}, error("a", 1, 3),
               {}, [], {"call_id": [], "version": 1, "status": "success", "values": []},
               {"call_id": {}, "version": 1, "status": "success", "values": []},
               success("a", 1, [1.5]), error("a", 1, None)]
    for item in invalid:
        expect_error(ValueError, lambda: p.ingest([success("b", 1, [8]), item]))
        assert p.snapshot() == before, repr(item)
    for invalid_batch in [None, {}, (success("b"),)]:
        expect_error(ValueError, lambda: p.ingest(invalid_batch))
        assert p.snapshot() == before
    p.ingest([])
    assert p.snapshot() == before


def failure_then_recovery(app):
    p = app.ToolBatch(specs())
    expect_error(ValueError, p.finalize)
    p.ingest([error("a", 1, "broken")])
    expect_error(ValueError, p.finalize)
    p.ingest([success("a", 2, [5]), error("b", 1, "")])
    assert p.finalize() == [row("a", 2, "success", 10), row("b", 1, "error", error="")]


def finalized_lifecycle(app):
    p = app.ToolBatch(specs())
    p.ingest([success("a"), error("b")])
    want = p.finalize()
    for updates in [[], [success("a", 2)], [success("a")], [success("unknown")]]:
        expect_error(RuntimeError, lambda: p.ingest(updates))
        assert p.snapshot() == want and p.finalize() == want
    assert p.finalize() is not p.finalize()
    assert p.snapshot()[0] is not p.snapshot()[0]


def caller_input_isolation(app):
    from copy import deepcopy
    definitions = list(reversed(specs()))
    original_definitions = deepcopy(definitions)
    p = app.ToolBatch(definitions)
    updates = [error("b", 1, "failed"), success("a", 1, [4, 1, 3])]
    original_updates = deepcopy(updates)
    p.ingest(updates)
    assert definitions == original_definitions and updates == original_updates
    definitions[0]["weight"] = 999
    definitions.reverse()
    definitions.append({"call_id": "x", "weight": 1})
    updates[1]["values"].append(100)
    updates[0]["message"] = "changed"
    updates.clear()
    assert p.finalize() == [row("b", 1, "error", error="failed"), row("a", 1, "success", 16)]


def output_isolation(app):
    p = app.ToolBatch(specs())
    p.ingest([success("a", 1, [6]), error("b", 1, "oops")])
    before = p.snapshot()
    altered = p.snapshot()
    altered[0]["value"] = 999
    altered[1]["status"] = "success"
    altered.reverse()
    altered.clear()
    assert p.snapshot() == before
    final = p.finalize()
    final[0]["call_id"] = "changed"
    final.append(row("new"))
    assert p.finalize() == before and p.snapshot() == before


def specifications_and_empty(app):
    invalid = [None, {}, [{"call_id": "", "weight": 1}], [{"call_id": "a", "weight": True}],
               [{"call_id": "a", "weight": 1}, {"call_id": "a", "weight": 2}],
               [{"call_id": [], "weight": 1}], [{"call_id": "a"}],
               [{"call_id": "a", "weight": 1, "extra": 2}]]
    for definitions in invalid:
        expect_error(ValueError, lambda: app.ToolBatch(definitions))
    p = app.ToolBatch([])
    assert p.snapshot() == []
    p.ingest([])
    assert p.finalize() == [] and p.finalize() == []
    expect_error(RuntimeError, lambda: p.ingest([]))


def independent_batches(app):
    a, b = app.ToolBatch(specs()), app.ToolBatch(specs())
    a.ingest([success("a", 1, [9]), error("b")])
    b.ingest([error("a"), success("b", 1, [7])])
    assert a.finalize() == [row("a", 1, "success", 18), row("b", 1, "error", error="oops")]
    assert b.finalize() == [row("a", 1, "error", error="oops"), row("b", 1, "success", 21)]


TESTS = [(fn.__name__, fn) for fn in [order_and_pending, scores, stale_and_retry,
    duplicate_and_conflict, atomic_conflicts, atomic_validation, failure_then_recovery,
    finalized_lifecycle, caller_input_isolation, output_isolation, specifications_and_empty,
    independent_batches]]


def main():
    checks = []
    module = None
    load_error = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            spec = importlib.util.spec_from_file_location("app", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
    except BaseException as exc:
        load_error = exc
    for name, test in TESTS:
        try:
            if load_error is not None:
                raise load_error
            with contextlib.redirect_stdout(io.StringIO()):
                test(module)
            checks.append({"test": name, "passed": True})
        except BaseException as exc:
            checks.append({"test": name, "passed": False,
                           "error": type(exc).__name__ + ": " + str(exc)})
    print(json.dumps({"passed": all(c["passed"] for c in checks),
                      "tests_run": len(checks), "checks": checks}, ensure_ascii=True))


if __name__ == "__main__":
    main()
