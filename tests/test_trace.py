"""Tests for runtime trace analysis."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_composition_review.trace import TraceParseError, trace_path

FIXTURES = Path(__file__).parent / "fixtures"


def findings_for(result, rule_id: str):
    return [finding for finding in result.findings if finding.rule == rule_id]


def test_duplicate_tool_call() -> None:
    result = trace_path(FIXTURES / "trace_duplicate_tool.json")
    findings = findings_for(result, "ACR101")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "medium"
    assert finding.docs == "docs/rules/ACR101.md"
    evidence = "\n".join(finding.evidence)
    assert "e2" in evidence and "e4" in evidence
    assert "'search'" in finding.message


def test_parallel_writes_same_step() -> None:
    result = trace_path(FIXTURES / "trace_parallel_writes.json")
    rules = {finding.rule for finding in result.findings}
    assert {"ACR103", "ACR104", "ACR105"} <= rules

    acr103 = findings_for(result, "ACR103")[0]
    assert acr103.severity == "high"
    assert "left" in acr103.message and "right" in acr103.message
    evidence = "\n".join(acr103.evidence)
    assert "e3" in evidence and "e4" in evidence
    assert "'b'" in acr103.message

    acr104 = findings_for(result, "ACR104")[0]
    assert acr104.severity == "high"
    assert "e5" in "\n".join(acr104.evidence)

    acr105 = findings_for(result, "ACR105")[0]
    assert acr105.severity == "medium"
    assert "'b'" in acr105.message

    assert result.initial_state_keys == ["a", "b"]
    assert result.final_state_keys == ["a"]
    assert result.input_format == "unified"


def test_repeated_node_write() -> None:
    result = trace_path(FIXTURES / "trace_repeated_node.json")
    findings = findings_for(result, "ACR102")
    assert len(findings) == 1
    assert findings[0].severity == "medium"
    evidence = "\n".join(findings[0].evidence)
    assert "e2" in evidence and "e4" in evidence


def test_jsonl_input() -> None:
    result = trace_path(FIXTURES / "trace_events.jsonl")
    assert result.input_format == "jsonl"
    assert len(result.events) == 2
    assert result.events[0].kind == "node_start"
    assert result.findings == []


def test_langgraph_debug_stream_jsonl() -> None:
    """Raw LangGraph stream_mode="debug" JSONL maps to node_start/node_end."""

    result = trace_path(FIXTURES / "trace_langgraph_debug.jsonl")
    assert result.input_format == "jsonl"
    assert len(result.events) == 4
    assert [event.kind for event in result.events] == [
        "node_start",
        "node_start",
        "node_end",
        "node_end",
    ]
    assert [event.node for event in result.events] == [
        "child",
        "sibling",
        "sibling",
        "child",
    ]
    assert result.events[1].step == 1
    assert result.events[2].writes == {"b": "old|updated"}
    assert result.events[3].writes == {"a": 1, "b": "old", "s1": 4, "s2": -2}

    acr103 = findings_for(result, "ACR103")
    assert len(acr103) == 1
    assert acr103[0].severity == "high"
    assert "child" in acr103[0].message and "sibling" in acr103[0].message
    assert "'b'" in acr103[0].message


def test_resume_restarts_completed_node() -> None:
    result = trace_path(FIXTURES / "trace_resume_replay.json")
    findings = findings_for(result, "ACR106")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "medium"
    assert "gate" in finding.message
    assert "resume" in finding.message
    assert finding.docs == "docs/rules/ACR106.md"


def test_acr106_ignores_interrupted_node_end() -> None:
    """An interrupted task is not a completed task; resume may legitimately restart it."""

    result = trace_path(FIXTURES / "trace_resume_interrupted.json")
    assert findings_for(result, "ACR106") == []


def test_trace_json_shape() -> None:
    result = trace_path(FIXTURES / "trace_parallel_writes.json")
    payload = result.to_dict()
    assert payload["schema_version"] == 1
    assert payload["command"] == "trace"
    assert payload["events"][0]["id"] == "e1"
    assert payload["summary"]["input_format"] == "unified"
    finding = payload["findings"][0]
    assert "rule" in finding and "evidence" in finding and "docs" in finding


def test_parse_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("this is not json\n", encoding="utf-8")
    with pytest.raises(TraceParseError, match="invalid JSON"):
        trace_path(bad)


def test_empty_trace(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(TraceParseError, match="empty"):
        trace_path(empty)


def test_missing_trace(tmp_path: Path) -> None:
    with pytest.raises(TraceParseError, match="not found"):
        trace_path(tmp_path / "missing.json")


def test_tool_calls_with_different_args_not_duplicate() -> None:
    trace = (
        '[{"id": "e1", "kind": "tool_start", "tool": "search", "args": {"q": "a"}},'
        ' {"id": "e2", "kind": "tool_end", "tool": "search", "result": {"hits": 1}},'
        ' {"id": "e3", "kind": "tool_start", "tool": "search", "args": {"q": "b"}},'
        ' {"id": "e4", "kind": "tool_end", "tool": "search", "result": {"hits": 2}}]'
    )
    path = Path(__file__).parent / "fixtures" / "_generated_trace.json"
    path.write_text(trace, encoding="utf-8")
    try:
        result = trace_path(path)
    finally:
        path.unlink(missing_ok=True)
    assert findings_for(result, "ACR101") == []
