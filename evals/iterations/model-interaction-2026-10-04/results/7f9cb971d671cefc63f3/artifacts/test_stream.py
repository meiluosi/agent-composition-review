"""Contract checks; TEST_MODULE=original can exercise the supplied baseline."""
import importlib
import json
import os
import random
import unittest

app = importlib.import_module(os.environ.get("TEST_MODULE", "app"))


def frame(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


DONE = frame({"event": "done"})
ERROR = frame({"event": "error", "message": "停止"})


class StreamTests(unittest.TestCase):
    def assert_failed(self, parser, operation):
        with self.assertRaises(app.StreamError):
            operation()
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)
        for retry in (lambda: parser.feed(b""), lambda: parser.feed(DONE), parser.finish):
            with self.assertRaises(app.StreamError):
                retry()
            self.assertEqual(parser.state, "FAILED")
            self.assertEqual(parser.buffered_bytes, 0)

    def test_split_utf8(self):
        wire = frame({"event": "token", "text": "你"})
        cut = wire.index("你".encode()) + 1
        parser = app.EventStream()
        self.assertEqual(parser.feed(wire[:cut]), [])
        self.assertEqual(parser.buffered_bytes, cut)
        self.assertEqual(parser.feed(wire[cut:]), [{"event": "token", "text": "你"}])

    def test_same_call_trailing_bytes(self):
        for terminal in (DONE, ERROR):
            for tail in (b"x", b"\n", b"\xff", DONE, ERROR,
                         frame({"event": "token", "text": "late"})):
                with self.subTest(terminal=terminal, tail=tail):
                    parser = app.EventStream()
                    self.assert_failed(parser, lambda: parser.feed(terminal + tail))

    def test_non_json_constants(self):
        for constant in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(constant=constant):
                parser = app.EventStream()
                wire = b'{"event":' + constant + b',"event":"done"}\n'
                self.assert_failed(parser, lambda: parser.feed(wire))

    def test_metrics(self):
        for text, characters, words in (("", 0, 0), (" \t\n\u2003", 4, 0),
                                        ("你\u00a0好\u2003🙂", 5, 3),
                                        ("e\u0301", 2, 1)):
            self.assertEqual(app.text_metrics(text), {"characters": characters, "words": words})

    def test_valid_chunk_boundaries(self):
        fragments = ["", "你🙂", "e\u0301\t", "hello", "world\u2003", "最后\n一词"]
        tokens = [{"event": "token", "text": text} for text in fragments]
        text = "".join(fragments)
        for terminal, terminal_event, state in (
            (DONE, {"event": "done", "text": text, "characters": len(text),
                    "words": len(text.split())}, "DONE"),
            (ERROR, {"event": "error", "message": "停止"}, "ERROR"),
        ):
            wire = b"".join(map(frame, tokens)) + terminal
            partitions = [[wire], [bytes([byte]) for byte in wire]]
            partitions += [[wire[:cut], wire[cut:]] for cut in range(len(wire) + 1)]
            rng = random.Random(74)
            for _ in range(30):
                cuts = [0] + sorted(rng.sample(range(1, len(wire)), 20)) + [len(wire)]
                partitions.append([wire[a:b] for a, b in zip(cuts, cuts[1:])])
            for chunks in partitions:
                with self.subTest(state=state, lengths=list(map(len, chunks))):
                    parser = app.EventStream()
                    emitted = []
                    pending = b""
                    for chunk in chunks:
                        pending = (pending + chunk).rsplit(b"\n", 1)[-1]
                        emitted.extend(parser.feed(chunk))
                        self.assertEqual(parser.buffered_bytes, len(pending))
                        self.assertEqual(parser.feed(b""), [])
                    self.assertEqual(emitted, tokens + [terminal_event])
                    self.assertEqual(parser.state, state)
                    self.assertEqual(parser.finish(), [])
                    self.assertEqual(parser.finish(), [])

    def test_frame_limits(self):
        token = frame({"event": "token", "text": "你🙂"})
        limit = len(token) - 1
        for cut in range(len(token) + 1):
            parser = app.EventStream(limit)
            events = parser.feed(token[:cut]) + parser.feed(token[cut:] + DONE)
            self.assertEqual(len(events), 2)
            self.assertEqual(parser.buffered_bytes, 0)
        for wire in (token, token[:-1]):
            for cut in range(len(wire) + 1):
                parser = app.EventStream(limit - 1)
                def feed_parts():
                    parser.feed(wire[:cut])
                    parser.feed(wire[cut:])
                self.assert_failed(parser, feed_parts)
        parser = app.EventStream(1)
        self.assertEqual(parser.feed(b"{"), [])
        self.assertEqual(parser.buffered_bytes, 1)
        self.assert_failed(parser, lambda: parser.feed(b"}"))

    def test_large_chunk_small_frames(self):
        token = frame({"event": "token", "text": "x"})
        parser = app.EventStream(len(token) - 1)
        output = parser.feed(token * 10000 + DONE)
        self.assertEqual(len(output), 10001)
        self.assertEqual(output[-1], {"event": "done", "text": "x" * 10000,
                                     "characters": 10000, "words": 1})

    def test_invalid_frames(self):
        invalid = [b"", b" ", b"\xff", b'{}', b'[]', b'null', b'1', b'"str"',
                   b'{"event":"unknown"}', b'{"event":"token"}',
                   b'{"event":"token","text":null}',
                   b'{"event":"token","text":1}',
                   b'{"event":"token","text":[]}',
                   b'{"event":"token","text":"x","extra":0}',
                   b'{"event":"done","text":"x"}',
                   b'{"event":"error"}', b'{"event":"error","message":false}',
                   b'{"event":"error","message":"x","extra":0}',
                   b'{"event":"done",}', b'{"event":"done"}{}',
                   b'{"event":"token","text":"\xc0\xaf"}',
                   b'{"event":"token","text":"\xed\xa0\x80"}',
                   b'{"event":"token","text":"\xf0\x9f"}',
                   b'{"event":"token","text":"literal\nnewline"}',
                   b'\xef\xbb\xbf{"event":"done"}']
        for raw in invalid:
            for chunks in ([raw + b"\n"], [raw, b"\n"]):
                with self.subTest(raw=raw, chunks=len(chunks)):
                    parser = app.EventStream()
                    def feed_parts():
                        for chunk in chunks:
                            parser.feed(chunk)
                    self.assert_failed(parser, feed_parts)

    def test_eof_open(self):
        for wire in (b"", b"{", DONE[:-1], b"\xf0", frame({"event": "token", "text": ""})):
            parser = app.EventStream()
            parser.feed(wire)
            self.assert_failed(parser, parser.finish)

    def test_terminal_lifecycle(self):
        for terminal, state in ((DONE, "DONE"), (ERROR, "ERROR")):
            for tail in (b"x", b"\n", DONE):
                parser = app.EventStream()
                self.assertEqual(parser.state, "OPEN")
                parser.feed(terminal)
                self.assertEqual(parser.state, state)
                self.assertEqual(parser.feed(b""), [])
                self.assertEqual(parser.finish(), [])
                self.assertEqual(parser.finish(), [])
                self.assert_failed(parser, lambda: parser.feed(tail))

    def test_previously_returned_events_survive_failure(self):
        parser = app.EventStream()
        token = {"event": "token", "text": "saved"}
        previous = parser.feed(frame(token))
        self.assert_failed(parser, lambda: parser.feed(frame(token) + b"bad\n"))
        self.assertEqual(previous, [token])

    def test_legal_json_whitespace_and_escapes(self):
        parser = app.EventStream()
        events = parser.feed(b' \t{"event":"token","text":"a\\nb\\t\\u4f60"}\r\n' + DONE)
        self.assertEqual(events[-1], {"event": "done", "text": "a\nb\t你",
                                     "characters": 5, "words": 3})


if __name__ == "__main__":
    unittest.main()
