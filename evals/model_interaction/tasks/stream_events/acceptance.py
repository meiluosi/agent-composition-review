"""Maintainer-side acceptance; run with the candidate app.py path."""
import contextlib
import importlib.util
import io
import json
import sys


def expect_error(kind, callback):
    try:
        callback()
    except kind:
        return
    raise AssertionError("expected " + kind.__name__)


def frame(event):
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def expected(texts):
    text = "".join(texts)
    return [{"event": "token", "text": value} for value in texts] + [
        {"event": "done", "text": text, "characters": len(text), "words": len(text.split())}]


def run_chunks(app, parts, limit=4096):
    parser = app.EventStream(max_buffer=limit)
    out = []
    for part in parts:
        out.extend(parser.feed(part))
        assert parser.buffered_bytes <= limit, "incomplete frame exceeded bound"
    assert parser.finish() == [], "finish duplicated terminal output"
    assert parser.state == "DONE" and parser.buffered_bytes == 0
    return out


def basic(app):
    texts = ["hello", " ", "world"]
    data = b"".join(frame({"event": "token", "text": s}) for s in texts) + frame({"event": "done"})
    assert run_chunks(app, [data]) == expected(texts)


def byte_boundaries(app):
    texts = ["雪🙂", " café", "\nnext"]
    data = b"".join(frame({"event": "token", "text": s}) for s in texts) + frame({"event": "done"})
    want = expected(texts)
    for split in range(len(data) + 1):
        assert run_chunks(app, [data[:split], b"", data[split:]]) == want, f"split at byte {split}"
    assert run_chunks(app, [bytes([b]) for b in data]) == want, "single-byte delivery"


def metrics(app):
    for texts in [[], [""], ["a\t", "b\n c"], ["  a", "  b  "], ["雪", "🙂\u2003z"], ["\r\n\t"]]:
        text = "".join(texts)
        assert app.text_metrics(text) == {"characters": len(text), "words": len(text.split())}
        data = b"".join(frame({"event": "token", "text": s}) for s in texts) + frame({"event": "done"})
        assert run_chunks(app, [data]) == expected(texts), repr(text)


def unfinished_buffer(app):
    parser = app.EventStream()
    data = frame({"event": "token", "text": "雪"})
    assert parser.feed(data[:-2]) == []
    assert parser.state == "OPEN" and parser.buffered_bytes == len(data) - 2
    assert parser.feed(data[-2:]) == [{"event": "token", "text": "雪"}]
    assert parser.buffered_bytes == 0
    done = frame({"event": "done"})
    assert parser.feed(done[:-1]) == []
    assert parser.state == "OPEN" and parser.buffered_bytes == len(done) - 1
    assert parser.feed(done[-1:]) == expected(["雪"])[-1:]
    assert parser.buffered_bytes == 0 and parser.finish() == []


def size_limits(app):
    payload = frame({"event": "token", "text": "雪" * 5})
    limit = len(payload) - 1
    done = frame({"event": "done"})
    assert run_chunks(app, [payload[:-1], payload[-1:] + done], limit) == expected(["雪" * 5])
    # The incoming chunk may exceed max_buffer if each frame fits.
    assert run_chunks(app, [payload * 20 + done], limit) == expected(["雪" * 5] * 20)
    for parts in [[payload], [payload[:-1]], [payload[:10], payload[10:]]]:
        p = app.EventStream(max_buffer=limit - 1)
        def too_large():
            for part in parts:
                p.feed(part)
        expect_error(app.StreamError, too_large)
        assert p.state == "FAILED" and p.buffered_bytes == 0


def error_terminal(app):
    p = app.EventStream()
    data = frame({"event": "token", "text": "partial"}) + frame({"event": "error", "message": "upstream unavailable"})
    assert p.feed(data) == [{"event": "token", "text": "partial"}, {"event": "error", "message": "upstream unavailable"}]
    assert p.state == "ERROR" and p.buffered_bytes == 0
    assert p.feed(b"") == [] and p.finish() == [] and p.finish() == []
    q = app.EventStream()
    assert q.feed(frame({"event": "error", "message": ""})) == [{"event": "error", "message": ""}]
    assert q.finish() == []


