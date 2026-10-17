"""SARIF output for GitHub code scanning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_composition_review.cli import main
from agent_composition_review.sarif import findings_to_sarif
from agent_composition_review.scan import scan_path

FIXTURES = Path(__file__).parent / "fixtures"


def test_sarif_shape_and_rule_metadata() -> None:
    result = scan_path(FIXTURES / "acr001_positive.py")
    doc = findings_to_sarif(result.findings)
    assert doc["$schema"].endswith("sarif-2.1.0.json")
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    assert run["tool"]["driver"]["name"] == "acr"
    rule = run["tool"]["driver"]["rules"][0]
    assert rule["id"] == "ACR001"
    assert rule["defaultConfiguration"]["level"] == "error"
    assert rule["helpUri"].endswith("/docs/rules/ACR001.md")
    assert rule["properties"]["status"] == "grounded"
    finding = run["results"][0]
    assert finding["ruleId"] == "ACR001"
    assert finding["level"] == "error"
    assert finding["locations"][0]["physicalLocation"]["artifactLocation"]["uri"].endswith(
        "acr001_positive.py"
    )
    assert finding["partialFingerprints"]["primaryLocationLineHash"]


def test_sarif_cli_writes_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "out" / "acr.sarif"
    code = main(
        [
            "scan",
            str(FIXTURES / "acr001_positive.py"),
            "--sarif",
            "-o",
            str(output),
        ]
    )
    assert code == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["runs"][0]["results"][0]["ruleId"] == "ACR001"
    assert "wrote" in capsys.readouterr().out


def test_sarif_and_json_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["scan", str(FIXTURES / "acr001_positive.py"), "--json", "--sarif"])
    assert excinfo.value.code == 2
