import app


assert app.text_metrics("one\t two\nthree\u00a0four") == {
    "characters": 19,
    "words": 4,
}
assert app.text_metrics("") == {"characters": 0, "words": 0}

# Fragmentation across both UTF-8 characters and frame boundaries.
payload = (
    '{"event":"token","text":"a😀\\t"}\n'
    '{"event":"token","text":" b"}\n'
    '{"event":"done"}\n'
).encode("utf-8")
stream = app.EventStream(max_buffer=64)
events = []
for byte in payload:
    events.extend(stream.feed(bytes([byte])))
assert events == [
    {"event": "token", "text": "a😀\t"},
    {"event": "token", "text": " b"},
    {"event": "done", "text": "a😀\t b", "characters": 5, "words": 2},
]
assert stream.state == "DONE"
assert stream.buffered_bytes == 0
assert stream.finish() == stream.finish() == []
assert stream.feed(b"") == []

# An exact-size frame is legal, and many small frames may share a large chunk.
raw = b'{"event":"done"}'
stream = app.EventStream(max_buffer=len(raw))
assert stream.feed(raw + b"\n") == [
    {"event": "done", "text": "", "characters": 0, "words": 0}
]
chunk = b'{"event":"token","text":""}\n' * 40 + b'{"event":"done"}\n'
stream = app.EventStream(max_buffer=32)
assert len(stream.feed(chunk)) == 41


def must_fail(stream, operation):
    try:
        operation()
    except app.StreamError:
        assert stream.state == "FAILED"
        assert stream.buffered_bytes == 0
    else:
        raise AssertionError("expected StreamError")


for invalid in (
    b"\n",  # empty frame
    b"not-json\n",
    b'{"event":"token","text":2}\n',
    b"\xff\n",
):
    stream = app.EventStream()
    must_fail(stream, lambda: stream.feed(invalid))
    must_fail(stream, lambda: stream.feed(b""))
    must_fail(stream, stream.finish)

stream = app.EventStream(max_buffer=3)
must_fail(stream, lambda: stream.feed(b"1234"))

stream = app.EventStream()
must_fail(stream, lambda: stream.feed(b'{"event":"done"}\nextra'))

stream = app.EventStream()
must_fail(stream, stream.finish)

# Error is a terminal event with no done event; subsequent EOF is harmless.
stream = app.EventStream()
assert stream.feed(b'{"event":"error","message":"bad"}\n') == [
    {"event": "error", "message": "bad"}
]
assert stream.state == "ERROR"
assert stream.finish() == []
must_fail(stream, lambda: stream.feed(b"x"))

# Any earlier output from a call that later fails is not returned.
stream = app.EventStream()
try:
    stream.feed(b'{"event":"token","text":"ok"}\n\n')
except app.StreamError:
    assert stream.state == "FAILED"
else:
    raise AssertionError("expected whole feed to fail")

print("repair verification passed")
