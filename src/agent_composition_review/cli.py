"""``acr`` command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import __version__
from .demo import DemoDependencyError, run_demo
from .model import FAIL_ON_CHOICES, Finding
from .report import ReportError, write_report
from .rules import format_rules_text
from .scan import ScanError, render_scan_text, scan_path
from .trace import TraceParseError, render_trace_text, trace_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acr",
        description=(
            "Diagnose LangGraph/LangChain composition failures: static graph "
            "analysis (scan), runtime trace analysis (trace), Markdown reports, "
            "and a built-in runnable demo."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"acr {__version__}",
        help="print the acr version and exit",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    scan = subparsers.add_parser(
        "scan",
        help="statically analyse Python graph construction with AST rules",
        description=(
            "Scan a Python file or directory for LangGraph composition hazards. "
            "No imports, execution, network access, or model calls are performed."
        ),
    )
    scan.add_argument("path", metavar="PATH", help="Python file or directory to scan")
    scan.add_argument(
        "--json",
        action="store_true",
        help="emit the stable JSON schema instead of text",
    )
    scan.add_argument(
        "--fail-on",
        choices=FAIL_ON_CHOICES,
        default="high",
        help=(
            "exit 1 when a finding at this level or above is present "
            "(default: high; 'review' means medium+high; 'never' always exits 0)"
        ),
    )

    trace = subparsers.add_parser(
        "trace",
        help="analyse a runtime trace (JSON array, JSONL, or unified object)",
        description=(
            "Analyse runtime events for repeated tool calls, duplicate node "
            "writes, parallel state writes, partial writes before an error, and "
            "lost final state keys."
        ),
    )
    trace.add_argument("path", metavar="TRACE", help="trace file to analyse")
    trace.add_argument(
        "--json",
        action="store_true",
        help="emit the stable JSON schema instead of text",
    )

    report = subparsers.add_parser(
        "report",
        help="merge scan/trace JSON results into a one-page Markdown report",
        description="Read `acr scan --json` and `acr trace --json` files and write "
        "a one-page Markdown report.",
    )
    report.add_argument("--scan", metavar="FILE", required=True, help="scan JSON file")
    report.add_argument("--trace", metavar="FILE", required=True, help="trace JSON file")
    report.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        default=None,
        help="write the report to FILE (default: print to stdout)",
    )

    subparsers.add_parser(
        "demo",
        help="run a built-in broken/fixed LangGraph state-leak demo",
        description=(
            "Run a LangGraph #6446-style subgraph state leak, show the runtime "
            "InvalidUpdateError, scan the source with ACR001, then run the fixed "
            "version. Requires the optional langgraph dependency."
        ),
    )

    subparsers.add_parser(
        "rules",
        help="print the rule table (ids, severities, titles, docs)",
        description="Print the diagnostic rule table used by scan and trace.",
    )
    return parser


def _scan_exit_code(findings: list[Finding], fail_on: str) -> int:
    if fail_on == "never":
        return 0
    severities = {finding.severity for finding in findings}
    if fail_on == "high":
        return 1 if "high" in severities else 0
    return 1 if severities & {"high", "medium"} else 0


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help(sys.stderr)
        return 2

    if args.command == "scan":
        try:
            result = scan_path(args.path)
        except ScanError as exc:
            print(f"acr scan: {exc}", file=sys.stderr)
            return 2
        if args.json:
            _print_json(result.to_dict())
        else:
            print(render_scan_text(result))
        return _scan_exit_code(result.findings, args.fail_on)

    if args.command == "trace":
        try:
            result = trace_path(args.path)
        except TraceParseError as exc:
            print(f"acr trace: {exc}", file=sys.stderr)
            return 2
        if args.json:
            _print_json(result.to_dict())
        else:
            print(render_trace_text(result))
        return 0

    if args.command == "report":
        try:
            markdown = write_report(args.scan, args.trace, args.output)
        except ReportError as exc:
            print(f"acr report: {exc}", file=sys.stderr)
            return 2
        if args.output is None:
            print(markdown)
        else:
            print(f"acr report: wrote {args.output}")
        return 0

    if args.command == "demo":
        try:
            return run_demo()
        except DemoDependencyError as exc:
            print(f"acr demo: {exc}", file=sys.stderr)
            return 2

    if args.command == "rules":
        print(format_rules_text())
        return 0

    parser.error(f"unknown command: {args.command}")  # pragma: no cover
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
