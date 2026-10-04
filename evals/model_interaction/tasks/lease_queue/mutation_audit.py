"""Maintainer-only sensitivity checks; finite mutations do not prove correctness."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile


MUTANTS = {
    "truncate_fractional_lease": [("expires_at = now + lease_seconds", "expires_at = now + int(lease_seconds)")],
    "drop_receipt_generation": [
        ("WHERE job_id = ? AND worker = ? AND generation = ?", "WHERE job_id = ? AND worker = ?"),
        ('(receipt["job_id"], receipt["worker"], receipt["generation"], now)',
         '(receipt["job_id"], receipt["worker"], now)')],
    "ack_expired_at_boundary": [("AND state = 'leased' AND expires_at > ?", "AND state = 'leased' AND expires_at >= ?")],
    "reclaim_after_boundary_only": [("state = 'leased' AND expires_at <= ?", "state = 'leased' AND expires_at < ?")],
    "reverse_queue_order": [("ORDER BY seq LIMIT 1", "ORDER BY seq DESC LIMIT 1")],
    "accept_duplicate_and_overwrite": [("INSERT INTO jobs(job_id, payload)", "INSERT OR REPLACE INTO jobs(job_id, payload)")],
    "completed_job_redelivered": [("(state = 'leased' AND expires_at <= ?)", "(expires_at <= ?)")],
    "accept_nonpositive_duration": [("not math.isfinite(lease_seconds) or lease_seconds <= 0", "not math.isfinite(lease_seconds)")],
    "renewed_delivery_changes_payload": [('"payload": json.loads(payload)', '"payload": json.loads(payload) if previous_generation == 0 else None')],
}


def main():
    root = Path(__file__).resolve().parent
    source = (root / "normal.py").read_text()
    meta = json.loads((root / "meta.json").read_text())
    results = []
    for name, edits in MUTANTS.items():
        mutated = source
        for old, new in edits:
            assert mutated.count(old) == 1, (name, "mutation target must occur exactly once")
            mutated = mutated.replace(old, new, 1)
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "app.py"
            candidate.write_text(mutated)
            process = subprocess.run([sys.executable, str(root / "acceptance.py"), str(candidate)],
                                     capture_output=True, text=True, timeout=15)
            assert process.returncode == 0, (name, process.stderr)
            result = json.loads(process.stdout)
            assert result["tests_run"] == meta["expected_checks"]
            assert {item["test"] for item in result["checks"]} == set(meta["test_names"])
            failed = [item["test"] for item in result["checks"] if not item["passed"]]
            results.append({"mutant": name, "detected": not result["passed"], "failed_checks": failed})
    report = {"family": root.name, "all_detected": all(item["detected"] for item in results),
              "mutants": results,
              "scope": "Authored negative controls; no exhaustive concurrency or crash-recovery claim."}
    print(json.dumps(report, indent=2))
    if not report["all_detected"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
