import app
p = app.EventStream()
assert p.feed(b'{"event":"token","text":"hello"}\n{"event":"done"}\n') == [
    {"event": "token", "text": "hello"},
    {"event": "done", "text": "hello", "characters": 5, "words": 1},
]
assert p.finish() == []
print("smoke passed")
