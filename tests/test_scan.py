"""Tests for the AST scanner and its rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_composition_review.scan import ScanError, scan_path, scan_source

FIXTURES = Path(__file__).parent / "fixtures"


def scan_fixture(name: str):
    return scan_path(FIXTURES / name)


def findings_for(result, rule_id: str):
    return [finding for finding in result.findings if finding.rule == rule_id]


def test_acr001_positive_shared_key() -> None:
    result = scan_fixture("acr001_positive.py")
    findings = findings_for(result, "ACR001")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "high"
    assert finding.docs == "docs/rules/ACR001.md"
    assert finding.file.endswith("acr001_positive.py")
    assert finding.line > 0
    evidence = "\n".join(finding.evidence)
    assert "shared state key(s): 'b'" in evidence
    assert "parent state 'ParentState' declared at line" in evidence
    assert "child state 'ChildState' declared at line" in evidence
    assert "child graph compiled without output_schema" in evidence
    assert "add_node at line" in evidence
    assert "output_schema" in finding.fix
    # ACR003 is only a fallback when ACR001 does not already explain the node.
    assert findings_for(result, "ACR003") == []


def test_acr001_resolves_helper_returning_compiled_graph() -> None:
    """Real-world shape: the child is built by a helper and returned compiled."""

    result = scan_fixture("acr001_helper.py")
    findings = findings_for(result, "ACR001")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "high"
    assert finding.file.endswith("acr001_helper.py")
    assert "child state 'State' shares" in finding.message
    assert "compiled without output_schema" in "\n".join(finding.evidence)
    # ACR002 may also fire for the same fan-out; it is part of the same root cause.
    assert findings_for(result, "ACR002")


def test_acr001_helper_with_output_schema_not_reported() -> None:
    result = scan_fixture("acr001_helper_healthy.py")
    assert findings_for(result, "ACR001") == []
    assert findings_for(result, "ACR003") == []
    assert result.findings == []


def test_acr002_positive_parallel_writes() -> None:
    result = scan_fixture("acr002_positive.py")
    findings = findings_for(result, "ACR002")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "high"
    assert finding.docs == "docs/rules/ACR002.md"
    evidence = "\n".join(finding.evidence)
    assert "'b'" in evidence
    assert "no reducer" in evidence
    assert "node 'one'" in evidence and "node 'two'" in evidence
    assert "START" in evidence
    assert "Annotated" in finding.fix


def test_acr002_negative_reducer_not_reported() -> None:
    result = scan_fixture("acr002_negative_reducer.py")
    assert findings_for(result, "ACR002") == []
    assert result.findings == []


def test_acr002_negative_readonly_reducer() -> None:
    """ReadOnly[Annotated[..., reducer]] still has a reducer."""

    result = scan_fixture("acr002_negative_readonly_reducer.py")
    assert findings_for(result, "ACR002") == []
    assert result.findings == []


def test_acr003_subgraph_without_output_schema() -> None:
    result = scan_fixture("acr003_positive.py")
    assert findings_for(result, "ACR001") == []
    findings = findings_for(result, "ACR003")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "medium"
    assert finding.docs == "docs/rules/ACR003.md"
    evidence = "\n".join(finding.evidence)
    assert "child_only" in evidence
    assert "output_schema" in finding.fix


def test_acr004_cache_with_side_effect() -> None:
    result = scan_fixture("acr004_cache_side_effect.py")
    findings = findings_for(result, "ACR004")
    assert len(findings) == 1
    assert findings[0].severity == "medium"
    assert "lru_cache" in findings[0].message


def test_acr005_retry_with_side_effect() -> None:
    result = scan_fixture("acr005_retry_side_effect.py")
    findings = findings_for(result, "ACR005")
    assert len(findings) == 1
    assert findings[0].severity == "medium"
    assert "retry" in findings[0].message.lower()


def test_acr006_checkpointer_access() -> None:
    result = scan_fixture("acr006_checkpointer.py")
    findings = findings_for(result, "ACR006")
    assert len(findings) == 1
    assert findings[0].severity == "low"
    assert "get_state" in findings[0].message


def test_acr007_declared_keys_function_node() -> None:
    result = scan_fixture("acr007_declared_keys.py")
    findings = findings_for(result, "ACR007")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "medium"
    assert "undeclared_key" in finding.message
    assert "state schema 'State'" in finding.message
    assert "filtered" in finding.message or "silently" in finding.message
    assert finding.docs == "docs/rules/ACR007.md"


def test_acr007_declared_keys_class_node() -> None:
    """Class-based nodes (issue #2644) are analyzed through __call__."""

    result = scan_fixture("acr007_class_node.py")
    findings = findings_for(result, "ACR007")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "medium"
    assert "'zoo'" in finding.message
    assert "Node 'node_1'" in finding.message


def test_acr007_negative_declared_keys() -> None:
    result = scan_fixture("acr007_negative.py")
    assert findings_for(result, "ACR007") == []
    assert result.findings == []


def test_acr008_cache_on_anonymous_callable() -> None:
    result = scan_fixture("acr008_cache_identity.py")
    findings = findings_for(result, "ACR008")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "medium"
    assert "cache_policy" in finding.message
    assert "dynamic namespace" in finding.message
    assert finding.docs == "docs/rules/ACR008.md"


def test_acr008_negative_named_function() -> None:
    result = scan_fixture("acr008_negative_named.py")
    assert findings_for(result, "ACR008") == []


def test_acr008_lambda_cache() -> None:
    result = scan_fixture("acr008_lambda_cache.py")
    findings = findings_for(result, "ACR008")
    assert len(findings) == 1
    assert "dynamic namespace" in findings[0].message


def test_acr008_negative_without_cache_policy() -> None:
    result = scan_fixture("acr008_negative_without_policy.py")
    assert findings_for(result, "ACR008") == []


def test_acr009_retry_with_multiple_subgraphs() -> None:
    result = scan_fixture("acr009_retry_subgraphs.py")
    findings = findings_for(result, "ACR009")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "medium"
    assert "'research'" in finding.message and "'review'" in finding.message
    assert "retry_policy" in finding.message
    assert finding.docs == "docs/rules/ACR009.md"


def test_acr009_negative_single_subgraph() -> None:
    result = scan_fixture("acr009_negative_single_subgraph.py")
    assert findings_for(result, "ACR009") == []


def test_acr009_class_node_retry_two_subgraphs() -> None:
    result = scan_fixture("acr009_class_retry_subgraphs.py")
    findings = findings_for(result, "ACR009")
    assert len(findings) == 1
    assert "'research'" in findings[0].message and "'review'" in findings[0].message


def test_acr009_negative_two_subgraphs_without_retry() -> None:
    result = scan_fixture("acr009_negative_two_subgraphs_no_retry.py")
    assert findings_for(result, "ACR009") == []


def test_acr010_retry_self_loop_with_non_reducer_key() -> None:
    result = scan_fixture("acr010_retry_loop.py")
    findings = findings_for(result, "ACR010")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "medium"
    assert "self-loop" in finding.message
    assert "'count_b'" in finding.message
    assert finding.docs == "docs/rules/ACR010.md"


def test_acr010_negative_reducer_backed_loop() -> None:
    result = scan_fixture("acr010_negative_reducer.py")
    assert findings_for(result, "ACR010") == []


def test_scan_directory_aggregates_findings() -> None:
    result = scan_path(FIXTURES)
    rules = {finding.rule for finding in result.findings}
    assert {
        "ACR001",
        "ACR002",
        "ACR003",
        "ACR004",
        "ACR005",
        "ACR006",
        "ACR007",
        "ACR008",
        "ACR009",
        "ACR010",
    } <= rules
    assert result.files_parsed >= 7


def test_scan_reports_json_shape() -> None:
    result = scan_fixture("acr001_positive.py")
    payload = result.to_dict()
    assert payload["schema_version"] == 1
    assert payload["command"] == "scan"
    assert payload["root"].endswith("acr001_positive.py")
    finding = payload["findings"][0]
    assert set(finding) == {
        "rule",
        "severity",
        "title",
        "message",
        "file",
        "line",
        "evidence",
        "fix",
        "docs",
    }
    summary = payload["summary"]
    assert summary["findings"] == len(payload["findings"])
    assert summary["by_severity"]["high"] >= 1
    assert summary["by_rule"]["ACR001"] == 1


def test_scan_source_helper() -> None:
    source = (
        "from typing import TypedDict\n"
        "from langgraph.graph import END, START, StateGraph\n"
        "class S(TypedDict):\n"
        "    b: int\n"
        "def one(state: S) -> dict:\n"
        "    return {'b': 1}\n"
        "def two(state: S) -> dict:\n"
        "    return {'b': 2}\n"
        "g = StateGraph(S)\n"
        "g.add_node('one', one)\n"
        "g.add_node('two', two)\n"
        "g.add_edge(START, 'one')\n"
        "g.add_edge(START, 'two')\n"
    )
    findings = scan_source(source, "inline_fixture.py")
    assert [finding.rule for finding in findings] == ["ACR002"]


def test_scan_skips_non_langgraph_files(tmp_path: Path) -> None:
    plain = tmp_path / "plain.py"
    plain.write_text(
        "class StateGraph:\n"
        "    pass\n"
        "class S:\n"
        "    b = 1\n"
        "g = StateGraph()\n",
        encoding="utf-8",
    )
    result = scan_path(plain)
    assert result.findings == []


def test_scan_missing_path(tmp_path: Path) -> None:
    with pytest.raises(ScanError, match="not found"):
        scan_path(tmp_path / "missing.py")


def test_scan_syntax_error_single_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken_syntax.py"
    broken.write_text("def broken(:\n    pass\n", encoding="utf-8")
    with pytest.raises(ScanError, match="cannot parse"):
        scan_path(broken)
