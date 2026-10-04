import app

p = app.EventStream(max_buffer=32)
frames = b'{"event":"token","text":""}\n' * 100 + b'{"event":"done"}\n'
result = p.feed(frames)
assert len(result) == 101 and result[-1]["characters"] == 0
assert p.state == "DONE" and p.buffered_bytes == 0
assert p.feed(b"") == [] and p.finish() == []

q = app.EventStream(max_buffer=64)
raw = '{"event":"token","text":"雪🙂"}\n'.encode()
out = q.feed(raw[:27]) + q.feed(raw[27:]) + q.feed(b'{"event":"done"}\n')
assert out[-1] == {"event": "done", "text": "雪🙂", "characters": 2, "words": 1}
assert q.finish() == []

r = app.EventStream(max_buffer=15)
assert r.feed(b"{" + b" " * 14) == [] and r.buffered_bytes == 15
s = app.EventStream()
s.feed(b'{"event":"done"}\n')
try:
    s.feed(b"x")
except app.StreamError:
    assert s.state == "FAILED" and s.buffered_bytes == 0
else:
    raise AssertionError

t = app.EventStream()
try:
    t.finish()
except app.StreamError:
    assert t.state == "FAILED" and t.buffered_bytes == 0
else:
    raise AssertionError
print("boundary probes passed")

u = app.EventStream()
try:
    u.feed(b'{"event":"token","text":"a","text":"b"}\n')
except app.StreamError:
    assert u.state == "FAILED" and u.buffered_bytes == 0
else:
    raise AssertionError("duplicate field accepted")
try:
    u.feed(b"")
except app.StreamError:
    pass
else:
    raise AssertionError("failed parser reused")

# A completed frame exactly at the byte limit is accepted.
v = app.EventStream(max_buffer=len(b'{"event":"done"}'))
assert v.feed(b'{"event":"done"}\n') == [
    {"event": "done", "text": "", "characters": 0, "words": 0}
]
print("duplicate and exact-limit probes passed")
