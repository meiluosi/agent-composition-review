"""Shared data model for :command:`acr` scan and trace results.

The JSON emitted by ``acr scan --json`` and ``acr trace --json`` is a stable,
documented schema.  Everything in this module is deliberately plain data so it
can be serialised with :func:`json.dumps` and consumed by ``acr report``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1

#: Ordered from most to least severe.
SEVERITIES: tuple[str, ...] = ("high", "medium", "low")

SEVERITY_RANK: dict[str, int] = {"high": 3, "medium": 2, "low": 1}

#: ``--fail-on`` levels understood by ``acr scan``.
FAIL_ON_CHOICES: tuple[str, ...] = ("high", "review", "never")


def severity_counts(findings: list["Finding"]) -> dict[str, int]:
    """Count findings per severity, always returning every severity key."""

    counts = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        if finding.severity in counts:
            counts[finding.severity] += 1
    return counts


def rule_counts(findings: list["Finding"]) -> dict[str, int]:
    """Count findings per rule id in a deterministic order."""

    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.rule] = counts.get(finding.rule, 0) + 1
    return dict(sorted(counts.items()))


def sort_findings(findings: list["Finding"]) -> list["Finding"]:
    """Sort findings deterministically: file, line, rule, severity."""

    return sorted(
        findings,
        key=lambda finding: (
            finding.file,
            finding.line,
            finding.rule,
            -finding.severity_rank,
            finding.message,
        ),
    )


@dataclass(frozen=True)
class Rule:
    """Metadata for one diagnostic rule."""

    id: str
    severity: str
    title: str
    scope: str
    summary: str
    fix: str

    @property
    def docs(self) -> str:
        """Repository-relative documentation path for this rule."""

        return f"docs/rules/{self.id}.md"

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "severity": self.severity,
            "title": self.title,
            "scope": self.scope,
            "summary": self.summary,
            "fix": self.fix,
            "docs": self.docs,
        }


@dataclass
class Finding:
    """A single diagnostic emitted by a rule."""

    rule: str
    severity: str
    title: str
    message: str
    file: str
    line: int
    evidence: list[str] = field(default_factory=list)
    fix: str = ""
    docs: str = ""

    @property
    def severity_rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "evidence": list(self.evidence),
            "fix": self.fix,
            "docs": self.docs,
        }


@dataclass
class ScanResult:
    """Result of ``acr scan``."""

    root: str
    findings: list[Finding]
    files_scanned: int
    files_parsed: int
    files_skipped: int
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        counts = severity_counts(self.findings)
        return {
            "schema_version": SCHEMA_VERSION,
            "command": "scan",
            "root": self.root,
            "findings": [finding.to_dict() for finding in self.findings],
            "summary": {
                "files_scanned": self.files_scanned,
                "files_parsed": self.files_parsed,
                "files_skipped": self.files_skipped,
                "findings": len(self.findings),
                "by_severity": counts,
                "by_rule": rule_counts(self.findings),
                "errors": list(self.errors),
            },
        }


@dataclass
class TraceEvent:
    """One normalised event from a runtime trace."""

    id: str
    seq: int
    kind: str
    node: str | None = None
    tool: str | None = None
    step: int | str | None = None
    writes: dict[str, Any] | None = None
    args: Any = None
    result: Any = None
    ts: Any = None
    message: str | None = None
    final: bool = False
    initial: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "seq": self.seq,
            "kind": self.kind,
        }
        for name in ("node", "tool", "step", "writes", "args", "result", "ts", "message"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.final:
            payload["final"] = True
        if self.initial:
            payload["initial"] = True
        return payload


@dataclass
class TraceResult:
    """Result of ``acr trace``."""

    root: str
    findings: list[Finding]
    events: list[TraceEvent]
    input_format: str
    initial_state_keys: list[str] | None = None
    final_state_keys: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        counts = severity_counts(self.findings)
        return {
            "schema_version": SCHEMA_VERSION,
            "command": "trace",
            "root": self.root,
            "events": [event.to_dict() for event in self.events],
            "findings": [finding.to_dict() for finding in self.findings],
            "summary": {
                "events": len(self.events),
                "findings": len(self.findings),
                "by_severity": counts,
                "by_rule": rule_counts(self.findings),
                "input_format": self.input_format,
                "initial_state_keys": self.initial_state_keys,
                "final_state_keys": self.final_state_keys,
            },
        }
