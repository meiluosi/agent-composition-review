"""Incremental parser for the project's small event protocol."""
import json


class StreamError(ValueError):
    pass


def text_metrics(text):
    return {"characters": len(text), "words": len(text.split(" "))}


class EventStream:
    def __init__(self, max_buffer=4096):
        self.max_buffer = max_buffer
        self._buffer = bytearray()
        self._text = []
        self.state = "OPEN"

    @property
    def buffered_bytes(self):
        return len(self._buffer)

    def _fail(self, message):
        self.state = "FAILED"
        self._buffer.clear()
        raise StreamError(message)

    def _record(self, raw):
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._fail("invalid UTF-8 JSON frame")
        if not isinstance(record, dict):
            self._fail("frame must be an object")
        event = record.get("event")
        if event == "token" and set(record) == {"event", "text"} and isinstance(record["text"], str):
            self._text.append(record["text"])
            return {"event": "token", "text": record["text"]}
        if event == "done" and set(record) == {"event"}:
            self.state = "DONE"
            text = "".join(self._text)
            return {"event": "done", "text": text, **text_metrics(text)}
        if event == "error" and set(record) == {"event", "message"} and isinstance(record["message"], str):
            self.state = "ERROR"
            return {"event": "error", "message": record["message"]}
        self._fail("invalid event schema")

    def feed(self, data):
        if self.state == "FAILED":
            raise StreamError("stream has failed")
        if not data:
            return []
        if self.state != "OPEN":
            self._fail("data after terminal event")
        output = []
        start = 0
        while start < len(data):
            newline = data.find(b"\n", start)
            end = len(data) if newline < 0 else newline
            part = data[start:end]
            if len(self._buffer) + len(part) > self.max_buffer:
                self._fail("frame exceeds max_buffer")
            self._buffer.extend(part)
            if newline < 0:
                break
            raw = bytes(self._buffer)
            self._buffer.clear()
            output.append(self._record(raw))
            start = newline + 1
            if self.state != "OPEN" and start < len(data):
                self._fail("data after terminal event")
        return output

    def finish(self):
        if self.state == "FAILED":
            raise StreamError("stream has failed")
        if self.state in {"DONE", "ERROR"}:
            return []
        self._fail("EOF before a complete terminal event")
