import importlib.util
import pathlib
import sys
import tempfile

spec = importlib.util.spec_from_file_location("app", pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "app.py"))
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)
with tempfile.TemporaryDirectory() as directory:
    queue = app.Queue(pathlib.Path(directory) / "queue.db")
    queue.put("one", {"text": "hello"})
    claim = queue.claim("worker", now=0, lease_seconds=5)
    assert claim["payload"] == {"text": "hello"}
    assert queue.ack(claim["receipt"], now=1) is True
    assert queue.claim("worker", now=2, lease_seconds=5) is None
print("smoke passed")
