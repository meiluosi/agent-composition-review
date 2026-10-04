"""Finite post-hoc regression and positive controls, without model calls."""
import hashlib
import json
import subprocess
import sys
import unittest

from .reproduce import CONSTANTS, HERE, ROOT, RUN, masked_constant, original_api, revised_api


class StreamJSONFollowupTests(unittest.TestCase):
    def test_frozen_reference_exhibits_masked_constant_counterexample(self):
        api = original_api()
        for value in CONSTANTS:
            with self.subTest(value=value):
                parser = api.EventStream()
                result = parser.feed(masked_constant(value))
                self.assertEqual(result, [{"event": "done", "text": "", "characters": 0, "words": 0}])
                self.assertEqual(parser.state, "DONE")
                self.assertEqual(parser.finish(), [])

    def test_revision_rejects_constants_and_preserves_failed_state(self):
        api = revised_api()
        for value in CONSTANTS:
            with self.subTest(value=value):
                parser = api.EventStream()
                wire = masked_constant(value)
                self.assertEqual(parser.feed(wire[:10]), [])
                with self.assertRaises(api.StreamError):
                    parser.feed(wire[10:])
                self.assertEqual(parser.state, "FAILED")
                self.assertEqual(parser.buffered_bytes, 0)
                for operation in (lambda: parser.feed(b""), parser.finish):
                    with self.assertRaises(api.StreamError):
                        operation()

    def test_valid_frames_and_constant_names_inside_strings(self):
        api = revised_api()
        parser = api.EventStream()
        text = "雪 NaN Infinity -Infinity"
        wire = json.dumps({"event": "token", "text": text}, ensure_ascii=False).encode() + b"\n"
        output = []
        for byte in wire:
            output.extend(parser.feed(bytes([byte])))
        output.extend(parser.feed(b'{"event":"done"}\n'))
        self.assertEqual(output, [{"event": "token", "text": text},
                                 {"event": "done", "text": text, "characters": len(text), "words": 4}])
        self.assertEqual(parser.finish(), [])

    def test_revision_does_not_introduce_a_duplicate_name_ban(self):
        for api in (original_api(), revised_api()):
            parser = api.EventStream()
            self.assertEqual(parser.feed(b'{"event":"token","text":"first","text":"last"}\n'),
                             [{"event": "token", "text": "last"}])
            self.assertEqual(parser.feed(b'{"event":"done","event":"done"}\n'),
                             [{"event": "done", "text": "last", "characters": 4, "words": 1}])
            self.assertEqual(parser.finish(), [])

    def test_revision_passes_unchanged_frozen_acceptance(self):
        acceptance = ROOT / "evals/model_interaction/tasks/stream_events/acceptance.py"
        manifest = json.loads((RUN / "manifest.json").read_text())
        expected_digest = manifest["files_sha256"][str(acceptance.relative_to(ROOT))]
        self.assertEqual(hashlib.sha256(acceptance.read_bytes()).hexdigest(), expected_digest)
        process = subprocess.run([sys.executable, "-B", "-S", str(acceptance), str(HERE / "stream_reference.py")],
                                 capture_output=True, text=True, timeout=5)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["tests_run"], 12)
        self.assertEqual(len(result["checks"]), 12)
        self.assertTrue(all(item["passed"] is True for item in result["checks"]))


if __name__ == "__main__":
    unittest.main()
