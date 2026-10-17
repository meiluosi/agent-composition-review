"""One-page Markdown report generation for :command:`acr report`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import SCHEMA_VERSION, severity_counts


class ReportError(RuntimeError):
    """Raised when a scan/trace JSON result cannot be used for a report."""


def load_result(path: str | Path, command: str) -> dict[str, Any]:
    """Load a previously generated ``acr scan --json`` / ``acr trace --json`` file."""

    target = Path(path)
    if not target.exists():
        raise ReportError(f"result file not found: {path}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ReportError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReportError(f"invalid JSON in {path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ReportError(f"{path} is not an acr {command} JSON object")
    if payload.get("command") != command:
        raise ReportError(
            f"{path} is not an acr {command} result (command={payload.get('command')!r})"
        )
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ReportError(
            f"{path} uses unsupported schema_version "
            f"{payload.get('schema_version')!r} (expected {SCHEMA_VERSION})"
        )
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ReportError(f"{path} has no 'findings' list")
    return payload


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _summary_table(
    scan: dict[str, Any] | None, trace: dict[str, Any] | None
) -> list[str]:
    rows = [
        "| Source | Input | Findings | High | Medium | Low |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for label, payload in (("static scan", scan), ("runtime trace", trace)):
        if payload is None:
            continue
        findings = payload.get("findings", [])
        counts = {"high": 0, "medium": 0, "low": 0}
        for finding in findings:
            severity = finding.get("severity")
            if severity in counts:
                counts[severity] += 1
        root = payload.get("root", "")
        rows.append(
            f"| {label} | `{_cell(root)}` | {len(findings)} | {counts['high']} | "
            f"{counts['medium']} | {counts['low']} |"
        )
    return rows


def _finding_section(title: str, findings: list[dict[str, Any]]) -> list[str]:
    lines = [f"## {title}", ""]
    if not findings:
        lines.append("_No findings._")
        lines.append("")
        return lines
    lines.extend(
        [
            "| Rule | Severity | Location | Finding |",
            "| --- | --- | --- | --- |",
        ]
    )
    for finding in findings:
        location = f"`{_cell(finding.get('file', ''))}:{finding.get('line', 0)}`"
        lines.append(
            f"| {finding.get('rule', '')} | {finding.get('severity', '')} | "
            f"{location} | {_cell(finding.get('title', ''))} |"
        )
    lines.append("")
    for finding in findings:
        rule = finding.get("rule", "?")
        lines.append(f"### {rule} - {finding.get('title', '')}")
        lines.append("")
        lines.append(
            f"- **Severity:** {finding.get('severity', '')}"
        )
        lines.append(
            f"- **Location:** `{finding.get('file', '')}:{finding.get('line', 0)}`"
        )
        lines.append(f"- **Message:** {finding.get('message', '')}")
        evidence = finding.get("evidence") or []
        if evidence:
            lines.append("- **Evidence:**")
            for item in evidence:
                lines.append(f"  - {item}")
        lines.append(f"- **Fix:** {finding.get('fix', '')}")
        docs = finding.get("docs", "")
        if docs:
            lines.append(f"- **Docs:** [{docs}]({docs})")
        lines.append("")
    return lines


def _timeline_section(trace: dict[str, Any]) -> list[str]:
    events = trace.get("events") or []
    lines = ["## Trace timeline", ""]
    if not events:
        lines.append("_No events._")
        lines.append("")
        return lines
    lines.extend(
        [
            "| # | Event | Kind | Node | Tool | Detail |",
            "| ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for event in events:
        detail_parts: list[str] = []
        if event.get("step") is not None:
            detail_parts.append(f"step={event['step']}")
        if event.get("writes"):
            detail_parts.append(f"writes={_short(event['writes'])}")
        if event.get("args") is not None:
            detail_parts.append(f"args={_short(event['args'])}")
        if event.get("result") is not None:
            detail_parts.append(f"result={_short(event['result'])}")
        if event.get("message"):
            detail_parts.append(f"message={_short(event['message'])}")
        if event.get("final"):
            detail_parts.append("final=true")
        lines.append(
            f"| {event.get('seq', '')} | {event.get('id', '')} | "
            f"{event.get('kind', '')} | {_cell(event.get('node', ''))} | "
            f"{_cell(event.get('tool', ''))} | {_cell('; '.join(detail_parts))} |"
        )
    lines.append("")
    summary = trace.get("summary") or {}
    if summary.get("initial_state_keys") is not None:
        lines.append(
            f"Input state keys: {summary.get('initial_state_keys')}; final state "
            f"keys: {summary.get('final_state_keys')}"
        )
        lines.append("")
    return lines


def _short(value: Any, limit: int = 80) -> str:
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def build_report(
    scan: dict[str, Any] | None,
    trace: dict[str, Any] | None,
) -> str:
    """Build the full Markdown report from scan and/or trace JSON results."""

    if scan is None and trace is None:
        raise ReportError("a report needs at least one of scan/trace results")

    lines = [
        "# Agent Composition Review",
        "",
        "Composition diagnostics generated by `acr report`. Static findings come "
        "from `acr scan`; runtime findings come from `acr trace`.",
        "",
        "## Summary",
        "",
    ]
    lines.extend(_summary_table(scan, trace))
    lines.append("")

    if scan is not None:
        lines.extend(
            _finding_section(
                "Static analysis findings (acr scan)",
                list(scan.get("findings") or []),
            )
        )
    if trace is not None:
        lines.extend(
            _finding_section(
                "Runtime trace findings (acr trace)",
                list(trace.get("findings") or []),
            )
        )
        lines.extend(_timeline_section(trace))

    lines.append("---")
    lines.append("")
    lines.append(
        "Rule documentation lives under `docs/rules/`. Each finding links to its "
        "rule page."
    )
    lines.append("")
    return "\n".join(lines)


def write_report(
    scan_path: str | Path | None,
    trace_path: str | Path | None,
    output: str | Path | None = None,
) -> str:
    """Load results, build Markdown, optionally write it, and return the text."""

    scan = load_result(scan_path, "scan") if scan_path else None
    trace = load_result(trace_path, "trace") if trace_path else None
    markdown = build_report(scan, trace)
    if output is not None:
        target = Path(output)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown, encoding="utf-8")
        except OSError as exc:
            raise ReportError(f"cannot write {output}: {exc}") from exc
    return markdown
