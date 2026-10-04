"""Executable positive/negative controls for the authored study inputs."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from .catalog import FAMILIES, TASKS, cases
from .grading import behavioral_check


class FixtureTests(unittest.TestCase):
    def test_normal_passes_and_both_defect_classes_fail(self):
        for case in cases():
            with self.subTest(case=case['case_id']):
                result = behavioral_check(case['family'], case['source'])
                self.assertFalse(result.get('isolation_failure'), result)
                self.assertEqual(result['tests_run'], case['expected_checks'], result)
                self.assertEqual({c['test'] for c in result['checks']}, set(case['test_names']))
                self.assertEqual(result['passed'], case['variant'] == 'normal', result)

    def test_public_smoke_does_not_reveal_the_hidden_defect(self):
        for case in cases():
            with self.subTest(case=case['case_id']), tempfile.TemporaryDirectory() as directory:
                stage = Path(directory)
                (stage / 'app.py').write_text(case['source'])
                (stage / 'smoke.py').write_text(case['smoke'])
                completed = subprocess.run([sys.executable, '-S', '-B', 'smoke.py'], cwd=stage,
                                           capture_output=True, text=True, timeout=10)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_request_is_same_for_all_variants_and_names_are_unique(self):
        all_cases = cases()
        self.assertEqual(len({c['case_id'] for c in all_cases}), 12)
        for family in FAMILIES:
            group = [c for c in all_cases if c['family'] == family]
            self.assertEqual({c['variant'] for c in group}, {'incident', 'normal', 'local'})
            self.assertEqual(len({c['request'] for c in group}), 1)
            self.assertEqual(len({c['smoke'] for c in group}), 1)

    def test_acceptance_supports_standard_dataclass_imports(self):
        prefix = 'from __future__ import annotations\nfrom dataclasses import dataclass\n@dataclass\nclass _ImportCompatibility:\n    value: int = 1\n'
        for family in FAMILIES:
            with self.subTest(family=family):
                source = (TASKS / family / 'normal.py').read_text()
                result = behavioral_check(family, prefix + source)
                self.assertTrue(result['passed'], result)


if __name__ == '__main__':
    unittest.main()
