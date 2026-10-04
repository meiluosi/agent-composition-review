# Repair the streamed tool-output adapter

`app.py` exposes `StreamError(ValueError)`, `text_metrics(text)`, and `EventStream(max_buffer=4096)`. Correct any behavior that violates the contract below. Preserve the public API. A conforming implementation may be left unchanged. Use only Python 3.12's standard library.

This is a deliberately small **LF-delimited JSON protocol**, not the full SSE protocol. Input to `feed` is `bytes`; its boundaries may fall anywhere, including inside a UTF-8 character or between any two bytes of a frame. Every frame is exactly one UTF-8 JSON object followed by LF (`b"\n"`). Literal newlines inside JSON strings must be escaped. Empty lines are invalid. Fields must match one of these exact shapes:

- `{"event":"token","text":<string>}` emits the same two fields. Text fragments are concatenated in arrival order; empty fragments are legal.
- `{"event":"done"}` emits `{"event":"done","text":<concatenated text>,"characters":<character count>,"words":<word count>}`. Character count is Python `len(text)`; word count is `len(text.split())`, so runs of any Unicode whitespace separate words. `text_metrics(text)` exposes those same two counts independently.
- `{"event":"error","message":<string>}` emits those two fields and ends the stream without a done event.

`feed(data)` returns a list of all completed events from that call, in order. An incomplete frame is retained for later calls. `state` is initially `"OPEN"`, becomes `"DONE"` or `"ERROR"` at the corresponding terminal event, and becomes `"FAILED"` on a protocol failure. Each successful stream has exactly one terminal event. Any bytes after a terminal event, including in the same call, are a failure. Empty feeds after a terminal event return `[]`. `finish()` marks transport EOF: it returns `[]` after DONE/ERROR, including on repeat calls, and fails if the stream is still OPEN (whether it has pending bytes or is simply missing a terminal event).

A malformed UTF-8/JSON frame, invalid event schema, oversized frame, premature EOF, or data after termination raises `StreamError`, sets state to FAILED, and clears the frame buffer. A failing feed returns no partial event list. Previously returned events remain valid. Once FAILED, every subsequent `feed` (even empty) and `finish` raises `StreamError`; the parser cannot be reused.

`max_buffer` is a positive integer supplied by the caller. It bounds the number of **wire bytes in each frame excluding LF**, including both complete and unfinished frames. A frame exactly at the limit is allowed. A chunk containing many small frames can be much larger than this limit and must still work. `buffered_bytes` reports the current incomplete frame's byte count and is zero after terminal/failure. This bound concerns the frame buffer, not the accumulated output text or the caller-owned incoming chunk. Caller-provided arguments have the documented types; do not add a new interface or change the wire format.
