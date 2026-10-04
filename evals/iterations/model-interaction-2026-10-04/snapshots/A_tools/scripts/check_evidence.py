#!/usr/bin/env python3
"""Check record consistency, not truth, causality, or architectural quality. Stdlib only."""

import argparse
import json
from pathlib import Path

TARGETS = {"current_contract", "earliest_observed_error", "decisive_cause",
           "architecture_review", "refactor_preservation"}
KINDS = {"plan", "action", "observation", "artifact", "statement"}


def check(record):
    errors = []
    if not isinstance(record, dict):
        return ["record must be an object"]

    def enum(value, choices, where):
        if not isinstance(value, str) or value not in choices:
            errors.append(f"{where}: invalid value")

    def text(value, where):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{where}: nonempty string required")

    def index(key):
        rows = record.get(key)
        if not isinstance(rows, list):
            errors.append(f"{key}: array required")
            return {}
        result = {}
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append(f"{key}[{i}]: object required")
                continue
            rid = row.get("id")
            if not isinstance(rid, str) or not rid.strip():
                errors.append(f"{key}[{i}]: nonempty id required")
            elif rid in result:
                errors.append(f"{key}: duplicate id {rid}")
            else:
                result[rid] = row
        return result

    def refs(value, allowed, where, nonempty=False):
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            errors.append(f"{where}: string array required")
            return []
        if nonempty and not value:
            errors.append(f"{where}: evidence reference required")
        if len(set(value)) != len(value):
            errors.append(f"{where}: duplicate reference")
        for v in value:
            if v not in allowed:
                errors.append(f"{where}: unknown reference {v}")
        return [v for v in value if v in allowed]

    enum(record.get("target"), TARGETS, "target")
    enum(record.get("verdict"), {"violation", "satisfied", "insufficient"}, "verdict")
    requirements, events, findings = (index(k) for k in ("requirements", "events", "findings"))
    if not requirements:
        errors.append("requirements: at least one requirement required")
    for rid, r in requirements.items():
        text(r.get("text"), f"requirement {rid}.text")
    for eid, e in events.items():
        enum(e.get("kind"), KINDS, f"event {eid}.kind")
        for key in ("source", "actor", "text"):
            text(e.get(key), f"event {eid}.{key}")
    for fid, f in findings.items():
        text(f.get("claim"), f"finding {fid}.claim")
        rid = f.get("requirement_id")
        if not isinstance(rid, str) or rid not in requirements:
            errors.append(f"finding {fid}: unknown requirement")
        evidence = refs(f.get("event_ids"), events, f"finding {fid}.event_ids", True)
        recovery = refs(f.get("recovery_event_ids"), events, f"finding {fid}.recovery_event_ids")
        intervention = refs(f.get("intervention_event_ids"), events, f"finding {fid}.intervention_event_ids")
        for key, choices in {
            "status": {"confirmed", "hypothesis", "risk", "ruled_out"},
            "execution": {"observed", "planned", "unknown", "not_applicable"},
            "recovery": {"recovered", "unrecovered", "unknown", "not_applicable"},
            "causal": {"untested", "intervention_supported", "not_applicable"},
        }.items():
            enum(f.get(key), choices, f"finding {fid}.{key}")
        if f.get("status") == "confirmed":
            if f.get("execution") in ("planned", "unknown"):
                errors.append(f"finding {fid}: confirmed cannot have planned/unknown execution")
            if evidence and all(events[e].get("kind") == "plan" for e in evidence):
                errors.append(f"finding {fid}: plans alone cannot confirm a violation")
        if f.get("recovery") == "recovered":
            if not recovery or all(events[e].get("kind") in ("plan", "statement") for e in recovery):
                errors.append(f"finding {fid}: recovered requires observation/action/artifact reference")
        if f.get("causal") == "intervention_supported":
            if not intervention or all(events[e].get("kind") in ("plan", "statement") for e in intervention):
                errors.append(f"finding {fid}: intervention_supported requires execution evidence")
    selected = refs(record.get("selected_ids"), findings, "selected_ids")
    if record.get("verdict") == "violation" and not any(
        findings[f].get("status") == "confirmed" for f in selected
    ):
        errors.append("violation verdict requires a selected confirmed finding")
    if record.get("verdict") == "satisfied" and selected:
        errors.append("satisfied verdict cannot select violation candidates; retain history unselected")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    try:
        errors = check(json.loads(args.record.read_text()))
    except (OSError, ValueError) as error:
        errors = [str(error)]
    print(json.dumps({"valid": not errors, "errors": errors,
                      "scope": "structural consistency only; no truth or causal verification"}, ensure_ascii=False))
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
