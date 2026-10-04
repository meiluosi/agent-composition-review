import app

# UTF-8 code point may be split at every byte boundary.
wire = '{"event":"token","text":"é🙂"}\n{"event":"done"}\n'.encode()
for split in range(len(wire) + 1):
    parser = app.EventStream()
    events = parser.feed(wire[:split]) + parser.feed(wire[split:])
    assert events == [
        {"event": "token", "text": "é🙂"},
        {"event": "done", "text": "é🙂", "characters": 2, "words": 1},
    ]
    assert parser.state == "DONE" and parser.buffered_bytes == 0
    assert parser.finish() == parser.finish() == []

# The exact maximum frame length is accepted, and LF does not count.
done = b'{"event":"done"}'
parser = app.EventStream(max_buffer=len(done))
assert parser.feed(done + b'\n')[0]["event"] == "done"

# A single incoming chunk may contain much more than max_buffer in many frames.
token = b'{"event":"token","text":""}'
wire = (token + b'\n') * 80 + done + b'\n'
parser = app.EventStream(max_buffer=len(token))
events = parser.feed(wire)
assert len(events) == 81 and events[-1]["event"] == "done"

# Trailing bytes in the same call fail atomically and clear buffered data.
for tail in (b'x', b'\n', b'{}\n'):
    parser = app.EventStream()
    try:
        parser.feed(done + b'\n' + tail)
    except app.StreamError:
        pass
    else:
        raise AssertionError("trailing bytes accepted")
    assert parser.state == "FAILED" and parser.buffered_bytes == 0
    for operation in (lambda: parser.feed(b''), parser.finish):
        try:
            operation()
        except app.StreamError:
            pass
        else:
            raise AssertionError("failed parser was reusable")

# Invalid frame and premature EOF fail and clear pending bytes.
for wire in (b'\n', b'{bad}\n', b'{"event":"done"}\n', b'\xff\n'):
    parser = app.EventStream()
    try:
        if wire == b'{"event":"done"}\n':
            parser.feed(b'{"event":')
            parser.finish()
        else:
            parser.feed(wire)
    except app.StreamError:
        pass
    else:
        raise AssertionError("invalid or incomplete stream accepted")
    assert parser.state == "FAILED" and parser.buffered_bytes == 0

# Unicode whitespace metrics follow str.split semantics.
assert app.text_metrics("a\u2003b\n") == {"characters": 4, "words": 2}
print("review checks passed")

# A frame one byte over the limit fails, including a frame completed in this call.
for suffix in (b'', b'\n'):
    parser = app.EventStream(max_buffer=len(done) - 1)
    try:
        parser.feed(done + suffix)
    except app.StreamError:
        pass
    else:
        raise AssertionError("oversized frame accepted")
    assert parser.state == "FAILED" and parser.buffered_bytes == 0

# Error is terminal and includes exactly the supplied message.
parser = app.EventStream()
assert parser.feed(b'{"event":"error","message":"stop"}\n') == [
    {"event": "error", "message": "stop"}
]
assert parser.state == "ERROR" and parser.finish() == []

# Schema is exact; malformed JSON constants and duplicate fields are rejected.
for frame in (
    b'{"event":"token","text":"x","extra":1}\n',
    b'{"event":"token","text":"x","text":"y"}\n',
    b'{"event":"token","text":NaN}\n',
):
    parser = app.EventStream()
    try:
        parser.feed(frame)
    except app.StreamError:
        pass
    else:
        raise AssertionError("invalid schema or JSON accepted")
    assert parser.state == "FAILED" and parser.buffered_bytes == 0
print("additional checks passed")
