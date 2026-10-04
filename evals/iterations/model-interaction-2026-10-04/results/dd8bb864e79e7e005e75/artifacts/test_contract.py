"""Contract regression tests; run with app (default) or original as argument."""
import importlib
import json
import random
import sys
import unittest


module = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else "app")


def frame(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


TOKEN = frame({"event": "token", "text": "hello"})
DONE = frame({"event": "done"})
ERROR = frame({"event": "error", "message": "failure"})


class ContractTests(unittest.TestCase):
    def assert_failed(self, parser, operation):
        with self.assertRaises(module.StreamError):
            operation()
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)
        for action in (lambda: parser.feed(b""), lambda: parser.feed(DONE), parser.finish):
            with self.assertRaises(module.StreamError):
                action()
            self.assertEqual(parser.state, "FAILED")
            self.assertEqual(parser.buffered_bytes, 0)

    def test_metrics(self):
        for value in ("", " a  b\t\n", "中🙂 e\u0301", "one\u2003two\u00a0three", " \r\v\f "):
            self.assertEqual(module.text_metrics(value), {
                "characters": len(value), "words": len(value.split())})

    def test_all_two_chunk_splits(self):
        fragments = ["你🙂", "", "好\n\t", "one\u2003two", "e\u0301"]
        records = [{"event": "token", "text": value} for value in fragments]
        text = "".join(fragments)
        for terminal in ({"event": "done"}, {"event": "error", "message": "错误🙂"}):
            wire = b"".join(frame(record) for record in records + [terminal])
            last = terminal if terminal["event"] == "error" else {
                "event": "done", "text": text, "characters": len(text), "words": len(text.split())}
            for split in range(len(wire) + 1):
                with self.subTest(terminal=terminal["event"], split=split):
                    parser = module.EventStream()
                    events = parser.feed(wire[:split]) + parser.feed(wire[split:])
                    self.assertEqual(events, records + [last])
                    self.assertEqual(parser.state, terminal["event"].upper())
                    self.assertEqual(parser.buffered_bytes, 0)
                    self.assertEqual(parser.finish(), [])

    def test_random_and_bytewise_chunking(self):
        records = [{"event": "token", "text": s} for s in ("🙂", "a", "\n中", "", "\u00a0b")]
        text = "".join(record["text"] for record in records)
        wire = b"".join(map(frame, records)) + DONE
        expected = records + [{"event": "done", "text": text,
                               "characters": len(text), "words": len(text.split())}]
        rng = random.Random(2026)
        for trial in range(101):
            parser = module.EventStream()
            events = []
            start = 0
            while start < len(wire):
                width = 1 if trial == 0 else rng.randint(1, 17)
                chunk = wire[start:start + width]
                events.extend(parser.feed(chunk))
                start += len(chunk)
                prefix = wire[:start]
                self.assertEqual(parser.buffered_bytes, len(prefix.rsplit(b"\n", 1)[-1]))
                self.assertEqual(parser.feed(b""), [])
            self.assertEqual(events, expected)
            self.assertEqual(parser.finish(), [])

    def test_terminal_trailing_bytes_same_call(self):
        for terminal in (DONE, ERROR):
            for suffix in (b"x", b"\n", b" ", b"\xc3", TOKEN, DONE, ERROR):
                with self.subTest(terminal=terminal, suffix=suffix):
                    parser = module.EventStream()
                    self.assert_failed(parser, lambda: parser.feed(TOKEN + terminal + suffix))

    def test_terminal_later_calls_and_repeat_finish(self):
        for terminal in (DONE, ERROR):
            parser = module.EventStream()
            events = parser.feed(terminal)
            self.assertEqual(len(events), 1)
            for _ in range(3):
                self.assertEqual(parser.finish(), [])
                self.assertEqual(parser.feed(b""), [])
            self.assert_failed(parser, lambda: parser.feed(b" "))

    def test_empty_stream_done(self):
        parser = module.EventStream()
        self.assertEqual(parser.state, "OPEN")
        self.assertEqual(parser.feed(b""), [])
        self.assertEqual(parser.feed(DONE), [
            {"event": "done", "text": "", "characters": 0, "words": 0}])

    def test_invalid_frames(self):
        invalid = [b"", b" ", b"{", b"{}{}", b"\xff", b'"text"', b"[]", b"null",
                   b'{"event":"token","text":"literal\nnewline"}',
                   b'{"event":"token","text":"\xc3"}', b"\xef\xbb\xbf{}"]
        invalid += [frame(record)[:-1] for record in (
            {}, {"event": "unknown"}, {"event": "token"}, {"event": "token", "text": None},
            {"event": "token", "text": 42}, {"event": "token", "text": []},
            {"event": "token", "text": "ok", "extra": 1}, {"event": "done", "text": ""},
            {"event": "error"}, {"event": "error", "message": False},
            {"event": "error", "message": "bad", "extra": 1}, {"event": []})]
        for raw in invalid:
            with self.subTest(raw=raw):
                parser = module.EventStream()
                self.assert_failed(parser, lambda: parser.feed(raw + b"\n"))

    def test_unfinished_utf8_retained(self):
        parser = module.EventStream()
        raw = b'{"event":"token","text":"\xf0\x9f'
        self.assertEqual(parser.feed(raw), [])
        self.assertEqual(parser.state, "OPEN")
        self.assertEqual(parser.buffered_bytes, len(raw))
        self.assert_failed(parser, parser.finish)

    def test_premature_eof(self):
        for wire in (b"", b"{", TOKEN, TOKEN + DONE[:-1]):
            with self.subTest(wire=wire):
                parser = module.EventStream()
                parser.feed(wire)
                self.assert_failed(parser, parser.finish)

    def test_frame_limit_complete_and_incomplete(self):
        wire = frame({"event": "token", "text": "🙂" * 8})
        size = len(wire) - 1
        for split in range(len(wire) + 1):
            with self.subTest(split=split):
                parser = module.EventStream(size)
                events = parser.feed(wire[:split]) + parser.feed(wire[split:])
                self.assertEqual(events, [{"event": "token", "text": "🙂" * 8}])
                self.assertEqual(parser.buffered_bytes, 0)
                parser.feed(DONE)
                parser = module.EventStream(size - 1)
                def oversized():
                    parser.feed(wire[:split])
                    parser.feed(wire[split:])
                self.assert_failed(parser, oversized)
        parser = module.EventStream(size)
        self.assertEqual(parser.feed(wire[:-1]), [])
        self.assertEqual(parser.buffered_bytes, size)
        self.assert_failed(parser, lambda: parser.feed(b" "))

    def test_many_frames_large_chunk(self):
        parser = module.EventStream(len(TOKEN) - 1)
        events = parser.feed(TOKEN * 5000 + DONE)
        self.assertEqual(len(events), 5001)
        self.assertEqual(events[:-1], [{"event": "token", "text": "hello"}] * 5000)
        self.assertEqual(events[-1], {"event": "done", "text": "hello" * 5000,
                                    "characters": 25000, "words": 1})

    def test_buffer_counts_wire_bytes(self):
        wire = frame({"event": "token", "text": "汉字"})
        parser = module.EventStream()
        self.assertEqual(parser.feed(wire[:-1]), [])
        self.assertEqual(parser.buffered_bytes, len(wire) - 1)
        self.assertEqual(parser.feed(b"\n"), [{"event": "token", "text": "汉字"}])
        self.assertEqual(parser.buffered_bytes, 0)

    def test_failed_feed_no_partial_return_and_prior_events_valid(self):
        parser = module.EventStream()
        prior = parser.feed(TOKEN)
        sentinel = object()
        result = sentinel
        with self.assertRaises(module.StreamError):
            result = parser.feed(TOKEN + b"invalid\n")
        self.assertIs(result, sentinel)
        self.assertEqual(prior, [{"event": "token", "text": "hello"}])
        self.assert_failed(parser, lambda: parser.feed(b""))

    def test_tiny_limit(self):
        parser = module.EventStream(1)
        self.assertEqual(parser.feed(b"{"), [])
        self.assertEqual(parser.buffered_bytes, 1)
        self.assert_failed(parser, lambda: parser.feed(b"}"))


if __name__ == "__main__":
    unittest.main()
