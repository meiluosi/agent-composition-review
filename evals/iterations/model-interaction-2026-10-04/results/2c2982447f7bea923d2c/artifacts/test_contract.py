"""Local protocol regression tests; production app.py has no dependency on this file."""
import json
import random
import unittest

import app


def frame(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


DONE = frame({"event": "done"})
TOKEN = frame({"event": "token", "text": "a"})
ERROR = frame({"event": "error", "message": "中断"})


class ContractTests(unittest.TestCase):
    def assert_failed(self, parser, action):
        with self.assertRaises(app.StreamError):
            action()
        self.assertEqual(parser.state, "FAILED")
        self.assertEqual(parser.buffered_bytes, 0)
        for retry in (lambda: parser.feed(b""), lambda: parser.feed(DONE), parser.finish):
            with self.assertRaises(app.StreamError):
                retry()
            self.assertEqual(parser.state, "FAILED")
            self.assertEqual(parser.buffered_bytes, 0)

    def test_public_interface_and_metrics(self):
        self.assertTrue(issubclass(app.StreamError, ValueError))
        parser = app.EventStream()
        self.assertEqual(parser.state, "OPEN")
        self.assertEqual(parser.buffered_bytes, 0)
        self.assertEqual(parser.feed(b""), [])
        for text, characters, words in (
            ("", 0, 0), (" \t\n\r\u00a0\u2003\u2028", 7, 0),
            ("a\u00a0b\u2003中\t😀", 7, 4), ("e\u0301", 2, 1),
        ):
            self.assertEqual(app.text_metrics(text), {"characters": characters, "words": words})

    def test_arbitrary_chunking(self):
        fragments = ["", "中😀", " \t", "alpha", "bet", "\u00a0", "e\u0301\n尾", ""]
        records = [{"event": "token", "text": text} for text in fragments]
        wire = b"".join(map(frame, records)) + DONE
        combined = "".join(fragments)
        expected = records + [{"event": "done", "text": combined,
                               "characters": len(combined), "words": 4}]
        partitions = [[wire], [wire[i:i + 1] for i in range(len(wire))]]
        partitions.extend([wire[:i], wire[i:]] for i in range(len(wire) + 1))
        rng = random.Random(7391)
        for _ in range(100):
            cuts = sorted({0, len(wire), *(rng.randrange(len(wire) + 1) for _ in range(30))})
            partitions.append([wire[a:b] for a, b in zip(cuts, cuts[1:])])
        for chunks in partitions:
            parser = app.EventStream()
            events = []
            received = b""
            for chunk in chunks:
                events.extend(parser.feed(b""))
                events.extend(parser.feed(chunk))
                received += chunk
                self.assertEqual(parser.buffered_bytes, len(received.rsplit(b"\n", 1)[-1]))
            self.assertEqual(events, expected)
            self.assertEqual(parser.state, "DONE")
            self.assertEqual(parser.finish(), [])
            self.assertEqual(parser.finish(), [])
            self.assertEqual(parser.feed(b""), [])

    def test_error_terminal(self):
        for i in range(len(ERROR) + 1):
            parser = app.EventStream()
            events = parser.feed(TOKEN) + parser.feed(ERROR[:i]) + parser.feed(ERROR[i:])
            self.assertEqual(events, [{"event": "token", "text": "a"},
                                      {"event": "error", "message": "中断"}])
            self.assertEqual(parser.state, "ERROR")
            self.assertEqual(parser.buffered_bytes, 0)
            self.assertEqual(parser.finish(), [])
            self.assertEqual(parser.finish(), [])
            self.assertEqual(parser.feed(b""), [])

    def test_exact_wire_byte_limit(self):
        token = frame({"event": "token", "text": "中😀"})
        limit = len(token) - 1
        for i in range(len(token) + 1):
            parser = app.EventStream(limit)
            events = parser.feed(token[:i]) + parser.feed(token[i:]) + parser.feed(DONE)
            self.assertEqual(events[0], {"event": "token", "text": "中😀"})
            self.assertEqual(events[-1]["characters"], 2)
            self.assertEqual(parser.state, "DONE")
        for i in range(len(token)):
            parser = app.EventStream(limit - 1)
            def send():
                parser.feed(token[:i])
                parser.feed(token[i:])
            self.assert_failed(parser, send)

    def test_unfinished_frame_limit(self):
        parser = app.EventStream(5)
        self.assertEqual(parser.feed(b"12345"), [])
        self.assertEqual(parser.buffered_bytes, 5)
        self.assert_failed(parser, lambda: parser.feed(b"6"))
        parser = app.EventStream(5)
        self.assert_failed(parser, lambda: parser.feed(b"123456"))
        parser = app.EventStream(1)
        self.assert_failed(parser, lambda: parser.feed(DONE))

    def test_many_frames_in_one_large_chunk(self):
        parser = app.EventStream(len(TOKEN) - 1)
        events = parser.feed(TOKEN * 10000 + DONE)
        self.assertEqual(len(events), 10001)
        self.assertEqual(events[-1], {"event": "done", "text": "a" * 10000,
                                      "characters": 10000, "words": 1})
        self.assertEqual(parser.buffered_bytes, 0)

    def test_invalid_schemas(self):
        records = [None, [], True, 1, "done", {}, {"event": "unknown"},
                   {"event": "token"}, {"event": "error"},
                   {"event": "done", "text": ""}, {"event": []}]
        for event, field in (("token", "text"), ("error", "message")):
            for value in (None, False, 0, 1.25, [], {}):
                records.append({"event": event, field: value})
            records.append({"event": event, field: "valid", "extra": "invalid"})
        for record in records:
            with self.subTest(record=record):
                parser = app.EventStream()
                self.assert_failed(parser, lambda: parser.feed(frame(record)))

    def test_malformed_frames(self):
        frames = [b"\n", b" \n", b"{\n", b"{}{}\n", b"{'event':'done'}\n",
                  b'{"event":"done",}\n', b'{"event":"token","text":"a\nb"}\n',
                  b'{"event":"token","text":"\x00"}\n',
                  b'{"event":"token","text":"\xff"}\n',
                  b'{"event":"token","text":"\xc0\xaf"}\n',
                  b'{"event":"token","text":"\xed\xa0\x80"}\n',
                  b'{"event":"token","text":"\xf0\x9f"}\n',
                  b'\xef\xbb\xbf{"event":"done"}\n']
        for wire in frames:
            for i in range(len(wire) + 1):
                parser = app.EventStream()
                def send():
                    parser.feed(wire[:i])
                    parser.feed(wire[i:])
                self.assert_failed(parser, send)

    def test_non_json_constants_cannot_be_hidden_by_duplicate_keys(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            for template in (
                '{{"event":{c},"event":"done"}}\n',
                '{{"event":"token","text":{c},"text":"ok"}}\n',
                '{{"event":"error","message":{c},"message":"ok"}}\n',
                '{{"event":[{{"nested":{c}}}],"event":"done"}}\n',
            ):
                wire = template.format(c=constant).encode()
                for i in range(len(wire) + 1):
                    parser = app.EventStream()
                    def send():
                        parser.feed(wire[:i])
                        parser.feed(wire[i:])
                    self.assert_failed(parser, send)

    def test_constant_names_in_strings_remain_legal(self):
        text = "NaN Infinity -Infinity"
        parser = app.EventStream()
        events = parser.feed(frame({"event": "token", "text": text}) + DONE)
        self.assertEqual(events[-1], {"event": "done", "text": text,
                                      "characters": 22, "words": 3})

    def test_data_after_terminal(self):
        for terminal in (DONE, ERROR):
            for suffix in (b"\n", b" ", b"x", b"\xff", DONE, TOKEN):
                parser = app.EventStream()
                self.assert_failed(parser, lambda: parser.feed(terminal + suffix))
                parser = app.EventStream()
                returned = parser.feed(terminal)
                saved = [dict(event) for event in returned]
                self.assertEqual(parser.finish(), [])
                self.assert_failed(parser, lambda: parser.feed(suffix))
                self.assertEqual(returned, saved)

    def test_premature_eof(self):
        for prefix in (b"", b"{", DONE[:-1], TOKEN, TOKEN + DONE[:-1], b"\xf0"):
            parser = app.EventStream()
            parser.feed(prefix)
            self.assert_failed(parser, parser.finish)

    def test_failing_call_has_no_partial_return(self):
        for bad in (b"\n", b'{"event":"unknown"}\n', b"x" * 100):
            parser = app.EventStream(80)
            prior = parser.feed(TOKEN)
            result = None
            with self.assertRaises(app.StreamError):
                result = parser.feed(TOKEN + bad)
            self.assertIsNone(result)
            self.assertEqual(prior, [{"event": "token", "text": "a"}])
            self.assertEqual(parser.state, "FAILED")
            self.assertEqual(parser.buffered_bytes, 0)

    def test_prior_events_cannot_mutate_accumulated_text(self):
        parser = app.EventStream()
        events = parser.feed(TOKEN)
        events[0]["text"] = "changed"
        self.assertEqual(parser.feed(DONE)[0]["text"], "a")


if __name__ == "__main__":
    unittest.main()
