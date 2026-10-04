"""Local contract regressions; run with ./run_python -m unittest -v test_app."""
import json
import unittest

import app


def frame(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


class EventStreamTests(unittest.TestCase):
    def assert_failed(self, parser, action):
        with self.assertRaises(app.StreamError):
            action()
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)
        for action in (lambda: parser.feed(b""), lambda: parser.feed(b"x"), parser.finish):
            with self.assertRaises(app.StreamError):
                action()
            self.assertEqual(parser.state, "FAILED")
            self.assertEqual(parser.buffered_bytes, 0)

    def test_metrics_and_fragments(self):
        text = "  你🙂\tworld\u2003again\r\nend  "
        expected_metrics = {"characters": len(text), "words": 4}
        self.assertEqual(app.text_metrics(text), expected_metrics)
        self.assertEqual(app.text_metrics(""), {"characters": 0, "words": 0})
        parser = app.EventStream()
        for fragment in ("", text[:3], text[3:6], "", text[6:]):
            token = {"event": "token", "text": fragment}
            self.assertEqual(parser.feed(frame(token)), [token])
        self.assertEqual(parser.feed(frame({"event": "done"})), [
            {"event": "done", "text": text, **expected_metrics}
        ])

    def test_every_pair_of_chunk_boundaries(self):
        token = {"event": "token", "text": "你🙂\n a"}
        wire = frame(token) + frame({"event": "done"})
        expected = [token, {"event": "done", "text": token["text"], "characters": 5, "words": 2}]
        for first in range(len(wire) + 1):
            for second in range(first, len(wire) + 1):
                parser = app.EventStream()
                events = []
                for chunk in (wire[:first], wire[first:second], wire[second:]):
                    events.extend(parser.feed(chunk))
                self.assertEqual(events, expected, (first, second))
                self.assertEqual(parser.state, "DONE")
                self.assertEqual(parser.buffered_bytes, 0)
                self.assertEqual(parser.finish(), [])

    def test_byte_at_a_time_and_buffer_count(self):
        token = {"event": "token", "text": "汉🙂"}
        wire = frame(token)
        parser = app.EventStream()
        self.assertEqual(parser.state, "OPEN")
        for index, byte in enumerate(wire[:-1], 1):
            self.assertEqual(parser.feed(bytes([byte])), [])
            self.assertEqual(parser.buffered_bytes, index)
            self.assertEqual(parser.feed(b""), [])
        self.assertEqual(parser.feed(b"\n"), [token])
        self.assertEqual(parser.buffered_bytes, 0)
        error = {"event": "error", "message": "错误🙂"}
        events = []
        for byte in frame(error):
            events.extend(parser.feed(bytes([byte])))
        self.assertEqual(events, [error])
        self.assertEqual(parser.state, "ERROR")

    def test_terminal_lifecycle(self):
        for terminal in ({"event": "done"}, {"event": "error", "message": ""}):
            parser = app.EventStream()
            events = parser.feed(frame(terminal))
            self.assertEqual(len(events), 1)
            self.assertEqual(parser.state, terminal["event"].upper())
            self.assertEqual(parser.buffered_bytes, 0)
            for _ in range(3):
                self.assertEqual(parser.feed(b""), [])
                self.assertEqual(parser.finish(), [])
            self.assert_failed(parser, lambda: parser.feed(b"x"))

    def test_any_bytes_after_terminal_in_same_feed(self):
        suffixes = (b"x", b"\n", b" ", b"\xff", frame({"event": "done"}),
                    frame({"event": "token", "text": "late"}),
                    frame({"event": "error", "message": "late"}))
        for terminal in ({"event": "done"}, {"event": "error", "message": "stop"}):
            for suffix in suffixes:
                with self.subTest(terminal=terminal, suffix=suffix):
                    parser = app.EventStream()
                    self.assert_failed(parser, lambda: parser.feed(frame(terminal) + suffix))

    def test_frame_limits(self):
        token = {"event": "token", "text": "你🙂"}
        wire = frame(token)
        limit = len(wire) - 1
        for split in range(len(wire) + 1):
            parser = app.EventStream(limit)
            self.assertEqual(parser.feed(wire[:split]) + parser.feed(wire[split:]), [token])
            self.assertEqual(parser.buffered_bytes, 0)
        parser = app.EventStream(limit)
        self.assertEqual(parser.feed(wire[:-1]), [])
        self.assertEqual(parser.buffered_bytes, limit)
        self.assertEqual(parser.feed(b"\n"), [token])
        for chunks in ((wire,), (wire[:-1],), (wire[:5], wire[5:])):
            parser = app.EventStream(limit - 1)
            for chunk in chunks[:-1]:
                self.assertEqual(parser.feed(chunk), [])
            self.assert_failed(parser, lambda: parser.feed(chunks[-1]))
        parser = app.EventStream(1)
        self.assertEqual(parser.feed(b"{"), [])
        self.assert_failed(parser, lambda: parser.feed(b"x"))

    def test_large_chunk_of_small_frames(self):
        token = {"event": "token", "text": "a"}
        wire = frame(token)
        parser = app.EventStream(len(wire) - 1)
        result = parser.feed(wire * 1000 + frame({"event": "done"}))
        self.assertEqual(result[:-1], [token] * 1000)
        self.assertEqual(result[-1], {"event": "done", "text": "a" * 1000, "characters": 1000, "words": 1})
        self.assertEqual(parser.buffered_bytes, 0)

    def test_invalid_frames_and_schema(self):
        invalid = [b"\n", b" \n", b"{\n", b"{}{}\n", b"[]\n", b"null\n",
                   b'{"event":"token","text":"\xff"}\n',
                   b'{"event":"token","text":"\xe4"}\n',
                   b'{"event":"token","text":"literal\nnewline"}\n']
        invalid.extend(frame(value) for value in (
            {}, {"event": "unknown"}, {"event": "token"},
            {"event": "token", "text": None}, {"event": "token", "text": 1},
            {"event": "token", "text": []}, {"event": "token", "text": "", "extra": 1},
            {"event": "done", "text": ""}, {"event": "error"},
            {"event": "error", "message": False}, {"event": "error", "message": "", "extra": 1},
            {"event": []}, {"event": {}},
        ))
        for wire in invalid:
            with self.subTest(wire=wire):
                parser = app.EventStream()
                self.assert_failed(parser, lambda: parser.feed(wire))

    def test_premature_eof(self):
        for wire in (b"", b"{", b'{"event":"done"}', frame({"event": "token", "text": "ok"}),
                     b'{"event":"token","text":"\xe4'):
            parser = app.EventStream()
            parser.feed(wire)
            self.assert_failed(parser, parser.finish)

    def test_failure_returns_no_partial_list_and_preserves_prior_events(self):
        parser = app.EventStream()
        token = {"event": "token", "text": "prior"}
        previous = parser.feed(frame(token))
        sentinel = object()
        result = sentinel
        with self.assertRaises(app.StreamError):
            result = parser.feed(frame({"event": "token", "text": "unreturned"}) + b"invalid\n")
        self.assertIs(result, sentinel)
        self.assertEqual(previous, [token])
        self.assert_failed(parser, parser.finish)


if __name__ == "__main__":
    unittest.main()
