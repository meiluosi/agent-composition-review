"""Local contract regression tests; run with app (default) or original."""
import importlib
import json
import random
import sys
import unittest


subject = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else "app")


def frame(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


TOKEN = frame({"event": "token", "text": "hello"})
DONE = frame({"event": "done"})
ERROR = frame({"event": "error", "message": "停止 😀"})


class ContractTests(unittest.TestCase):
    def assert_failed(self, parser, operation):
        with self.assertRaises(subject.StreamError):
            operation()
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)
        for later in (lambda: parser.feed(b""), lambda: parser.feed(DONE), parser.finish):
            with self.assertRaises(subject.StreamError):
                later()
            self.assertEqual(parser.state, "FAILED")
            self.assertEqual(parser.buffered_bytes, 0)

    def test_metrics(self):
        for text, counts in [("", (0, 0)), ("  a\tb\n", (6, 2)),
                             ("中\u2003文\u00a0😀", (5, 3)), ("e\u0301", (2, 1))]:
            with self.subTest(text=text):
                self.assertEqual(subject.text_metrics(text), dict(zip(("characters", "words"), counts)))

    def test_initial_and_empty_feeds(self):
        p = subject.EventStream()
        self.assertEqual(p.state, "OPEN")
        self.assertEqual(p.buffered_bytes, 0)
        self.assertEqual(p.feed(b""), [])
        self.assertEqual(p.state, "OPEN")

    def test_every_split_and_single_bytes(self):
        fragments = ["", "中😀", " e\u0301\t", "word\u2003", "next\n"]
        records = [{"event": "token", "text": text} for text in fragments]
        text = "".join(fragments)
        expected = records + [{"event": "done", "text": text, "characters": len(text), "words": 4}]
        wire = b"".join(map(frame, records)) + DONE
        chunkings = [[wire[:i], wire[i:]] for i in range(len(wire) + 1)]
        chunkings.append([bytes([byte]) for byte in wire])
        rng = random.Random(451)
        for _ in range(100):
            cuts = sorted({0, len(wire), *(rng.randrange(len(wire) + 1) for _ in range(25))})
            chunkings.append([wire[a:b] for a, b in zip(cuts, cuts[1:])])
        for chunks in chunkings:
            with self.subTest(lengths=[len(chunk) for chunk in chunks]):
                p = subject.EventStream()
                actual = []
                pending = b""
                for chunk in chunks:
                    actual.extend(p.feed(chunk))
                    pending = (pending + chunk).rsplit(b"\n", 1)[-1]
                    self.assertEqual(p.buffered_bytes, len(pending))
                self.assertEqual(actual, expected)
                self.assertEqual(p.state, "DONE")
                self.assertEqual(p.finish(), [])

    def test_error_split_at_every_byte(self):
        for i in range(len(ERROR) + 1):
            with self.subTest(split=i):
                p = subject.EventStream()
                self.assertEqual(p.feed(ERROR[:i]) + p.feed(ERROR[i:]),
                                 [{"event": "error", "message": "停止 😀"}])
                self.assertEqual(p.state, "ERROR")
                self.assertEqual(p.buffered_bytes, 0)

    def test_terminal_repeated_eof_and_empty_feed(self):
        for terminal, state in ((DONE, "DONE"), (ERROR, "ERROR")):
            p = subject.EventStream()
            p.feed(terminal)
            for _ in range(3):
                self.assertEqual(p.finish(), [])
                self.assertEqual(p.feed(b""), [])
                self.assertEqual(p.state, state)
                self.assertEqual(p.buffered_bytes, 0)

    def test_trailing_data_same_call(self):
        for terminal in (DONE, ERROR):
            for trailing in (b"x", b"\n", b" ", b"\xff", TOKEN, DONE, ERROR):
                with self.subTest(terminal=terminal, trailing=trailing):
                    p = subject.EventStream()
                    self.assert_failed(p, lambda: p.feed(TOKEN + terminal + trailing))

    def test_trailing_data_later_call(self):
        for terminal in (DONE, ERROR):
            p = subject.EventStream()
            p.feed(terminal)
            p.finish()
            self.assert_failed(p, lambda: p.feed(b"x"))

    def test_premature_eof(self):
        for wire in (b"", b"{", TOKEN, TOKEN + DONE[:-1], b"\xf0"):
            with self.subTest(wire=wire):
                p = subject.EventStream()
                # All bytes are retained until LF, including incomplete UTF-8.
                p.feed(wire)
                self.assert_failed(p, p.finish)

    def test_invalid_frames(self):
        invalid = [b"", b" ", b"{", b"null", b"[]", b"1", b'"x"',
                   b"{}", b'{"event":"other"}', b'{"event":"token"}',
                   b'{"event":"token","text":null}', b'{"event":"token","text":42}',
                   b'{"event":"token","text":[]}', b'{"event":"done","text":""}',
                   b'{"event":"error"}', b'{"event":"error","message":false}',
                   b'{"event":"token","text":"x","extra":0}',
                   b'{"event":"done",}', b'{"event":"done"} {}',
                   b'{"event":"token","text":"\xff"}', b'\xef\xbb\xbf{"event":"done"}',
                   b'{"event":"token","text":"literal\nnewline"}',
                   b'{"event":"token","text":"\xc0\xaf"}']
        for raw in invalid:
            with self.subTest(raw=raw):
                p = subject.EventStream()
                self.assert_failed(p, lambda: p.feed(raw + b"\n"))

    def test_non_json_constants(self):
        for constant in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(constant=constant):
                p = subject.EventStream()
                self.assert_failed(p, lambda: p.feed(b'{"event":' + constant + b',"event":"done"}\n'))

    def test_limit_is_wire_bytes_excluding_lf(self):
        wire = frame({"event": "token", "text": "中文😀"})
        size = len(wire) - 1
        for split in range(len(wire) + 1):
            with self.subTest(split=split):
                p = subject.EventStream(max_buffer=size)
                self.assertEqual(p.feed(wire[:split]) + p.feed(wire[split:]),
                                 [{"event": "token", "text": "中文😀"}])
                p.feed(DONE)
                self.assertEqual(p.finish(), [])
        for wire_input in (wire, wire[:-1]):
            p = subject.EventStream(max_buffer=size - 1)
            p.feed(wire_input[:size - 1])
            self.assertEqual(p.buffered_bytes, size - 1)
            self.assert_failed(p, lambda: p.feed(wire_input[size - 1:]))

    def test_large_chunk_of_small_frames(self):
        p = subject.EventStream(max_buffer=len(TOKEN) - 1)
        events = p.feed(TOKEN * 1000 + DONE)
        self.assertEqual(len(events), 1001)
        self.assertEqual(events[-1], {"event": "done", "text": "hello" * 1000,
                                     "characters": 5000, "words": 1})
        self.assertEqual(p.buffered_bytes, 0)

    def test_oversized_complete_and_unfinished_frames(self):
        for data in (b"x" * 10000, b"x" * 10000 + b"\n"):
            p = subject.EventStream(max_buffer=32)
            self.assert_failed(p, lambda: p.feed(data))
        p = subject.EventStream(max_buffer=1)
        self.assertEqual(p.feed(b"{"), [])
        self.assertEqual(p.buffered_bytes, 1)
        self.assert_failed(p, lambda: p.feed(b"}"))

    def test_completed_and_incomplete_in_same_call(self):
        p = subject.EventStream()
        self.assertEqual(p.feed(TOKEN + DONE[:5]), [{"event": "token", "text": "hello"}])
        self.assertEqual(p.buffered_bytes, 5)
        self.assertEqual(p.feed(b""), [])
        self.assertEqual(p.feed(DONE[5:]), [{"event": "done", "text": "hello", "characters": 5, "words": 1}])
        self.assertEqual(p.buffered_bytes, 0)

    def test_failure_preserves_previously_returned_events(self):
        p = subject.EventStream()
        previous = p.feed(TOKEN)
        result = "not returned"
        with self.assertRaises(subject.StreamError):
            result = p.feed(TOKEN + b"bad\n")
        self.assertEqual(result, "not returned")
        self.assertEqual(previous, [{"event": "token", "text": "hello"}])
        self.assert_failed(p, p.finish)

    def test_json_whitespace_escapes_and_empty_stream_text(self):
        p = subject.EventStream()
        self.assertEqual(p.feed(b' \t{ "text": "a\\nb\\t\\u4e2d", "event": "token" }\r\n')[0],
                         {"event": "token", "text": "a\nb\t中"})
        self.assertEqual(p.feed(DONE)[0], {"event": "done", "text": "a\nb\t中", "characters": 5, "words": 3})
        p = subject.EventStream()
        self.assertEqual(p.feed(DONE), [{"event": "done", "text": "", "characters": 0, "words": 0}])


if __name__ == "__main__":
    unittest.main()
