import app


def result(call_id, version, values):
    return {"call_id": call_id, "version": version, "status": "success", "values": values}


def row(call_id, version, status, value=None, error=None):
    return {"call_id": call_id, "version": version, "status": status,
            "value": value, "error": error}


assert app.score([4, -7, 2], -3) == 3
assert app.score([], 10**30) == 0

specs = [{"call_id": "a", "weight": 2}, {"call_id": "b", "weight": -1}]
batch = app.ToolBatch(specs)
specs[0]["weight"] = 99
first = result("a", 4, [3])
batch.ingest([first])
first["values"].append(100)
batch.ingest([result("a", 2, [500])])  # Valid stale retry is ignored.
assert batch.snapshot() == [row("a", 4, "success", 6), row("b", None, "pending")]

# A stale payload conflict is ignored, but stale updates still undergo validation.
batch.ingest([result("a", 1, [999])])
before = batch.snapshot()
try:
    batch.ingest([{"call_id": "a", "version": 1, "status": "success", "values": [1, True]}])
except ValueError:
    pass
else:
    raise AssertionError("malformed stale update accepted")
assert batch.snapshot() == before

# A conflict after another staged update rejects the entire ingest call.
try:
    batch.ingest([result("b", 1, [7]), result("a", 4, [8])])
except ValueError:
    pass
else:
    raise AssertionError("same-version conflict accepted")
assert batch.snapshot() == before

# The rejected batch did not poison later valid ingestion.
batch.ingest([result("b", 1, [7]), result("a", 5, [10])])
snapshot = batch.snapshot()
assert snapshot == [row("a", 5, "success", 20), row("b", 1, "success", -7)]
snapshot[0]["value"] = -100
snapshot.append({})
assert batch.snapshot() == [row("a", 5, "success", 20), row("b", 1, "success", -7)]
final = batch.finalize()
final[1]["status"] = "error"
assert batch.finalize() == [row("a", 5, "success", 20), row("b", 1, "success", -7)]
try:
    batch.ingest([])
except RuntimeError:
    pass
else:
    raise AssertionError("ingest after finalize accepted")

# Incomplete finalization leaves the instance open.
incomplete = app.ToolBatch([{"call_id": "x", "weight": 1}])
try:
    incomplete.finalize()
except ValueError:
    pass
else:
    raise AssertionError("incomplete finalize accepted")
incomplete.ingest([{"call_id": "x", "version": 1, "status": "error", "message": ""}])
assert incomplete.finalize() == [row("x", 1, "error", error="")]

transitions = app.ToolBatch([{"call_id": "t", "weight": 3}])
transitions.ingest([result("t", 1, [2])])
transitions.ingest([{"call_id": "t", "version": 2, "status": "error", "message": "failed"}])
assert transitions.snapshot() == [row("t", 2, "error", error="failed")]
transitions.ingest([result("t", 3, [])])
assert transitions.snapshot() == [row("t", 3, "success", 0)]

# Empty batches can finalize immediately and remain readable.
empty = app.ToolBatch([])
assert empty.finalize() == []
assert empty.snapshot() == []
print("review checks passed")
