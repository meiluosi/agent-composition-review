"""Combine versioned results from independently completing tools."""
from copy import deepcopy


def score(values, weight):
    return sum(values) * weight


class ToolBatch:
    def __init__(self, call_specs):
        if not isinstance(call_specs, list):
            raise ValueError("call_specs must be a list")
        seen = set()
        for item in call_specs:
            if (not isinstance(item, dict) or set(item) != {"call_id", "weight"}
                    or not isinstance(item["call_id"], str) or not item["call_id"]
                    or type(item["weight"]) is not int or item["call_id"] in seen):
                raise ValueError("invalid call specification")
            seen.add(item["call_id"])
        self._specs = deepcopy(call_specs)
        self._known = seen
        self._latest = {}
        self._sealed = False

    def _validate(self, item):
        if (not isinstance(item, dict) or not isinstance(item.get("call_id"), str)
                or item["call_id"] not in self._known):
            raise ValueError("unknown call")
        if type(item.get("version")) is not int or item["version"] < 1:
            raise ValueError("invalid version")
        if item.get("status") == "success":
            if (set(item) != {"call_id", "version", "status", "values"}
                    or not isinstance(item["values"], list)
                    or any(type(v) is not int for v in item["values"])):
                raise ValueError("invalid success")
        elif item.get("status") == "error":
            if (set(item) != {"call_id", "version", "status", "message"}
                    or not isinstance(item["message"], str)):
                raise ValueError("invalid error")
        else:
            raise ValueError("invalid status")

    def ingest(self, updates):
        if self._sealed:
            raise RuntimeError("batch is finalized")
        if not isinstance(updates, list):
            raise ValueError("updates must be a list")
        # Build the candidate state separately so a rejected batch cannot
        # partially update the live accumulator.
        staged = deepcopy(self._latest)
        for item in updates:
            self._validate(item)
            key = item["call_id"]
            previous = staged.get(key)
            if previous is not None:
                if item["version"] == previous["version"]:
                    if item != previous:
                        raise ValueError("conflicting result at same version")
                    continue
            staged[key] = deepcopy(item)
        self._latest = staged

    def snapshot(self):
        output = []
        for spec in self._specs:
            item = self._latest.get(spec["call_id"])
            row = {"call_id": spec["call_id"], "version": None,
                   "status": "pending", "value": None, "error": None}
            if item is not None:
                row.update(version=item["version"], status=item["status"])
                if item["status"] == "success":
                    row["value"] = score(item["values"], spec["weight"])
                else:
                    row["error"] = item["message"]
            output.append(row)
        return output

    def finalize(self):
        if len(self._latest) != len(self._specs):
            raise ValueError("results are incomplete")
        self._sealed = True
        return self.snapshot()
