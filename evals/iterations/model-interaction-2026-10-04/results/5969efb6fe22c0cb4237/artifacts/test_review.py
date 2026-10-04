"""Contract checks: ./run_python test_review.py [app|original]."""
import importlib
import json
import random
import sys
import unittest


target = sys.argv.pop(1) if len(sys.argv) > 1 and sys.argv[1] in {"app", "original"} else "app"
api = importlib.import_module(target)


def frame(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


class ContractTests(unittest.TestCase):
    def assert_failed(self, parser, action):
        with self.assertRaises(api.StreamError):
            action()
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)
        for again in (lambda: parser.feed(b""), lambda: parser.feed(b"{}\n"), parser.finish):
            with self.assertRaises(api.StreamError):
                again()
            self.assertEqual(parser.state, "FAILED")
            self.assertEqual(parser.buffered_bytes, 0)

    def check_chunks(self, wire, chunks, expected, terminal, limit=4096):
        parser = api.EventStream(limit)
        self.assertEqual(parser.state, "OPEN")
        self.assertEqual(parser.buffered_bytes, 0)
        events = []
        prefix = b""
        for chunk in chunks:
            prefix += chunk
            events.extend(parser.feed(chunk))
            self.assertEqual(parser.buffered_bytes, len(prefix.rsplit(b"\n", 1)[-1]))
            self.assertEqual(parser.state, terminal if len(prefix) == len(wire) else "OPEN")
        self.assertEqual(prefix, wire)
        self.assertEqual(events, expected)
        self.assertEqual(parser.feed(b""), [])
        self.assertEqual(parser.finish(), [])
        self.assertEqual(parser.finish(), [])

    def test_metrics(self):
        for text, characters, words in [
            ("", 0, 0), (" \t\n\r\v\f\u00a0\u2003\u2028\u3000", 10, 0),
            ("a\u00a0b\u2003c\u2028d", 7, 4), ("你好🙂e\u0301", 5, 1),
            (" one\t two\nthree ", 16, 3),
        ]:
            self.assertEqual(api.text_metrics(text), {"characters": characters, "words": words})

    def test_all_two_chunk_boundaries(self):
        tokens = ["你🙂", "", "好\u2003", "one", "two\n\tthree\\\""]
        records = [{"event": "token", "text": text} for text in tokens]
        text = "".join(tokens)
        for terminal in [{"event": "done"}, {"event": "error", "message": "出错🙂"}]:
            wire = b"".join(map(frame, records + [terminal]))
            end = terminal if terminal["event"] == "error" else {
                "event": "done", "text": text, "characters": len(text), "words": 3,
            }
            for split in range(len(wire) + 1):
                with self.subTest(terminal=terminal["event"], split=split):
                    self.check_chunks(wire, [wire[:split], b"", wire[split:]],
                                      records + [end], terminal["event"].upper())
            self.check_chunks(wire, [bytes([byte]) for byte in wire],
                              records + [end], terminal["event"].upper())

    def test_seeded_chunk_partitions(self):
        rng = random.Random(20261004)
        alphabet = ["a", "中", "🙂", "\n", "\t", "\u00a0", "\u2003", "\u0301", "\\", '"']
        for case in range(100):
            records = [{"event": "token", "text": "".join(rng.choices(alphabet, k=rng.randrange(20)))}
                       for _ in range(rng.randrange(20))]
            text = "".join(record["text"] for record in records)
            wire = b"".join(map(frame, records + [{"event": "done"}]))
            expected = records + [{"event": "done", "text": text,
                                   "characters": len(text), "words": len(text.split())}]
            chunks = []
            start = 0
            while start < len(wire):
                end = start + rng.randrange(1, 50)
                chunks.append(wire[start:end])
                start = end
            self.check_chunks(wire, chunks, expected, "DONE")

    def test_no_tokens_and_empty_error(self):
        for terminal, expected in [
            ({"event": "done"}, {"event": "done", "text": "", "characters": 0, "words": 0}),
            ({"event": "error", "message": ""}, {"event": "error", "message": ""}),
        ]:
            wire = frame(terminal)
            self.check_chunks(wire, [wire], [expected], terminal["event"].upper(), len(wire) - 1)

    def test_frame_limits(self):
        token = {"event": "token", "text": "汉🙂"}
        wire = frame(token)
        size = len(wire) - 1
        for split in range(len(wire) + 1):
            parser = api.EventStream(size)
            self.assertEqual(parser.feed(wire[:split]) + parser.feed(wire[split:]), [token])
            self.assertEqual(parser.buffered_bytes, 0)
            self.assertEqual(parser.feed(frame({"event": "done"}))[0]["text"], "汉🙂")
        for complete in [False, True]:
            payload = wire if complete else wire[:-1]
            for split in range(len(payload)):
                parser = api.EventStream(size - 1)
                if split <= size - 1:
                    self.assertEqual(parser.feed(payload[:split]), [])
                    self.assert_failed(parser, lambda: parser.feed(payload[split:]))
        parser = api.EventStream(1)
        self.assertEqual(parser.feed(b"{"), [])
        self.assertEqual(parser.buffered_bytes, 1)
        self.assert_failed(parser, lambda: parser.feed(b"}"))

    def test_many_frames_in_large_chunk(self):
        token = {"event": "token", "text": "x"}
        token_wire = frame(token)
        parser = api.EventStream(len(token_wire) - 1)
        events = parser.feed(token_wire * 10000 + frame({"event": "done"}))
        self.assertEqual(events[:-1], [token] * 10000)
        self.assertEqual(events[-1], {"event": "done", "text": "x" * 10000,
                                    "characters": 10000, "words": 1})
        self.assertEqual(parser.buffered_bytes, 0)

    def test_malformed_frames(self):
        malformed = [
            b"", b" ", b"{", b"{}{}", b"{'event':'done'}", b'{"event":"done",}',
            b'\xef\xbb\xbf{"event":"done"}', b'{"event":"token","text":"\xff"}',
            b'{"event":"token","text":"\xc0\x80"}', b'{"event":"token","text":"\xed\xa0\x80"}',
            b'{"event":"token","text":"\xf4\x90\x80\x80"}',
            b'{"event":"token","text":"\xf0\x9f"}',
            b'{"event":"token","text":"\x00"}', b'{"event":"token","text":"\t"}',
            b'{"event":"token","text":"literal\nnewline"}',
        ]
        for raw in malformed:
            for split in range(len(raw) + 1):
                with self.subTest(raw=raw, split=split):
                    parser = api.EventStream()
                    def feed_parts():
                        parser.feed(raw[:split])
                        return parser.feed(raw[split:] + b"\n")
                    self.assert_failed(parser, feed_parts)

    def test_invalid_schemas(self):
        invalid = [None, True, 1, "done", [], {}, {"event": "unknown"},
                   {"event": ["done"]}, {"event": None}, {"event": "token"},
                   {"event": "error"}, {"event": "done", "text": ""},
                   {"event": "token", "text": "", "extra": 1},
                   {"event": "error", "message": "", "extra": 1}]
        for value in [None, False, 0, [], {}, float("nan"), float("inf")]:
            invalid.extend([{"event": "token", "text": value}, {"event": "error", "message": value}])
        for record in invalid:
            with self.subTest(record=record):
                parser = api.EventStream()
                self.assert_failed(parser, lambda: parser.feed(frame(record)))

    def test_after_terminal(self):
        for terminal in [{"event": "done"}, {"event": "error", "message": "stop"}]:
            for extra in [b"\n", b" ", b"x", b"\xff", frame({"event": "done"}),
                          frame({"event": "token", "text": "late"})]:
                parser = api.EventStream()
                self.assert_failed(parser, lambda: parser.feed(frame(terminal) + extra))
                parser = api.EventStream()
                saved = parser.feed(frame(terminal))
                snapshot = [dict(record) for record in saved]
                self.assertEqual(parser.finish(), [])
                self.assert_failed(parser, lambda: parser.feed(extra))
                self.assertEqual(saved, snapshot)

    def test_premature_eof(self):
        wire = frame({"event": "token", "text": "🙂"})
        for pending in [b"", b"{", wire, wire[:-1], frame({"event": "done"})[:-1]]:
            parser = api.EventStream()
            parser.feed(pending)
            self.assert_failed(parser, parser.finish)

    def test_failure_does_not_return_partial_events(self):
        parser = api.EventStream()
        first = {"event": "token", "text": "already returned"}
        saved = parser.feed(frame(first))
        returned = []
        def fail():
            returned.append(parser.feed(frame({"event": "token", "text": "in failing call"}) + b"bad\n"))
        self.assert_failed(parser, fail)
        self.assertEqual(returned, [])
        self.assertEqual(saved, [first])

    def test_json_whitespace_escaping_and_key_order(self):
        parser = api.EventStream()
        self.assertEqual(parser.feed(b' \t{"text":"\\u4f60\\ud83d\\ude42\\n", "event":"token"} \r\n'),
                         [{"event": "token", "text": "你🙂\n"}])
        self.assertEqual(parser.feed(b' { "event" : "done" } \r\n'),
                         [{"event": "done", "text": "你🙂\n", "characters": 3, "words": 1}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
