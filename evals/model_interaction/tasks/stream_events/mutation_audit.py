"""Reproduce maintainer-side negative controls, without model calls."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

MUTANTS = {'unhandled_deep_json': ['except (UnicodeDecodeError, ValueError, RecursionError):', 'except (UnicodeDecodeError, ValueError):'], 'chunk_utf8_validation': ['        output = []\n', '        data = data.decode("utf-8").encode("utf-8")\n        output = []\n'], 'allow_same_call_terminal_trailing_bytes': ['            if self.state != "OPEN" and start < len(data):\n                self._fail("data after terminal event")\n', ''], 'remove_frame_bound': ['            if len(self._buffer) + len(part) > self.max_buffer:\n                self._fail("frame exceeds max_buffer")\n', ''], 'limit_incoming_chunk_instead': ['        output = []\n', '        if len(data) > self.max_buffer:\n            self._fail("chunk too large")\n        output = []\n'], 'reject_equal_frame_bound': ['len(self._buffer) + len(part) > self.max_buffer', 'len(self._buffer) + len(part) >= self.max_buffer'], 'accept_eof_without_terminal': ['        self._fail("EOF before a complete terminal event")', '        return []'], 'error_does_not_terminate': ['            self.state = "ERROR"', '            self.state = "OPEN"'], 'ascii_space_word_count': ['len(text.split())', 'len(text.split(" "))']}


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
