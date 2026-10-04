"""Local regression checks for the task.md protocol contract."""
import json
import unittest

import app


def frame(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


class ContractTests(unittest.TestCase):
    def assert_failed(self, parser, operation):
        with self.assertRaises(app.StreamError):
            operation()
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)
        for retry in (lambda: parser.feed(b""),
                      lambda: parser.feed(b'{"event":"done"}\n'),
                      parser.finish):
            with self.assertRaises(app.StreamError):
                retry()
            self.assertEqual(parser.state, "FAILED")
            self.assertEqual(parser.buffered_bytes, 0)

    def test_metrics(self):
        for text, characters, words in [
            ("", 0, 0), (" \t\n\r\u2003\u00a0", 6, 0),
            ("你好\u2003世界\u00a0x", 7, 3), ("e\u0301🙂", 3, 1),
        ]:
            self.assertEqual(app.text_metrics(text),
                             {"characters": characters, "words": words})

    def test_all_two_cut_partitions(self):
        records = [
            {"event": "token", "text": "你🙂"},
            {"event": "token", "text": ""},
            {"event": "token", "text": "\n\u2003a"},
            {"event": "done"},
        ]
        wire = b"".join(map(frame, records))
        expected = records[:-1] + [
            {"event": "done", "text": "你🙂\n\u2003a", "characters": 5, "words": 2}
        ]
        for first in range(len(wire) + 1):
            for second in range(first, len(wire) + 1):
                parser = app.EventStream()
                events = []
                for chunk in (wire[:first], wire[first:second], wire[second:]):
                    events.extend(parser.feed(chunk))
                self.assertEqual(events, expected)
                self.assertEqual(parser.state, "DONE")
                self.assertEqual(parser.buffered_bytes, 0)
                self.assertEqual(parser.finish(), [])
                self.assertEqual(parser.finish(), [])
                self.assertEqual(parser.feed(b""), [])

    def test_bytewise_buffer_count(self):
        wire = frame({"event": "token", "text": "你🙂\n"})
        parser = app.EventStream(len(wire) - 1)
        for index, byte in enumerate(wire[:-1], 1):
            self.assertEqual(parser.feed(bytes([byte])), [])
            self.assertEqual(parser.buffered_bytes, index)
        self.assertEqual(parser.feed(b"\n"), [{"event": "token", "text": "你🙂\n"}])
        self.assertEqual(parser.buffered_bytes, 0)
        self.assertEqual(parser.state, "OPEN")

    def test_frame_limits(self):
        wire = frame({"event": "token", "text": "你好"})
        limit = len(wire) - 1
        for cut in range(len(wire) + 1):
            parser = app.EventStream(limit)
            self.assertEqual(parser.feed(wire[:cut]) + parser.feed(wire[cut:]),
                             [{"event": "token", "text": "你好"}])
        for cut in range(limit):
            parser = app.EventStream(limit - 1)
            parser.feed(wire[:cut])
            self.assert_failed(parser, lambda: parser.feed(wire[cut:]))
        parser = app.EventStream(limit)
        self.assertEqual(parser.feed(wire[:-1]), [])
        self.assert_failed(parser, lambda: parser.feed(b" "))
        parser = app.EventStream(limit)
        self.assertEqual(len(parser.feed(wire * 1000)), 1000)
        done = parser.feed(frame({"event": "done"}))[0]
        self.assertEqual(done["text"], "你好" * 1000)

    def test_invalid_frames(self):
        malformed = [b"\n", b" \n", b"{\n", b"{}{}\n", b"[]\n",
                     b"null\n", b"42\n", b'"token"\n',
                     b'{"event":"token","text":"\xff"}\n',
                     b'{"event":"token","text":"\xc3"}\n',
                     b'{"event":"token","text":"literal\nnewline"}\n']
        schemas = [
            {}, {"event": "unknown"}, {"event": []}, {"event": "token"},
            {"event": "token", "text": None}, {"event": "token", "text": 1},
            {"event": "token", "text": []},
            {"event": "token", "text": "", "extra": True},
            {"event": "done", "text": ""}, {"event": "error"},
            {"event": "error", "message": False},
            {"event": "error", "message": "", "extra": 0},
        ]
        for wire in malformed + list(map(frame, schemas)):
            with self.subTest(wire=wire):
                parser = app.EventStream()
                self.assert_failed(parser, lambda: parser.feed(wire))

    def test_terminal_behavior(self):
        for terminal in ({"event": "done"}, {"event": "error", "message": "故障"},
                         {"event": "error", "message": ""}):
            wire = frame(terminal)
            parser = app.EventStream()
            self.assertEqual(parser.state, "OPEN")
            self.assertEqual(parser.feed(b""), [])
            result = parser.feed(wire)
            expected = (dict(terminal, text="", characters=0, words=0)
                        if terminal["event"] == "done" else terminal)
            self.assertEqual(result, [expected])
            self.assertEqual(parser.state, terminal["event"].upper())
            self.assertEqual(parser.buffered_bytes, 0)
            for _ in range(2):
                self.assertEqual(parser.finish(), [])
                self.assertEqual(parser.feed(b""), [])
            for suffix in (b"\n", b" ", b"x", wire):
                for same_call in (True, False):
                    parser = app.EventStream()
                    if same_call:
                        self.assert_failed(parser, lambda: parser.feed(wire + suffix))
                    else:
                        parser.feed(wire)
                        self.assert_failed(parser, lambda: parser.feed(suffix))

    def test_eof(self):
        for pending in (b"", b"{", b'{"event":"done"}',
                        frame({"event": "token", "text": "already returned"})):
            parser = app.EventStream()
            parser.feed(pending)
            self.assert_failed(parser, parser.finish)

    def test_prior_events_and_error(self):
        parser = app.EventStream()
        prior = parser.feed(frame({"event": "token", "text": "a"}))
        self.assert_failed(parser, lambda: parser.feed(
            frame({"event": "token", "text": "b"}) + b"invalid\n"))
        self.assertEqual(prior, [{"event": "token", "text": "a"}])
        parser = app.EventStream()
        self.assertEqual(parser.feed(frame({"event": "token", "text": "a"}) +
                                     frame({"event": "error", "message": "stop"})),
                         [{"event": "token", "text": "a"},
                          {"event": "error", "message": "stop"}])
        self.assertEqual(parser.finish(), [])


if __name__ == "__main__":
    unittest.main()
