import concurrent.futures
import math
import tempfile
from pathlib import Path

import app


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "queue.db"
    q1 = app.Queue(path)
    q2 = app.Queue(path)

    # Payload is snapshotted; returned payloads cannot alter future deliveries.
    original = {"nested": [1, {"x": True}]}
    q1.put("snapshot", original)
    original["nested"][1]["x"] = False
    first = q1.claim("worker", 0, 5)
    assert first["payload"] == {"nested": [1, {"x": True}]}
    first["payload"]["nested"].clear()
    assert q2.ack(first["receipt"], 1) is True
    assert q1.ack(first["receipt"], 1) is False
    try:
        q2.put("snapshot", None)
    except ValueError:
        pass
    else:
        raise AssertionError("completed ID was reusable")

    # Exact expiry boundary and a time value whose REAL representation loses
    # the distinction between adjacent integers.
    base = 10**12
    q1.put("precision", {"ok": []})
    precise = q1.claim("same-worker", base, 1)
    assert precise["expires_at"] == base + 1
    assert q2.claim("same-worker", base + 1 - 1, 2) is None
    assert q2.ack(precise["receipt"], base + 1) is False
    reclaimed = q2.claim("same-worker", base + 1, 2)
    assert reclaimed["expires_at"] == base + 3
    assert q1.ack(precise["receipt"], base + 1) is False
    assert q1.ack(reclaimed["receipt"], base + 2) is True

    # Invalid encodings must not insert a row or disturb a valid job.
    q1.put("stays", "valid")
    invalid = [math.nan, math.inf, object()]
    for index, payload in enumerate(invalid):
        try:
            q1.put(f"invalid-{index}", payload)
        except (TypeError, ValueError, RecursionError):
            pass
        else:
            raise AssertionError("unsupported payload unexpectedly encoded")
    deep = None
    for _ in range(10000):
        deep = [deep]
    try:
        q1.put("too-deep", deep)
    except (TypeError, ValueError, RecursionError):
        pass
    else:
        raise AssertionError("over-depth payload unexpectedly encoded")
    assert q1.claim("w", 0, 1)["payload"] == "valid"
    for bad_duration in (0, -1, True, math.inf, math.nan):
        try:
            q1.claim("w", 0, bad_duration)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid lease {bad_duration!r}")

    # Independent connections cannot issue duplicate active claims.
    for index in range(20):
        q1.put(f"concurrent-{index}", index)
    def claim_one(index):
        queue = app.Queue(path)
        return queue.claim(f"worker-{index}", 100, 50)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        claims = list(pool.map(claim_one, range(20)))
    assert all(claim is not None for claim in claims)
    ids = [claim["receipt"]["job_id"] for claim in claims]
    assert len(ids) == len(set(ids)) == 20

print("queue verification passed")
