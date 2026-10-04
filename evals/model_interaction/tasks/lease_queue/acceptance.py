"""Maintainer-only behavioral acceptance; no external services or wall clocks."""
import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import threading
import traceback


@contextlib.contextmanager
def queues(app):
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "queue.db"
        yield app.Queue(path), app.Queue(path), path


def test_empty_and_persistence(app):
    with queues(app) as (one, two, path):
        assert one.claim("w", 0, 1) is None
        one.put("a", None)
        claim = app.Queue(path).claim("w", 1, 5)
        assert set(claim) == {"payload", "expires_at", "receipt"}
        assert claim["payload"] is None and isinstance(claim["receipt"], dict)
        assert two.ack(claim["receipt"], 2) is True
        assert app.Queue(path).claim("w", 9, 1) is None


def test_fifo_and_active_exclusion(app):
    with queues(app) as (one, two, _):
        for name in ("a", "b", "c"):
            one.put(name, name)
        first = one.claim("same", 0, 2)
        assert first["payload"] == "a"
        second = two.claim("same", 0, 8)
        assert second["payload"] == "b"
        third = one.claim("other", 1, 8)
        assert third["payload"] == "c"
        assert two.claim("same", 1, 1) is None
        renewed = two.claim("same", 2, 3)
        assert renewed["payload"] == "a"


def test_same_worker_stale_receipt(app):
    with queues(app) as (one, two, _):
        one.put("a", {})
        old = one.claim("same", 0, 2)
        new = two.claim("same", 2, 8)
        assert one.ack(old["receipt"], 3) is False
        assert two.claim("other", 3, 1) is None
        assert one.ack(new["receipt"], 4) is True
        assert two.ack(new["receipt"], 4) is False
        assert one.claim("same", 12, 3) is None


def test_other_worker_stale_receipt(app):
    with queues(app) as (one, two, _):
        one.put("a", [])
        old = one.claim("alice", 0, 2)
        new = two.claim("bob", 2, 5)
        assert one.ack(old["receipt"], 3) is False
        assert two.ack(new["receipt"], 3) is True


def test_expiry_and_fractional_duration(app):
    with queues(app) as (one, two, _):
        one.put("a", 0)
        claim = one.claim("w", 1.25, 0.5)
        assert claim["expires_at"] == 1.75
        assert two.claim("x", 1.5, 1) is None
        assert one.ack(claim["receipt"], 1.75) is False
        new = two.claim("x", 1.75, 0.25)
        assert new["payload"] == 0 and new["expires_at"] == 2.0
        assert one.ack(new["receipt"], 1.875) is True


def test_payload_isolation(app):
    with queues(app) as (one, two, _):
        original = {"items": [{"tags": ["initial"]}]}
        one.put("a", original)
        original["items"][0]["tags"].append("input edit")
        claim = one.claim("w", 0, 1)
        assert claim["payload"] == {"items": [{"tags": ["initial"]}]}
        claim["payload"]["items"][0]["tags"].append("output edit")
        another = two.claim("w", 1, 3)
        assert another["payload"] == {"items": [{"tags": ["initial"]}]}
        assert original["items"][0]["tags"] == ["initial", "input edit"]


def test_failed_put_and_duplicate_reuse(app):
    with queues(app) as (one, two, _):
        one.put("a", {"value": 1})
        too_deep = None
        for _ in range(10000):
            too_deep = [too_deep]
        for job_id, payload, errors in (("a", {"value": 2}, (ValueError,)),
                                        ("bad", {"set": {1}}, (TypeError, ValueError)),
                                        ("bad", float("nan"), (TypeError, ValueError)),
                                        ("bad", too_deep, (TypeError, ValueError, RecursionError))):
            try:
                two.put(job_id, payload)
            except errors:
                pass
            else:
                raise AssertionError("invalid put succeeded")
        one.put("bad", {})
        first = two.claim("w", 0, 5)
        assert first["payload"] == {"value": 1}
        assert one.ack(first["receipt"], 1) is True
        try:
            one.put("a", 3)
        except ValueError:
            pass
        else:
            raise AssertionError("completed IDs cannot be reused")
        assert two.claim("w", 2, 5)["payload"] == {}
        assert one.claim("w", 2, 5) is None


def test_invalid_duration_preserves_jobs(app):
    with queues(app) as (one, two, _):
        one.put("a", "unchanged")
        for duration in (0, -1, float("nan"), float("inf"), -float("inf")):
            try:
                one.claim("w", 0, duration)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid duration accepted")
        valid = two.claim("w", 0, 5)
        assert valid["payload"] == "unchanged" and valid["expires_at"] == 5
        assert one.ack(valid["receipt"], 1) is True


def test_atomic_claim_across_connections(app):
    with queues(app) as (one, two, _):
        one.put("only", "one")
        barrier = threading.Barrier(2)
        results, errors = [], []
        def work(queue, name):
            try:
                barrier.wait(timeout=5)
                results.append(queue.claim(name, 0, 5))
            except BaseException as exc:
                errors.append(repr(exc))
        threads = [threading.Thread(target=work, args=(one, "a")),
                   threading.Thread(target=work, args=(two, "b"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert not any(thread.is_alive() for thread in threads), "claim did not finish"
        assert not errors, errors
        assert sum(result is not None for result in results) == 1, results
        claimed = next(result for result in results if result is not None)
        assert two.ack(claimed["receipt"], 1) is True


TESTS = [test_empty_and_persistence, test_fifo_and_active_exclusion,
         test_same_worker_stale_receipt, test_other_worker_stale_receipt,
         test_expiry_and_fractional_duration, test_payload_isolation,
         test_failed_put_and_duplicate_reuse, test_invalid_duration_preserves_jobs,
         test_atomic_claim_across_connections]


def main():
    checks = []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            spec = importlib.util.spec_from_file_location("app", pathlib.Path(sys.argv[1]))
            app = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = app
            spec.loader.exec_module(app)
        import_error = None
    except BaseException:
        import_error = traceback.format_exc(limit=4)
    for test in TESTS:
        try:
            if import_error:
                raise AssertionError(import_error)
            with contextlib.redirect_stdout(io.StringIO()):
                test(app)
            checks.append({"test": test.__name__, "passed": True})
        except BaseException:
            checks.append({"test": test.__name__, "passed": False,
                           "error": traceback.format_exc(limit=4)})
    print(json.dumps({"passed": all(item["passed"] for item in checks),
                      "tests_run": len(checks), "checks": checks}))


if __name__ == "__main__":
    main()
