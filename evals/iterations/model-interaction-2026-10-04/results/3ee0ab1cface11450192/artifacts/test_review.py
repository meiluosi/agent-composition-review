"""Contract checks for the LF-delimited event protocol."""
import json
import random
import unittest

import app


def frame(record):
    return json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"


DONE = frame({"event": "done"})


class ContractTests(unittest.TestCase):
    def assert_failed(self, parser, action):
        with self.assertRaises(app.StreamError):
            action()
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)
        for subsequent in (lambda: parser.feed(b""),
                           lambda: parser.feed(DONE), parser.finish):
            with self.assertRaises(app.StreamError):
                subsequent()
            self.assertEqual(parser.state, "FAILED")
            self.assertEqual(parser.buffered_bytes, 0)

    def test_metrics(self):
        cases = [("", 0, 0), ("hello", 5, 1), ("a  b", 4, 2),
                 ("a\tb\nc", 5, 3), (" \t\n\r\v\f\u2003", 7, 0),
                 ("  hello\u2003world  ", 15, 2), ("你🙂 e\u0301", 5, 2)]
        for text, characters, words in cases:
            with self.subTest(text=text):
                self.assertEqual(app.text_metrics(text),
                                 {"characters": characters, "words": words})
        # Cover every code point Python considers whitespace.
        for codepoint in range(0x110000):
            whitespace = chr(codepoint)
            if whitespace.isspace():
                self.assertEqual(app.text_metrics("a" + whitespace + "b")["words"], 2)

    def test_chunk_boundaries(self):
        tokens = [{"event": "token", "text": text}
                  for text in ("", "你🙂hel", "lo\t", "world\n\u2003", "e\u0301")]
        wire = b"".join(map(frame, tokens)) + DONE
        expected = tokens + [{"event": "done", "text": "你🙂hello\tworld\n\u2003e\u0301",
                              "characters": 17, "words": 3}]
        chunkings = [[wire[:cut], b"", wire[cut:]] for cut in range(len(wire) + 1)]
        chunkings.append([bytes([byte]) for byte in wire])
        rng = random.Random(42)
        for _ in range(100):
            cuts = sorted({0, len(wire)} | {rng.randrange(len(wire) + 1) for _ in range(30)})
            chunkings.append([wire[a:b] for a, b in zip(cuts, cuts[1:])])
        for chunks in chunkings:
            parser = app.EventStream()
            self.assertEqual(parser.state, "OPEN")
            self.assertEqual(parser.buffered_bytes, 0)
            received = []
            consumed = b""
            for chunk in chunks:
                consumed += chunk
                received.extend(parser.feed(chunk))
                self.assertEqual(parser.buffered_bytes, len(consumed.rsplit(b"\n", 1)[-1]))
            self.assertEqual(received, expected)
            self.assertEqual(parser.state, "DONE")
            self.assertEqual(parser.feed(b""), [])
            self.assertEqual(parser.finish(), [])
            self.assertEqual(parser.finish(), [])

    def test_empty_done_and_fragment_joining(self):
        parser = app.EventStream()
        self.assertEqual(parser.feed(DONE),
                         [{"event": "done", "text": "", "characters": 0, "words": 0}])
        parser = app.EventStream()
        for text in ("hel", "", "lo", "  ", "world"):
            token = {"event": "token", "text": text}
            self.assertEqual(parser.feed(frame(token)), [token])
        self.assertEqual(parser.feed(DONE),
                         [{"event": "done", "text": "hello  world", "characters": 12, "words": 2}])

    def test_error_terminal(self):
        for message in ("", "失败🙂\nmessage"):
            parser = app.EventStream()
            token = {"event": "token", "text": "partial"}
            error = {"event": "error", "message": message}
            self.assertEqual(parser.feed(frame(token) + frame(error)), [token, error])
            self.assertEqual(parser.state, "ERROR")
            self.assertEqual(parser.buffered_bytes, 0)
            self.assertEqual(parser.feed(b""), [])
            self.assertEqual(parser.finish(), [])
            self.assertEqual(parser.finish(), [])

    def test_frame_byte_limits(self):
        token = {"event": "token", "text": "你🙂"}
        wire = frame(token)
        size = len(wire) - 1
        for cut in range(len(wire) + 1):
            parser = app.EventStream(size)
            self.assertEqual(parser.feed(wire[:cut]) + parser.feed(wire[cut:]), [token])
            self.assertEqual(parser.buffered_bytes, 0)
            parser.feed(DONE)
        parser = app.EventStream(size)
        self.assertEqual(parser.feed(wire[:-1]), [])
        self.assertEqual(parser.buffered_bytes, size)
        self.assertEqual(parser.feed(b"\n"), [token])
        for data in (wire, wire[:-1]):
            parser = app.EventStream(size - 1)
            self.assert_failed(parser, lambda: parser.feed(data))
        parser = app.EventStream(size)
        parser.feed(wire[:-1])
        self.assert_failed(parser, lambda: parser.feed(b" "))
        parser = app.EventStream(1)
        self.assertEqual(parser.feed(b"{"), [])
        self.assertEqual(parser.buffered_bytes, 1)
        self.assert_failed(parser, lambda: parser.feed(b"}"))
        # The bound applies per frame, not to the chunk or accumulated text.
        parser = app.EventStream(size)
        output = parser.feed(wire * 1000 + DONE)
        self.assertEqual(output[:-1], [token] * 1000)
        self.assertEqual(output[-1], {"event": "done", "text": "你🙂" * 1000,
                                     "characters": 2000, "words": 1})

    def test_malformed_frames(self):
        bad_frames = [b"\n", b" \n", b"{\n", b"{}{}\n", b"[]\n", b"null\n",
                      b"true\n", b"3\n", b'"token"\n', b'{}\n',
                      b'{"event":"token","text":"\xff"}\n',
                      b'{"event":"token","text":"\xe4"}\n',
                      b'{"event":"token","text":"\xc0\xaf"}\n',
                      b'{"event":"token","text":"\xed\xa0\x80"}\n',
                      b'{"event":"token","text":"\\q"}\n',
                      b'{"event":"token","text":"literal\nnewline"}\n',
                      b'{"event":"done",}\n']
        bad_records = [{"event": "unknown"}, {"event": "token"},
                       {"event": "token", "text": None}, {"event": "token", "text": 1},
                       {"event": "token", "text": []}, {"event": "token", "text": {}},
                       {"event": "token", "text": True},
                       {"event": "token", "text": "ok", "extra": 0},
                       {"event": "done", "text": ""}, {"event": "error"},
                       {"event": "error", "message": 1},
                       {"event": "error", "message": "", "extra": 1},
                       {"event": []}, {"event": None}]
        bad_frames.extend(map(frame, bad_records))
        for data in bad_frames:
            for cut in range(len(data)):
                with self.subTest(data=data, cut=cut):
                    parser = app.EventStream()
                    # A literal LF may complete an invalid frame in the first chunk.
                    def feed_chunks():
                        parser.feed(data[:cut])
                        parser.feed(data[cut:])
                    self.assert_failed(parser, feed_chunks)

    def test_trailing_data(self):
        for terminal in (DONE, frame({"event": "error", "message": "stop"})):
            for trailing in (b"\n", b" ", b"x", b"\xff", DONE):
                parser = app.EventStream()
                self.assert_failed(parser, lambda: parser.feed(terminal + trailing))
                parser = app.EventStream()
                previous = parser.feed(terminal)
                self.assert_failed(parser, lambda: parser.feed(trailing))
                self.assertEqual(len(previous), 1)

    def test_premature_eof(self):
        token = frame({"event": "token", "text": "hello"})
        for pending in (b"", b"{", DONE[:-1], token, token + b"{"):
            parser = app.EventStream()
            parser.feed(pending)
            self.assert_failed(parser, parser.finish)

    def test_failed_call_returns_no_partial_events(self):
        parser = app.EventStream()
        token = {"event": "token", "text": "valid"}
        previous = parser.feed(frame(token))
        result = None
        with self.assertRaises(app.StreamError):
            result = parser.feed(frame(token) + b"invalid\n")
        self.assertIsNone(result)
        self.assertEqual(previous, [token])
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)


if __name__ == "__main__":
    unittest.main()
