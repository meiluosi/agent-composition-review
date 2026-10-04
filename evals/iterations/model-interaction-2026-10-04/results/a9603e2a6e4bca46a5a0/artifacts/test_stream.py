"""Local contract regression checks; accepts app or original as the first argument."""
import importlib
import json
import sys
import unittest

module = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else "app")


def frame(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


TOKEN = {"event": "token", "text": "你好🙂"}
DONE = {"event": "done"}
ERROR = {"event": "error", "message": "错误🙂"}


class ContractTests(unittest.TestCase):
    def failed(self, parser, action):
        with self.assertRaises(module.StreamError):
            action()
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)
        for again in (lambda: parser.feed(b""), lambda: parser.feed(frame(DONE)), parser.finish):
            with self.assertRaises(module.StreamError):
                again()
            self.assertEqual(parser.state, "FAILED")
            self.assertEqual(parser.buffered_bytes, 0)

    def test_metrics(self):
        for text in ("", " ", "a  b", " a ", "a\tb\nc\rd", "甲\u3000乙", "\u00a0\u2003", "你好🙂", "e\u0301"):
            with self.subTest(text=text):
                self.assertEqual(module.text_metrics(text), {"characters": len(text), "words": len(text.split())})

    def test_done_metrics_and_concatenation(self):
        for pieces in ([], [""], ["a", "b"], [" a", "", " \t", "甲\u3000乙", "\n"]):
            with self.subTest(pieces=pieces):
                parser = module.EventStream()
                tokens = [{"event": "token", "text": part} for part in pieces]
                text = "".join(pieces)
                self.assertEqual(parser.feed(b"".join(frame(token) for token in tokens) + frame(DONE)), tokens + [
                    {"event": "done", "text": text, "characters": len(text), "words": len(text.split())}
                ])

    def test_all_split_points(self):
        wire = frame(TOKEN) + frame(DONE)
        expected = [TOKEN, {"event": "done", "text": TOKEN["text"], "characters": 3, "words": 1}]
        for split in range(len(wire) + 1):
            with self.subTest(split=split):
                parser = module.EventStream()
                output = parser.feed(wire[:split])
                self.assertEqual(parser.buffered_bytes, len(wire[:split].rsplit(b"\n", 1)[-1]))
                output += parser.feed(wire[split:])
                self.assertEqual(output, expected)
                self.assertEqual(parser.state, "DONE")
                self.assertEqual(parser.buffered_bytes, 0)
                self.assertEqual(parser.finish(), [])

    def test_bytewise_and_escaped_newline(self):
        token = {"event": "token", "text": 'a\nb\\c"🙂'}
        parser = module.EventStream()
        output = []
        for byte in frame(token) + frame(ERROR):
            output.extend(parser.feed(bytes([byte])))
        self.assertEqual(output, [token, ERROR])
        self.assertEqual(parser.state, "ERROR")

    def test_terminal_calls(self):
        for terminal, state in ((DONE, "DONE"), (ERROR, "ERROR")):
            parser = module.EventStream()
            self.assertEqual(parser.state, "OPEN")
            self.assertEqual(parser.feed(b""), [])
            parser.feed(frame(terminal))
            for _ in range(3):
                self.assertEqual(parser.feed(b""), [])
                self.assertEqual(parser.finish(), [])
                self.assertEqual(parser.state, state)
                self.assertEqual(parser.buffered_bytes, 0)
            self.failed(parser, lambda: parser.feed(b"x"))

    def test_trailing_bytes_in_same_call(self):
        for terminal in (DONE, ERROR):
            for tail in (b"x", b"\n", frame(DONE), frame(TOKEN), b"\xff"):
                with self.subTest(terminal=terminal, tail=tail):
                    parser = module.EventStream()
                    self.failed(parser, lambda: parser.feed(frame(terminal) + tail))

    def test_invalid_frames(self):
        invalid = [b"\n", b" \n", b"{\n", b"\xff\n", b'{} {}\n',
                   b'{"event":"token","text":"a\nb"}\n',
                   b'{"event":"token","text":"\xff"}\n',
                   b'{"event":"token","text":NaN}\n']
        invalid += [frame(record) for record in (
            None, [], 42, "done", {}, {"event": "unknown"}, {"event": "token"},
            {"event": "token", "text": None}, {"event": "token", "text": 1},
            {"event": "token", "text": "", "extra": 1},
            {"event": "done", "text": ""}, {"event": "error"},
            {"event": "error", "message": False},
            {"event": "error", "message": "", "extra": 1},
        )]
        for wire in invalid:
            with self.subTest(wire=wire):
                parser = module.EventStream()
                self.failed(parser, lambda: parser.feed(wire))

    def test_premature_eof(self):
        for pending in (b"", b"{", frame(DONE)[:-1], frame(TOKEN), b"\xe4"):
            parser = module.EventStream()
            parser.feed(pending)
            self.failed(parser, parser.finish)

    def test_frame_limits(self):
        wire = frame(TOKEN)
        size = len(wire) - 1
        for split in range(len(wire) + 1):
            parser = module.EventStream(size)
            self.assertEqual(parser.feed(wire[:split]) + parser.feed(wire[split:]), [TOKEN])
            parser.feed(frame(DONE))
            self.assertEqual(parser.finish(), [])
        for terminated in (False, True):
            for split in range(size + 1):
                parser = module.EventStream(size - 1)
                oversized = wire if terminated else wire[:-1]
                def action():
                    parser.feed(oversized[:split])
                    parser.feed(oversized[split:])
                self.failed(parser, action)
        parser = module.EventStream(1)
        self.assertEqual(parser.feed(b"{"), [])
        self.assertEqual(parser.buffered_bytes, 1)
        self.failed(parser, lambda: parser.feed(b"}"))

    def test_many_frames_in_large_chunk(self):
        parser = module.EventStream(len(frame(TOKEN)) - 1)
        output = parser.feed(frame(TOKEN) * 1000 + frame(DONE))
        self.assertEqual(output[:-1], [TOKEN] * 1000)
        self.assertEqual(output[-1], {"event": "done", "text": TOKEN["text"] * 1000, "characters": 3000, "words": 1})
        self.assertEqual(parser.buffered_bytes, 0)

    def test_failed_feed_returns_no_partial_list(self):
        parser = module.EventStream()
        previous = parser.feed(frame(TOKEN))
        result = object()
        sentinel = result
        try:
            result = parser.feed(frame(TOKEN) + b"{}\n")
        except module.StreamError:
            pass
        else:
            self.fail("bad frame accepted")
        self.assertIs(result, sentinel)
        self.assertEqual(previous, [TOKEN])
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)


if __name__ == "__main__":
    unittest.main()
