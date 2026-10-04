"""Post-hoc development regressions, not changes to registered scores or labels.

Retains the frozen reference as a negative control and checks one completed
Astra A artifact as a positive control. No model calls or external services.
"""
import contextlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from .lease_reproduce import (
    FAMILY, ROOT, STUDY, check_in_range_expiry, check_payload_keys_preserved,
    check_payload_pair_preserved, check_surrogate_job_id, check_surrogate_worker,
    sha256,
)


COMPLETED_KEY = "f7bd5f14ab7b000407f6"
COMPLETED_SHA256 = "9387f3d3741c00dde388e19ca25fabf8504b07c791abb0ac786b1a89fcef8942"


@contextlib.contextmanager
def copied_module(source):
    """Run source and SQLite writes only from a fresh temporary directory."""
    with tempfile.TemporaryDirectory(prefix="lease-string-regression-") as directory:
        root = Path(directory)
        copied = root / "app.py"
        shutil.copyfile(source, copied)
        name = "lease_posthoc_under_test"
        previous = sys.modules.get(name)
        spec = importlib.util.spec_from_file_location(name, copied)
        app = importlib.util.module_from_spec(spec)
        sys.modules[name] = app
        try:
            spec.loader.exec_module(app)
            yield app, root
        finally:
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class LeaseStringFollowupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = FAMILY / "normal.py"
        cls.manifest = json.loads((STUDY / "manifest.json").read_text())
        expected = cls.manifest["files_sha256"][str(cls.original.relative_to(ROOT))]
        if sha256(cls.original) != expected:
            raise AssertionError("frozen normal source hash changed")
        result_root = STUDY / "results" / COMPLETED_KEY
        cls.repaired = result_root / "artifacts/app.py"
        record = json.loads((result_root / "record.json").read_text())
        if not (record["case_id"] == "m02" and record["trial"] == 1
                and record["model_requested"] == "gpt-6-astra"
                and record["condition"] == "A_tools"
                and record["completed"] is True and record["status"] == "completed"):
            raise AssertionError("positive control must be the completed m02 Astra A run")
        if not (sha256(cls.repaired) == record["artifacts"]["app.py"] == COMPLETED_SHA256):
            raise AssertionError("completed artifact source hash changed")

    def test_frozen_reference_rejects_nonempty_surrogate_id_and_worker(self):
        with copied_module(self.original) as (app, directory):
            for name, probe in (("job_id", check_surrogate_job_id),
                                ("worker", check_surrogate_worker)):
                with self.subTest(parameter=name):
                    with self.assertRaises(UnicodeEncodeError):
                        probe(app, directory / f"{name}.db")

    def test_frozen_reference_changes_pair_string_and_collapses_distinct_keys(self):
        with copied_module(self.original) as (app, directory):
            strings = check_payload_pair_preserved(app, directory / "strings.db")
            self.assertIs(strings["preserved"], False)
            self.assertEqual(strings["expected_codepoints"], [[0xD800, 0xDC00], [0x10000]])
            self.assertEqual(strings["observed_codepoints"], [[0x10000], [0x10000]])
            keys = check_payload_keys_preserved(app, directory / "keys.db")
            self.assertIs(keys["preserved"], False)
            self.assertEqual(keys["expected_keys"], 2)
            self.assertEqual(keys["observed_keys"], 1)
            self.assertEqual(keys["observed_entries"],
                             [{"key_codepoints": [0x10000], "value": "scalar"}])

    def test_legal_numeric_boundary_is_correct_in_reference_and_completed_fix(self):
        for name, source in (("reference", self.original), ("completed_fix", self.repaired)):
            with self.subTest(source=name), copied_module(source) as (app, directory):
                observed = check_in_range_expiry(app, directory / "expiry.db")
                self.assertEqual(observed["now"], 10**12 - 2)
                self.assertEqual(observed["expires_at"], 10**12 - 1)
                self.assertIs(observed["ack_at_expiry"], False)

    def test_completed_fix_preserves_all_four_observed_string_boundaries(self):
        with copied_module(self.repaired) as (app, directory):
            for name, probe in (("job_id", check_surrogate_job_id),
                                ("worker", check_surrogate_worker)):
                with self.subTest(parameter=name):
                    observed = probe(app, directory / f"{name}.db")
                    self.assertIs(observed["delivered_and_acknowledged"], True)
            strings = check_payload_pair_preserved(app, directory / "strings.db")
            self.assertIs(strings["preserved"], True)
            self.assertEqual(strings["observed_codepoints"], strings["expected_codepoints"])
            keys = check_payload_keys_preserved(app, directory / "keys.db")
            self.assertIs(keys["preserved"], True)
            self.assertEqual(keys["observed_keys"], 2)
            self.assertEqual(keys["observed_entries"], keys["expected_entries"])

    def test_completed_fix_passes_all_nine_unchanged_original_checks(self):
        acceptance = FAMILY / "acceptance.py"
        expected_digest = self.manifest["files_sha256"][str(acceptance.relative_to(ROOT))]
        self.assertEqual(sha256(acceptance), expected_digest)
        meta = json.loads((FAMILY / "meta.json").read_text())
        with tempfile.TemporaryDirectory(prefix="lease-original-acceptance-") as directory:
            copied = Path(directory) / "app.py"
            shutil.copyfile(self.repaired, copied)
            process = subprocess.run(
                [sys.executable, "-B", "-S", str(acceptance), str(copied)],
                cwd=directory, capture_output=True, text=True, timeout=15,
            )
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertIs(result["passed"], True, result)
        self.assertEqual(result["tests_run"], 9)
        self.assertEqual(len(result["checks"]), 9)
        self.assertEqual({item["test"] for item in result["checks"]}, set(meta["test_names"]))
        self.assertTrue(all(item["passed"] is True for item in result["checks"]))


if __name__ == "__main__":
    unittest.main()
