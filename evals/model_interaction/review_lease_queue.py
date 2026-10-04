"""Independent finite trace review of the lease queue's normal reference.

The state model uses only the public contract and does not read implementation
fields or the SQLite database. This is a reproducible pre-freeze check, not an
additional independent model-evaluation task or a proof of complete correctness.
"""
import argparse
from collections import Counter
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import random
import sys
import tempfile

SEED = 812
STEPS = 300
DEFAULT_SOURCE = Path(__file__).resolve().parent / "tasks/lease_queue/normal.py"


def review(source):
    result = {"seed": SEED, "steps": STEPS, "steps_run": 0, "passed": False}
    counts = Counter()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            spec = importlib.util.spec_from_file_location("app", source)
            app = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = app
            spec.loader.exec_module(app)
            rng = random.Random(SEED)
            with tempfile.TemporaryDirectory(prefix="lease-reference-review-") as directory:
                path = Path(directory) / "jobs.db"
                queue = app.Queue(path)
                jobs, receipts = [], []
                now = 0.0
                for step in range(STEPS):
                    result["steps_run"] = step + 1
                    now += rng.choice([0, 0, .125, .5, 2])
                    action = rng.choice(["put", "claim", "claim", "ack"])
                    if action == "put":
                        identifier = "job-" + str(len(jobs))
                        payload = {"id": identifier, "data": [len(jobs)]}
                        queue.put(identifier, payload)
                        jobs.append({"payload": payload, "state": "ready", "expires": None, "generation": 0})
                        counts["put"] += 1
                    elif action == "claim":
                        duration = rng.choice([.125, .5, 1, 3])
                        worker = rng.choice(["a", "a", "b"])
                        actual = app.Queue(path).claim(worker, now, duration)
                        eligible = next((job for job in jobs if job["state"] == "ready" or
                                         job["state"] == "leased" and job["expires"] <= now), None)
                        if eligible is None:
                            assert actual is None, f"step {step}: claimed an ineligible job"
                        else:
                            assert actual["payload"] == eligible["payload"], f"step {step}: wrong eligible job"
                            assert actual["expires_at"] == now + duration, f"step {step}: wrong expiration"
                            eligible.update(state="leased", expires=now + duration,
                                            generation=eligible["generation"] + 1)
                            receipts.append((actual["receipt"], eligible, eligible["generation"]))
                        counts["claim"] += 1
                    elif receipts:
                        receipt, job, generation = rng.choice(receipts)
                        expected = (job["state"] == "leased" and job["expires"] > now
                                    and job["generation"] == generation)
                        assert app.Queue(path).ack(receipt, now) is expected, f"step {step}: wrong receipt result"
                        if expected:
                            job["state"] = "done"
                        counts["ack"] += 1
                    else:
                        counts["ack_without_receipt_noop"] += 1
        result["passed"] = True
    except BaseException as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)
    result["operation_counts"] = dict(counts)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    result = review(args.source.resolve())
    print(json.dumps(result, sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
