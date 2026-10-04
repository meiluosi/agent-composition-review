"""Post-freeze lease-queue probes; these do not alter the registered scorer.

Uses only temporary SQLite files and copies of saved source. No model calls.
Default: frozen reference plus the six m02/trial-1 artifacts, including the
explicitly labelled incomplete Astra C artifact. --app probes one source.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
FAMILY = ROOT / "evals/model_interaction/tasks/lease_queue"
STUDY = ROOT / "evals/iterations/model-interaction-2026-10-04"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_surrogate_job_id(app, path):
    queue = app.Queue(path)
    queue.put("\ud800", "kept")
    claim = app.Queue(path).claim("worker", 0, 1)
    assert claim["payload"] == "kept"
    assert queue.ack(claim["receipt"], 0.5) is True
    return {"nonempty_python_string": True, "delivered_and_acknowledged": True}


def check_surrogate_worker(app, path):
    queue = app.Queue(path)
    queue.put("job", "kept")
    claim = queue.claim("\ud800", 0, 1)
    assert claim["payload"] == "kept"
    assert app.Queue(path).ack(claim["receipt"], 0.5) is True
    return {"nonempty_python_string": True, "delivered_and_acknowledged": True}


def check_payload_pair_preserved(app, path):
    payload = ["\ud800\udc00", "\U00010000"]
    json.dumps(payload, allow_nan=False)
    queue = app.Queue(path)
    queue.put("job", payload)
    observed = queue.claim("worker", 0, 1)["payload"]
    detail = {"expected_codepoints": [[ord(char) for char in value] for value in payload],
              "observed_codepoints": [[ord(char) for char in value] for value in observed],
              "expected_string_lengths": [len(value) for value in payload],
              "observed_string_lengths": [len(value) for value in observed]}
    return {"preserved": observed == payload, **detail}


def check_payload_keys_preserved(app, path):
    payload = {"\ud800\udc00": "pair", "\U00010000": "scalar"}
    json.dumps(payload, allow_nan=False)
    queue = app.Queue(path)
    queue.put("job", payload)
    observed = queue.claim("worker", 0, 1)["payload"]
    return {"preserved": observed == payload,
            "expected_entries": [{"key_codepoints": [ord(char) for char in key], "value": value}
                                 for key, value in payload.items()],
            "observed_entries": [{"key_codepoints": [ord(char) for char in key], "value": value}
                                 for key, value in observed.items()],
            "expected_keys": len(payload), "observed_keys": len(observed)}


def check_in_range_expiry(app, path):
    # All now values stay inside the frozen absolute-value limit of 10**12.
    now, duration = 10**12 - 2, 1
    queue = app.Queue(path)
    queue.put("job", None)
    claim = queue.claim("worker", now, duration)
    at_expiry = queue.ack(claim["receipt"], now + duration)
    assert claim["expires_at"] == now + duration
    assert at_expiry is False
    return {"now": now, "expires_at": claim["expires_at"], "ack_at_expiry": at_expiry}


def observe_bool_duration(app, path):
    # Caller promises a non-bool int/float. This is explicitly NOT a score.
    queue = app.Queue(path)
    queue.put("job", None)
    try:
        claim = queue.claim("worker", 0, True)
    except Exception as exc:
        return {"outcome": "rejected", "exception": type(exc).__name__}
    return {"outcome": "accepted", "expires_at": claim["expires_at"]}


def observe_out_of_range_expiry(app, path):
    # This reproduces REAL rounding, but 2**53+2 exceeds the task's 10**12 cap.
    now = 2**53 + 2
    queue = app.Queue(path)
    queue.put("job", None)
    claim = queue.claim("worker", now, 1)
    return {"now": now, "expires_at": claim["expires_at"],
            "ack_at_expiry": queue.ack(claim["receipt"], now + 1)}


PROBES = [
    ("surrogate_job_id", "unqualified_nonempty_str_contract", check_surrogate_job_id),
    ("surrogate_worker", "unqualified_nonempty_str_contract", check_surrogate_worker),
    ("payload_pair_preserved", "literal_python_string_preservation", check_payload_pair_preserved),
    ("payload_keys_preserved", "literal_python_string_preservation", check_payload_keys_preserved),
    ("in_range_expiry", "within_frozen_numeric_domain", check_in_range_expiry),
    ("bool_duration", "outside_caller_precondition_observation_only", observe_bool_duration),
    ("large_integer_expiry", "outside_frozen_numeric_domain_observation_only", observe_out_of_range_expiry),
]


def sources():
    yield {"label": "frozen_normal", "path": FAMILY / "normal.py"}
    selected = []
    for record_path in sorted((STUDY / "results").glob("*/record.json")):
        record = json.loads(record_path.read_text())
        if record.get("case_id") != "m02" or record.get("trial") != 1:
            continue
        app_path = record_path.parent / "artifacts/app.py"
        if not app_path.is_file():
            continue
        expected = record["artifacts"]["app.py"]
        assert sha256(app_path) == expected, "saved app differs from its record hash"
        selected.append({"label": record["model_requested"] + "/" + record["condition"],
                         "path": app_path, "record": str(record_path.relative_to(ROOT)),
                         "completed": record["completed"], "status": record["status"],
                         "original_acceptance_passed": record["acceptance"]["passed"]})
    yield from sorted(selected, key=lambda item: item["label"])


def probe_source(item):
    result = {key: value for key, value in item.items() if key != "path"}
    result["source"] = str(item["path"].relative_to(ROOT)) if item["path"].is_relative_to(ROOT) else str(item["path"])
    result["source_sha256"] = sha256(item["path"])
    checks = []
    with tempfile.TemporaryDirectory(prefix="lease-postfreeze-") as directory:
        temporary = Path(directory)
        copied = temporary / "app.py"
        shutil.copyfile(item["path"], copied)
        spec = importlib.util.spec_from_file_location("app", copied)
        app = importlib.util.module_from_spec(spec)
        sys.modules["app"] = app
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(app)
        for index, (name, scope, check) in enumerate(PROBES):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    detail = check(app, temporary / f"probe-{index}.db")
                checks.append({"probe": name, "scope": scope, "ran": True, **detail})
            except Exception as exc:
                checks.append({"probe": name, "scope": scope, "ran": True,
                               "exception": type(exc).__name__, "error": str(exc)})
    result["probes"] = checks
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, help="Probe just this app source instead of recorded m02 artifacts")
    args = parser.parse_args()
    selected = [{"label": "specified_source", "path": args.app.resolve()}] if args.app else list(sources())
    report = {"kind": "post_freeze_development_observations_not_registered_scores",
              "request_sha256": sha256(FAMILY / "request.md"),
              "reference_sha256": sha256(FAMILY / "normal.py"),
              "results": [probe_source(item) for item in selected]}
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
