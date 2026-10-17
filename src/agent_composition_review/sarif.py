"""SARIF 2.1.0 output for ``acr scan``.

SARIF lets GitHub code scanning render ACR findings as annotations in the
Security tab / pull-request review. This module only serializes findings; it
does not add new diagnostics.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .model import Finding
from .rules import get_rule

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_VERSION = "2.1.0"
DOCS_BASE = "https://github.com/meiluosi/agent-composition-review/blob/main/docs/rules"

_LEVELS = {"high": "error", "medium": "warning", "low": "note"}


def _artifact_uri(file: str) -> str:
    """Return a repository-relative, forward-slash URI when possible."""

    path = Path(file)
    if path.is_absolute():
        try:
            path = path.relative_to(Path.cwd())
        except ValueError:
            path = Path(path.name)
    text = path.as_posix()
    return text[2:] if text.startswith("./") else text


def _fingerprint(finding: Finding) -> str:
    raw = "|".join(
        (finding.rule, finding.file, str(finding.line), finding.message)
    ).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:32]


def _rule_descriptor(finding: Finding) -> dict[str, Any]:
    rule = get_rule(finding.rule)
    descriptor: dict[str, Any] = {
        "id": rule.id,
        "name": rule.title,
        "shortDescription": {"text": rule.title},
        "fullDescription": {"text": rule.summary},
        "helpUri": f"{DOCS_BASE}/{rule.id}.md",
        "defaultConfiguration": {"level": _LEVELS.get(rule.severity, "note")},
        "properties": {
            "status": rule.status,
            "severity": rule.severity,
        },
    }
    return descriptor


def findings_to_sarif(findings: Iterable[Finding]) -> dict[str, Any]:
    """Build a SARIF document for the given findings."""

    items = sorted(
        findings,
        key=lambda finding: (
            _artifact_uri(finding.file),
            finding.line,
            finding.rule,
            finding.message,
        ),
    )
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for finding in items:
        rules.setdefault(finding.rule, _rule_descriptor(finding))
        status_tag = "" if finding.status == "grounded" else " [experimental]"
        message = finding.message
        if finding.fix:
            message += "\n\nfix: " + finding.fix
        if status_tag:
            message += f"\n\nrule status:{status_tag}"
        results.append(
            {
                "ruleId": finding.rule,
                "level": _LEVELS.get(finding.severity, "note"),
                "message": {"text": message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": _artifact_uri(finding.file)},
                            "region": {"startLine": max(1, finding.line)},
                        }
                    }
                ],
                "partialFingerprints": {
                    "primaryLocationLineHash": _fingerprint(finding)
                },
                "properties": {
                    "status": finding.status,
                    "severity": finding.severity,
                },
            }
        )

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "acr",
                        "fullName": "Agent Composition Review",
                        "version": __version__,
                        "informationUri": "https://github.com/meiluosi/agent-composition-review",
                        "rules": [rules[key] for key in sorted(rules)],
                    }
                },
                "automationDetails": {"id": "acr/scan"},
                "columnKind": "utf16CodeUnits",
                "invocations": [{"executionSuccessful": True}],
                "results": results,
            }
        ],
    }


def render_sarif(findings: Iterable[Finding]) -> str:
    return json.dumps(findings_to_sarif(findings), indent=2, ensure_ascii=False)