def terminal_once(app):
    for terminal in [{"event": "done"}, {"event": "error", "message": "x"}]:
        for trailing in [frame({"event": "done"}), frame({"event": "token", "text": "late"}), b" ", b"\n"]:
            for combined in [True, False]:
                p = app.EventStream()
                if combined:
                    expect_error(app.StreamError, lambda: p.feed(frame(terminal) + trailing))
                else:
                    result = p.feed(frame(terminal))
                    assert len(result) == 1
                    assert p.feed(b"") == [] and p.finish() == []
                    expect_error(app.StreamError, lambda: p.feed(trailing))
                assert p.state == "FAILED" and p.buffered_bytes == 0
                expect_error(app.StreamError, p.finish)


def malformed_frames(app):
    bad_frames = [b"\xff\n", b"{bad}\n", b"\n", b"[]\n", b"null\n", b'"hello"\n',
                  frame({"event": "token", "text": 3}), frame({"event": "token"}),
                  frame({"event": "done", "extra": 1}), frame({"event": "error", "message": 7}),
                  frame({"event": "unknown"}), frame({"text": "no type"}), frame({"event": ["done"]})]
    for bad in bad_frames:
        p = app.EventStream()
        expect_error(app.StreamError, lambda: p.feed(bad))
        assert p.state == "FAILED" and p.buffered_bytes == 0, repr(bad)
    # A frame can fit the wire bound yet exceed the JSON decoder's nesting limit.
    p = app.EventStream(max_buffer=25000)
    deep_invalid_event = b"[" * 10000 + b"0" + b"]" * 10000 + b"\n"
    expect_error(app.StreamError, lambda: p.feed(deep_invalid_event))
    assert p.state == "FAILED" and p.buffered_bytes == 0


def premature_eof(app):
    for data in [b"", frame({"event": "token", "text": "ok"}), b'{"event":"done"}', b"\xe9"]:
        p = app.EventStream()
        assert isinstance(p.feed(data), list)
        expect_error(app.StreamError, p.finish)
        assert p.state == "FAILED" and p.buffered_bytes == 0
        expect_error(app.StreamError, p.finish)


def poison_state(app):
    for failure in [lambda p: p.feed(b"bad\n"), lambda p: p.finish(), lambda p: p.feed(b"x" * 4097)]:
        p = app.EventStream()
        expect_error(app.StreamError, lambda: failure(p))
        for data in [b"", frame({"event": "done"}), frame({"event": "token", "text": "restart"})]:
            expect_error(app.StreamError, lambda: p.feed(data))
            assert p.state == "FAILED" and p.buffered_bytes == 0
        expect_error(app.StreamError, p.finish)


def failed_call_has_no_partial_output(app):
    p = app.EventStream()
    first = p.feed(frame({"event": "token", "text": "before"}))
    expect_error(app.StreamError, lambda: p.feed(frame({"event": "token", "text": "hidden"}) + b"bad\n"))
    assert first == [{"event": "token", "text": "before"}], "prior result mutated"
    assert p.state == "FAILED" and p.buffered_bytes == 0


def independent_streams(app):
    a, b = app.EventStream(), app.EventStream()
    assert a.feed(frame({"event": "token", "text": "A"})) == [{"event": "token", "text": "A"}]
    assert b.feed(frame({"event": "token", "text": "B"})) == [{"event": "token", "text": "B"}]
    assert a.feed(frame({"event": "done"})) == expected(["A"])[-1:]
    assert b.feed(frame({"event": "done"})) == expected(["B"])[-1:]
    assert a.finish() == [] and b.finish() == []


TESTS = [(fn.__name__, fn) for fn in [basic, byte_boundaries, metrics, unfinished_buffer,
    size_limits, error_terminal, terminal_once, malformed_frames, premature_eof,
    poison_state, failed_call_has_no_partial_output, independent_streams]]


def main():
    checks = []
    module = None
    load_error = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            spec = importlib.util.spec_from_file_location("app", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
    except BaseException as exc:
        load_error = exc
    for name, test in TESTS:
        try:
            if load_error is not None:
                raise load_error
            with contextlib.redirect_stdout(io.StringIO()):
                test(module)
            checks.append({"test": name, "passed": True})
        except BaseException as exc:
            checks.append({"test": name, "passed": False,
                           "error": type(exc).__name__ + ": " + str(exc)})
    print(json.dumps({"passed": all(c["passed"] for c in checks),
                      "tests_run": len(checks), "checks": checks}, ensure_ascii=True))


if __name__ == "__main__":
    main()
