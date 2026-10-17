"""CLI behaviour: JSON schemas, exit codes, report, rules, version."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_composition_review.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
ACR001_FIXTURE = FIXTURES / "acr001_positive.py"
ACR003_FIXTURE = FIXTURES / "acr003_positive.py"
TRACE_PARALLEL = FIXTURES / "trace_parallel_writes.json"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == "acr 0.1.1"


def test_scan_json_schema_and_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["scan", str(ACR001_FIXTURE), "--json"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["command"] == "scan"
    assert payload["root"].endswith("acr001_positive.py")
    assert payload["summary"]["by_rule"]["ACR001"] == 1
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
    assert finding["docs"] == "docs/rules/ACR001.md"
    assert finding["file"].endswith("acr001_positive.py")
    assert isinstance(finding["line"], int)


def test_scan_text_output(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["scan", str(ACR001_FIXTURE)])
    assert code == 1
    out = capsys.readouterr().out
    assert "ACR scan" not in out  # stable, lowercase product name
    assert "acr scan -" in out
    assert "HIGH" in out
    assert "ACR001" in out
    assert "evidence:" in out
    assert "fix:" in out
    assert "docs: docs/rules/ACR001.md" in out


def test_scan_fail_on_levels(capsys: pytest.CaptureFixture[str]) -> None:
    # Medium-only fixture: default high threshold exits 0, review exits 1.
    assert main(["scan", str(ACR003_FIXTURE)]) == 0
    capsys.readouterr()
    assert main(["scan", str(ACR003_FIXTURE), "--fail-on", "review"]) == 1
    capsys.readouterr()
    assert main(["scan", str(ACR003_FIXTURE), "--fail-on", "never"]) == 0
    capsys.readouterr()
    assert main(["scan", str(ACR001_FIXTURE), "--fail-on", "never"]) == 0
    capsys.readouterr()


def test_scan_missing_path_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["scan", str(FIXTURES / "does_not_exist.py")])
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_scan_invalid_fail_on_exits_2() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["scan", str(ACR001_FIXTURE), "--fail-on", "sometimes"])
    assert excinfo.value.code == 2


def test_trace_json_and_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["trace", str(TRACE_PARALLEL), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["command"] == "trace"
    assert len(payload["events"]) == 5
    assert payload["summary"]["input_format"] == "unified"
    rules = {finding["rule"] for finding in payload["findings"]}
    assert {"ACR103", "ACR104", "ACR105"} <= rules


def test_trace_text_output(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["trace", str(TRACE_PARALLEL)])
    assert code == 0
    out = capsys.readouterr().out
    assert "timeline:" in out
    assert "ACR103" in out
    assert "e3" in out


def test_trace_parse_error_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code = main(["trace", str(bad)])
    assert code == 2
    assert "acr trace:" in capsys.readouterr().err


def test_report_writes_markdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scan", str(ACR001_FIXTURE), "--json"]) == 1
    scan_json = tmp_path / "scan.json"
    scan_json.write_text(capsys.readouterr().out, encoding="utf-8")

    assert main(["trace", str(TRACE_PARALLEL), "--json"]) == 0
    trace_json = tmp_path / "trace.json"
    trace_json.write_text(capsys.readouterr().out, encoding="utf-8")

    output = tmp_path / "report.md"
    code = main(
        [
            "report",
            "--scan",
            str(scan_json),
            "--trace",
            str(trace_json),
            "-o",
            str(output),
        ]
    )
    assert code == 0
    assert "wrote" in capsys.readouterr().out
    markdown = output.read_text(encoding="utf-8")
    assert markdown.startswith("# Agent Composition Review")
    assert "ACR001" in markdown
    assert "ACR103" in markdown
    assert "Trace timeline" in markdown
    assert "docs/rules/ACR001.md" in markdown


def test_report_invalid_input_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trace_json = tmp_path / "trace.json"
    trace_json.write_text("{}", encoding="utf-8")
    code = main(
        ["report", "--scan", str(trace_json), "--trace", str(trace_json)]
    )
    assert code == 2
    assert "acr report:" in capsys.readouterr().err


def test_rules_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["rules"]) == 0
    out = capsys.readouterr().out
    for rule_id in (
        "ACR001",
        "ACR002",
        "ACR003",
        "ACR004",
        "ACR005",
        "ACR006",
        "ACR007",
        "ACR008",
        "ACR009",
        "ACR101",
        "ACR102",
        "ACR103",
        "ACR104",
        "ACR105",
    ):
        assert rule_id in out
    assert "docs/rules/ACR001.md" in out


def test_no_command_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().err
