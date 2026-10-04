"""Reproduce maintainer-side negative controls, without model calls."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

MUTANTS = {'older_version_overwrites': ['                if item["version"] < previous["version"]:\n                    continue\n', ''], 'non_atomic_ingest': ['staged = deepcopy(self._latest)', 'staged = self._latest'], 'retained_spec_alias': ['self._specs = deepcopy(call_specs)', 'self._specs = call_specs'], 'retained_update_alias': ['staged[key] = deepcopy(item)', 'staged[key] = item'], 'sorted_instead_of_declared_order': ['for spec in self._specs:', 'for spec in sorted(self._specs, key=lambda item: item["call_id"]):'], 'allow_boolean_samples': ['type(v) is not int', 'not isinstance(v, int)'], 'accept_empty_ingest_after_finalization': ['    def ingest(self, updates):\n', '    def ingest(self, updates):\n        if updates == []:\n            return\n'], 'ignore_current_version_conflicts': ['                    if item != previous:\n                        raise ValueError("conflicting result at same version")\n', ''], 'finalize_incomplete_batches': ['        if len(self._latest) != len(self._specs):\n            raise ValueError("results are incomplete")\n', ''], 'absolute_value_score': ['return sum(values) * weight', 'return sum(abs(value) for value in values) * weight']}


def main():
    root = Path(__file__).resolve().parent
    source = (root / "normal.py").read_text()
    results = []
    for name, (old, new) in MUTANTS.items():
        assert source.count(old) == 1, (name, "mutation target must occur once")
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "app.py"
            candidate.write_text(source.replace(old, new, 1))
            process = subprocess.run([sys.executable, str(root / "acceptance.py"), str(candidate)],
                                     capture_output=True, text=True, timeout=3)
            assert process.returncode == 0, (name, process.stderr)
            result = json.loads(process.stdout)
            assert result["tests_run"] == 12 and len(result["checks"]) == 12
            failed = [item["test"] for item in result["checks"] if not item["passed"]]
            results.append({"mutant": name, "detected": not result["passed"], "failed_checks": failed})
    report = {"family": root.name, "all_detected": all(item["detected"] for item in results),
              "mutants": results,
              "scope": "Authored negative controls; detecting these mutations does not establish complete correctness."}
    print(json.dumps(report, indent=2))
    if not report["all_detected"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
