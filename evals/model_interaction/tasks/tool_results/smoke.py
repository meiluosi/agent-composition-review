import app
batch = app.ToolBatch([{"call_id": "a", "weight": 2}])
batch.ingest([{"call_id": "a", "version": 1, "status": "success", "values": [3]}])
assert batch.finalize() == [{"call_id": "a", "version": 1, "status": "success", "value": 6, "error": None}]
print("smoke passed")
