"""Local contract checks; optionally pass 'original' to test the baseline."""
import importlib
import json
import sys
import unittest

subject = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app')


def wire(record):
    return json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'


DONE = wire({'event': 'done'})


class ContractTests(unittest.TestCase):
    def assert_failed(self, parser, action):
        with self.assertRaises(subject.StreamError):
            action()
        self.assertEqual(parser.state, 'FAILED')
        self.assertEqual(parser.buffered_bytes, 0)
        for retry in (lambda: parser.feed(b''), lambda: parser.feed(DONE), parser.finish):
            with self.assertRaises(subject.StreamError):
                retry()

    def test_metrics(self):
        for text, words in [('', 0), ('  ', 0), ('hello', 1), (' one  two ', 2),
                            ('one\ttwo\nthree', 3), ('甲\u3000乙\u00a0丙', 3),
                            ('a\r\nb\vc\fd', 4), ('🙂e\u0301', 1)]:
            with self.subTest(text=text):
                self.assertEqual(subject.text_metrics(text), {'characters': len(text), 'words': words})

    def test_all_two_chunk_splits_and_bytewise(self):
        fragments = ['', '甲🙂', '\t 乙', '\n', '丙', '丁', ' ']
        records = [{'event': 'token', 'text': text} for text in fragments]
        text = ''.join(fragments)
        expected = records + [{'event': 'done', 'text': text, 'characters': len(text), 'words': 3}]
        data = b''.join(map(wire, records)) + DONE
        partitions = [[data[:i], data[i:]] for i in range(len(data) + 1)]
        partitions.append([data[i:i+1] for i in range(len(data))])
        for chunks in partitions:
            parser = subject.EventStream()
            output = []
            pending = b''
            for chunk in chunks:
                output.extend(parser.feed(chunk))
                pending = (pending + chunk).rsplit(b'\n', 1)[-1]
                self.assertEqual(parser.buffered_bytes, len(pending))
            self.assertEqual(output, expected)
            self.assertEqual(parser.state, 'DONE')
            self.assertEqual(parser.finish(), [])
            self.assertEqual(parser.finish(), [])

    def test_empty_done(self):
        parser = subject.EventStream()
        self.assertEqual(parser.feed(DONE), [
            {'event': 'done', 'text': '', 'characters': 0, 'words': 0}])

    def test_error_terminal(self):
        parser = subject.EventStream()
        records = [{'event': 'token', 'text': 'partial'}, {'event': 'error', 'message': '失败'}]
        self.assertEqual(parser.feed(b''.join(map(wire, records))), records)
        self.assertEqual(parser.state, 'ERROR')
        self.assertEqual(parser.buffered_bytes, 0)
        self.assertEqual(parser.feed(b''), [])
        self.assertEqual(parser.finish(), [])
        self.assertEqual(parser.finish(), [])

    def test_frame_limit(self):
        frame = wire({'event': 'token', 'text': '甲🙂'})
        limit = len(frame) - 1
        for split in range(len(frame) + 1):
            parser = subject.EventStream(limit)
            output = parser.feed(frame[:split]) + parser.feed(frame[split:])
            self.assertEqual(output, [{'event': 'token', 'text': '甲🙂'}])
            parser.feed(DONE)
        for complete in (False, True):
            for split in range(len(frame)):
                parser = subject.EventStream(limit - 1)
                data = frame if complete else frame[:-1]
                def feed_parts():
                    parser.feed(data[:split])
                    parser.feed(data[split:])
                self.assert_failed(parser, feed_parts)
        parser = subject.EventStream(1)
        self.assertEqual(parser.feed(b'{'), [])
        self.assertEqual(parser.buffered_bytes, 1)
        self.assert_failed(parser, lambda: parser.feed(b'}'))

    def test_many_small_frames(self):
        frame = wire({'event': 'token', 'text': 'x'})
        parser = subject.EventStream(len(frame) - 1)
        events = parser.feed(frame * 1000 + DONE)
        self.assertEqual(len(events), 1001)
        self.assertEqual(events[:-1], [{'event': 'token', 'text': 'x'}] * 1000)
        self.assertEqual(events[-1], {'event': 'done', 'text': 'x' * 1000, 'characters': 1000, 'words': 1})

    def test_invalid_frames(self):
        bad = [b'\n', b' \n', b'\xff\n', b'{\n', b'{}{}\n', b'[]\n', b'null\n',
               b'{"event":"token","text":"literal\nnewline"}\n',
               b'{"event":"token","text":"\xc3"}\n']
        bad += [wire(record) for record in [
            {}, {'event': 'unknown'}, {'event': 'token'}, {'event': 'token', 'text': 1},
            {'event': 'token', 'text': None}, {'event': 'token', 'text': []},
            {'event': 'token', 'text': '', 'extra': True}, {'event': 'done', 'text': ''},
            {'event': 'error'}, {'event': 'error', 'message': False},
            {'event': 'error', 'message': '', 'extra': 1}, {'event': []}]]
        for data in bad:
            with self.subTest(data=data):
                parser = subject.EventStream()
                self.assert_failed(parser, lambda: parser.feed(data))

    def test_eof(self):
        for data in (b'', b'{', DONE[:-1], b'\xf0\x9f', wire({'event': 'token', 'text': 'ok'})):
            parser = subject.EventStream()
            self.assertEqual(parser.state, 'OPEN')
            self.assertEqual(parser.feed(b''), [])
            parser.feed(data)
            self.assert_failed(parser, parser.finish)

    def test_data_after_terminal(self):
        for terminal in (DONE, wire({'event': 'error', 'message': ''})):
            for tail in (b'\n', b' ', b'x', DONE, b'\xff'):
                for same_call in (False, True):
                    parser = subject.EventStream()
                    if same_call:
                        self.assert_failed(parser, lambda: parser.feed(terminal + tail))
                    else:
                        parser.feed(terminal)
                        self.assertEqual(parser.feed(b''), [])
                        parser.finish()
                        self.assert_failed(parser, lambda: parser.feed(tail))

    def test_failure_does_not_return_partial_events(self):
        parser = subject.EventStream()
        record = {'event': 'token', 'text': 'valid'}
        previous = parser.feed(wire(record))
        result = ['unchanged']
        def fail_call():
            result[:] = parser.feed(wire(record) + b'bad\n')
        self.assert_failed(parser, fail_call)
        self.assertEqual(result, ['unchanged'])
        self.assertEqual(previous, [record])


if __name__ == '__main__':
    unittest.main()
