# Repair the parallel tool-result accumulator

`app.py` exposes `score(values, weight)` and `ToolBatch(call_specs)`. Correct contract violations while preserving these APIs. A conforming implementation may be left unchanged. Use only Python 3.12's standard library.

Tools finish in arbitrary order and can send retried results in later batches. `call_specs` is a list of exact objects `{"call_id": <nonempty string>, "weight": <integer>}` with unique IDs. An empty list is legal. Integers exclude booleans and may be zero, negative, or large. Invalid specifications raise `ValueError`.

`ingest(updates)` takes a list of result objects with one of these exact shapes:

- `{"call_id": <known ID>, "version": <positive integer>, "status":"success", "values": <list of integers>}`
- `{"call_id": <known ID>, "version": <positive integer>, "status":"error", "message": <string>}`

Integers exclude booleans. Empty values/messages are legal. Unknown IDs, missing/extra fields, incorrect types, or other statuses raise `ValueError`. Validation must also apply to stale updates before they can be ignored. Apply valid updates in list order: the largest version seen for each call wins; a lower version is ignored; an identical repeat of the current version is idempotent; different payloads for the same **current** version raise `ValueError`. Once a higher version exists, old-version payload conflicts need not be remembered. Version gaps are legal. Newer success may replace error, and newer error may replace success.

An ingest call is atomic. If any update is invalid or conflicts, no update from that call is retained, including any valid earlier items. The instance remains usable after rejection. Empty ingest lists are no-ops until finalization.

`snapshot()` returns a new list in the original `call_specs` order. Every row has exactly `call_id`, `version`, `status`, `value`, and `error`. An unseen call has `version=None`, `status="pending"`, `value=None`, `error=None`. Success has its version, `status="success"`, `value=score(values, weight)`, `error=None`; error has its version, `status="error"`, `value=None`, and the message as `error`. `score(values, weight)` is exactly the integer sum of all values multiplied by the weight, including negative values; an empty list scores zero.

`finalize()` requires every expected call to have a result; errors count as results. An incomplete finalize raises `ValueError` and leaves the batch open for further ingestion. A successful finalize seals the instance and returns the same shape as snapshot. Repeated finalize and snapshot calls remain valid and return fresh copies; **any ingest after successful finalization, including an empty list, raises `RuntimeError`**. Empty expected-call batches may finalize immediately.

The accumulator never mutates caller inputs. Later caller mutation of specifications or updates must not change retained state. Mutating a returned snapshot/finalized result must not affect later reads. Inputs and outputs have no shared mutable state with the accumulator. Expose no new required methods or arguments.
