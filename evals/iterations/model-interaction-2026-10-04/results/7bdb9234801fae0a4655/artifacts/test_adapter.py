"""Contract regression tests; STREAM_MODULE=original tests the supplied copy."""
import importlib
import json
import os
import random
import unittest


adapter = importlib.import_module(os.environ.get("STREAM_MODULE", "app"))


def frame(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


TOKEN = frame({"event": "token", "text": "hello"})
DONE = frame({"event": "done"})
ERROR = frame({"event": "error", "message": "stopped"})


class ContractTests(unittest.TestCase):
    def assert_failed(self, parser, action):
        with self.assertRaises(adapter.StreamError):
            action()
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)
        for retry in (lambda: parser.feed(b""), lambda: parser.feed(DONE), parser.finish):
            with self.assertRaises(adapter.StreamError):
                retry()
            self.assertEqual(parser.state, "FAILED")
            self.assertEqual(parser.buffered_bytes, 0)

    def test_public_api_and_metrics(self):
        self.assertTrue(issubclass(adapter.StreamError, ValueError))
        parser = adapter.EventStream()
        self.assertEqual(parser.max_buffer, 4096)
        self.assertEqual(parser.state, "OPEN")
        self.assertEqual(parser.buffered_bytes, 0)
        self.assertEqual(parser.feed(b""), [])
        for text in ("", "hello", " \t\n", "中 文\u2003hello\u00a0world", "e\u0301🙂"):
            self.assertEqual(adapter.text_metrics(text), {"characters": len(text), "words": len(text.split())})

    def test_every_two_chunk_boundary(self):
        fragments = ["", "你好🙂", " \talpha\n", "beta\u2003gamma", "e\u0301"]
        records = [{"event": "token", "text": text} for text in fragments]
        text = "".join(fragments)
        for terminal in ({"event": "done"}, {"event": "error", "message": "错误🙂"}):
            wire = b"".join(map(frame, records + [terminal]))
            last = terminal if terminal["event"] == "error" else {
                "event": "done", "text": text, "characters": len(text), "words": len(text.split())
            }
            for split in range(len(wire) + 1):
                with self.subTest(terminal=terminal, split=split):
                    parser = adapter.EventStream()
                    events = parser.feed(wire[:split])
                    self.assertEqual(parser.buffered_bytes, len(wire[:split].rsplit(b"\n", 1)[-1]))
                    events += parser.feed(wire[split:])
                    self.assertEqual(events, records + [last])
                    self.assertEqual(parser.state, terminal["event"].upper())
                    self.assertEqual(parser.buffered_bytes, 0)
                    self.assertEqual(parser.feed(b""), [])
                    self.assertEqual(parser.finish(), [])
                    self.assertEqual(parser.finish(), [])

    def test_bytewise_and_random_partitions(self):
        rng = random.Random(1729)
        fragments = ["".join(rng.choices("abc你好🙂 \t\n\u2003", k=rng.randrange(20))) for _ in range(60)]
        records = [{"event": "token", "text": text} for text in fragments]
        wire = b"".join(map(frame, records)) + DONE
        joined = "".join(fragments)
        expected = records + [{"event": "done", "text": joined, "characters": len(joined), "words": len(joined.split())}]
        for trial in range(21):
            parser = adapter.EventStream()
            output = []
            start = 0
            while start < len(wire):
                end = start + (1 if trial == 0 else rng.randrange(1, 101))
                output.extend(parser.feed(wire[start:end]))
                output.extend(parser.feed(b""))
                start = end
            self.assertEqual(output, expected)
            self.assertEqual(parser.finish(), [])

    def test_no_tokens_required(self):
        for wire, expected in ((DONE, {"event": "done", "text": "", "characters": 0, "words": 0}),
                               (ERROR, {"event": "error", "message": "stopped"})):
            parser = adapter.EventStream()
            self.assertEqual(parser.feed(wire), [expected])
            self.assertEqual(parser.finish(), [])

    def test_data_after_terminal_same_call(self):
        for terminal in (DONE, ERROR):
            for extra in (b"x", b"\n", b" ", b"\xff", TOKEN, DONE, ERROR):
                with self.subTest(terminal=terminal, extra=extra):
                    parser = adapter.EventStream()
                    self.assert_failed(parser, lambda: parser.feed(TOKEN + terminal + extra))

    def test_data_after_terminal_later_call(self):
        for terminal in (DONE, ERROR):
            for extra in (b"x", b"\n", TOKEN, DONE, ERROR):
                parser = adapter.EventStream()
                prior = parser.feed(TOKEN + terminal)
                snapshot = [dict(record) for record in prior]
                self.assertEqual(parser.finish(), [])
                self.assert_failed(parser, lambda: parser.feed(extra))
                self.assertEqual(prior, snapshot)

    def test_invalid_frames_and_schemas(self):
        invalid = [b"\n", b" \n", b"{\n", b"{}{}\n", b"\xff\n",
                   b'{"event":"token","text":"\xc0\xaf"}\n',
                   b'{"event":"token","text":"\xed\xa0\x80"}\n',
                   b'{"event":"token","text":"line\nbreak"}\n']
        invalid += [frame(value) for value in (
            None, True, 1, "done", [], {}, {"event": "unknown"}, {"event": []},
            {"event": "token"}, {"event": "token", "text": None},
            {"event": "token", "text": 1}, {"event": "token", "text": []},
            {"event": "token", "text": "x", "extra": 1},
            {"event": "done", "text": "x"}, {"event": "error"},
            {"event": "error", "message": False},
            {"event": "error", "message": "x", "extra": 1},
        )]
        for wire in invalid:
            with self.subTest(wire=wire):
                parser = adapter.EventStream()
                prior = parser.feed(TOKEN)
                self.assert_failed(parser, lambda: parser.feed(TOKEN + wire))
                self.assertEqual(prior, [{"event": "token", "text": "hello"}])

    def test_frame_limits(self):
        wire = frame({"event": "token", "text": "你好🙂"})
        size = len(wire) - 1
        for split in range(len(wire) + 1):
            parser = adapter.EventStream(size)
            events = parser.feed(wire[:split]) + parser.feed(wire[split:] + DONE)
            self.assertEqual(events[-1]["text"], "你好🙂")
            self.assertEqual(parser.buffered_bytes, 0)
        for terminated in (False, True):
            for split in range(size + 1):
                parser = adapter.EventStream(size - 1)
                over = wire if terminated else wire[:-1]
                def feed_over():
                    parser.feed(over[:split])
                    parser.feed(over[split:])
                self.assert_failed(parser, feed_over)
        parser = adapter.EventStream(1)
        self.assertEqual(parser.feed(b"{"), [])
        self.assertEqual(parser.buffered_bytes, 1)
        self.assert_failed(parser, lambda: parser.feed(b"}"))

    def test_many_small_frames_in_large_chunk(self):
        parser = adapter.EventStream(len(TOKEN) - 1)
        output = parser.feed(TOKEN * 10000 + DONE)
        self.assertEqual(len(output), 10001)
        self.assertEqual(output[-1], {"event": "done", "text": "hello" * 10000,
                                     "characters": 50000, "words": 1})

    def test_premature_eof(self):
        for pending in (b"", b"{", DONE[:-1], TOKEN, TOKEN + b"{", b"\xf0\x9f"):
            parser = adapter.EventStream()
            parser.feed(pending)
            self.assert_failed(parser, parser.finish)

    def test_json_whitespace_and_escaped_strings(self):
        parser = adapter.EventStream()
        events = parser.feed(b' { "text": "a\\nb\\u2003c\\uD83D\\uDE42", "event": "token" }\r\n' + DONE)
        self.assertEqual(events[-1], {"event": "done", "text": "a\nb\u2003c🙂", "characters": 6, "words": 3})


if __name__ == "__main__":
    unittest.main()
