"""Business-contract regressions; run with app (default) or original."""
import importlib
import json
import random
import sys
import unittest


subject = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else "app")


def frame(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


TOKEN = {"event": "token", "text": ""}
DONE = frame({"event": "done"})
ERROR = frame({"event": "error", "message": "出错"})


class ContractTests(unittest.TestCase):
    def assert_failed(self, parser, action):
        with self.assertRaises(subject.StreamError):
            action()
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)
        for later in (lambda: parser.feed(b""), lambda: parser.feed(DONE), parser.finish):
            with self.assertRaises(subject.StreamError):
                later()

    def test_metrics(self):
        for text, words in [("", 0), (" \t\n\u2003", 0), ("a\u00a0b\u2003中\n🙂", 4), ("e\u0301", 1)]:
            self.assertEqual(subject.text_metrics(text), {"characters": len(text), "words": words})

    def test_all_two_chunk_boundaries(self):
        records = [TOKEN, {"event": "token", "text": "中🙂 café\n\u2003word"}]
        wire = b"".join(map(frame, records)) + DONE
        text = records[1]["text"]
        expected = records + [{"event": "done", "text": text, "characters": len(text), "words": 3}]
        for split in range(len(wire) + 1):
            with self.subTest(split=split):
                parser = subject.EventStream()
                result = parser.feed(wire[:split]) + parser.feed(wire[split:])
                self.assertEqual(result, expected)
                self.assertEqual(parser.state, "DONE")
                self.assertEqual(parser.buffered_bytes, 0)
                self.assertEqual(parser.finish(), [])

    def test_random_and_single_byte_partitions(self):
        records = [{"event": "token", "text": t} for t in ("a", "", "é", "中🙂", "\t", "b")]
        wire = b"".join(map(frame, records)) + DONE
        text = "".join(r["text"] for r in records)
        expected = records + [{"event": "done", "text": text, "characters": len(text), "words": 2}]
        rng = random.Random(719)
        for trial in range(101):
            parser = subject.EventStream()
            result = []
            position = 0
            while position < len(wire):
                width = 1 if trial == 0 else rng.randrange(1, 23)
                chunk = wire[position:position + width]
                result.extend(parser.feed(chunk))
                position += len(chunk)
                pending = wire[:position].rsplit(b"\n", 1)[-1]
                self.assertEqual(parser.buffered_bytes, len(pending))
                self.assertEqual(parser.feed(b""), [])
            self.assertEqual(result, expected)

    def test_terminal_and_eof_idempotence(self):
        for terminal, state in [(DONE, "DONE"), (ERROR, "ERROR")]:
            parser = subject.EventStream()
            self.assertEqual(parser.state, "OPEN")
            self.assertEqual(parser.feed(b""), [])
            events = parser.feed(terminal)
            self.assertEqual(events[-1]["event"], state.lower())
            self.assertEqual(parser.state, state)
            for _ in range(3):
                self.assertEqual(parser.feed(b""), [])
                self.assertEqual(parser.finish(), [])
                self.assertEqual(parser.buffered_bytes, 0)

    def test_every_byte_after_terminal_fails(self):
        for terminal in (DONE, ERROR):
            for suffix in (b"x", b" ", b"\n", b"\xff", DONE, ERROR, frame(TOKEN)):
                for same_call in (True, False):
                    with self.subTest(terminal=terminal, suffix=suffix, same_call=same_call):
                        parser = subject.EventStream()
                        if same_call:
                            action = lambda: parser.feed(terminal + suffix)
                        else:
                            parser.feed(terminal)
                            action = lambda: parser.feed(suffix)
                        self.assert_failed(parser, action)

    def test_malformed_frames(self):
        malformed = [b"", b" ", b"{", b"{}{}", b"[]", b"null", b"42", b"true",
                     b'{"event":"token","text":"\xff"}',
                     b'{"event":"token","text":"\xc0\xaf"}',
                     b'{"event":"token","text":"\xed\xa0\x80"}',
                     b'{"event":"token","text":"\x01"}',
                     b'{"event":"done",}', b'{"event":"token","text":"x\ny"}']
        for raw in malformed:
            with self.subTest(raw=raw):
                parser = subject.EventStream()
                self.assert_failed(parser, lambda: parser.feed(raw + b"\n"))

    def test_invalid_schemas(self):
        invalid = [{}, {"event": "unknown"}, {"event": []}, {"event": "token"},
                   {"event": "error"}, {"event": "done", "text": ""},
                   {"event": "token", "text": "", "extra": 1},
                   {"event": "error", "message": "", "extra": 1}]
        for value in (None, True, 1, [], {}):
            invalid.extend([{"event": "token", "text": value}, {"event": "error", "message": value}])
        for record in invalid:
            with self.subTest(record=record):
                parser = subject.EventStream()
                self.assert_failed(parser, lambda: parser.feed(frame(record)))

    def test_premature_eof(self):
        for wire in (b"", frame(TOKEN), DONE[:-1], b'{"event":"token","text":"\xe4'):
            parser = subject.EventStream()
            parser.feed(wire)
            self.assert_failed(parser, parser.finish)

    def test_wire_byte_limits(self):
        wire = frame({"event": "token", "text": "中🙂"})
        size = len(wire) - 1
        for split in range(len(wire) + 1):
            with self.subTest(split=split):
                parser = subject.EventStream(size)
                result = parser.feed(wire[:split]) + parser.feed(wire[split:])
                self.assertEqual(result, [{"event": "token", "text": "中🙂"}])
                parser.feed(DONE)
                self.assertEqual(parser.finish(), [])
        for complete in (True, False):
            parser = subject.EventStream(size - 1)
            self.assert_failed(parser, lambda: parser.feed(wire if complete else wire[:-1]))
        parser = subject.EventStream(size)
        self.assertEqual(parser.feed(wire[:-1]), [])
        self.assertEqual(parser.buffered_bytes, size)
        self.assert_failed(parser, lambda: parser.feed(b" "))
        parser = subject.EventStream(1)
        parser.feed(b"{")
        self.assertEqual(parser.buffered_bytes, 1)
        self.assert_failed(parser, lambda: parser.feed(b"}"))

    def test_large_chunk_small_frames(self):
        record = {"event": "token", "text": "x"}
        wire = frame(record)
        parser = subject.EventStream(len(wire) - 1)
        events = parser.feed(wire * 10000 + DONE)
        self.assertEqual(events[:-1], [record] * 10000)
        self.assertEqual(events[-1], {"event": "done", "text": "x" * 10000, "characters": 10000, "words": 1})
        self.assertEqual(parser.buffered_bytes, 0)

    def test_failed_feed_does_not_return_partial_list(self):
        parser = subject.EventStream()
        prior = parser.feed(frame({"event": "token", "text": "prior"}))
        self.assert_failed(parser, lambda: parser.feed(frame(TOKEN) + b"{}\n"))
        self.assertEqual(prior, [{"event": "token", "text": "prior"}])

    def test_error_after_text(self):
        parser = subject.EventStream()
        self.assertEqual(parser.feed(frame(TOKEN) + ERROR), [TOKEN, {"event": "error", "message": "出错"}])
        self.assertEqual(parser.state, "ERROR")
        self.assertEqual(parser.finish(), [])


if __name__ == "__main__":
    unittest.main()
