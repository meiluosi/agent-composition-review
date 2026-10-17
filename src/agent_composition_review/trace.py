"""Runtime trace analysis for :command:`acr trace`.

Accepted input formats
======================

1. **JSON array** of event objects::

       [{"id": "e1", "kind": "node_start", "node": "planner"}, ...]

2. **JSONL** - one event object per line.

3. **Unified object** with an ``events`` array::

       {"input": {"question": "..."},
        "events": [...],
        "final_state": {"answer": "..."}}

   ``input``/``initial_state`` and ``final_state``/``output`` are optional; they
   are used by ``ACR105`` (final state lost keys).  A ``state`` event may also
   set ``"final": true`` (or ``"initial": true``) to mark a snapshot.

Canonical event kinds
=====================

``node_start``, ``node_end``, ``tool_start``, ``tool_end``, ``error``,
``state``.  Unknown kinds are kept in the timeline but never interpreted.

Event fields (all optional except an id/kind):

``id``, ``seq``, ``kind``, ``node``, ``tool``, ``step``, ``args``, ``result``,
``writes`` (state updates as a mapping), ``message``, ``ts``.  A few aliases are
understood (``event``/``type`` for ``kind``, ``input``/``output``,
``update``/``updates`` for state writes, ``superstep`` for ``step``) so
LangGraph debug-style payloads can be fed in without rewriting.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import Finding, TraceEvent, TraceResult
from .rules import build_finding
from .scan import display_path


class TraceParseError(RuntimeError):
    """Raised when a trace file cannot be read or decoded."""


_KIND_ALIASES = {
    "node_start": "node_start",
    "node_started": "node_start",
    "node_begin": "node_start",
    "node_enter": "node_start",
    "on_node_start": "node_start",
    "node_end": "node_end",
    "node_ended": "node_end",
    "node_finished": "node_end",
    "node_exit": "node_end",
    "on_node_end": "node_end",
    "tool_start": "tool_start",
    "tool_started": "tool_start",
    "tool_begin": "tool_start",
    "tool_call": "tool_start",
    "on_tool_start": "tool_start",
    "tool_end": "tool_end",
    "tool_ended": "tool_end",
    "tool_finished": "tool_end",
    "tool_result": "tool_end",
    "on_tool_end": "tool_end",
    "error": "error",
    "exception": "error",
    "failure": "error",
    "failed": "error",
    "on_error": "error",
    "node_error": "error",
    "tool_error": "error",
    "state": "state",
    "state_update": "state",
    "state_snapshot": "state",
    "checkpoint": "state",
    "update_state": "state",
    "resume": "resume",
    "resumed": "resume",
    "replay": "replay",
    "replayed": "replay",
}

_FRAMEWORK_CHAIN_NAMES = {"LangGraph", "RunnableSequence", "RunnableLambda", "_start", "_end"}


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _short(value: Any, limit: int = 90) -> str:
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        return repr(value)


def _normalise_kind(raw: dict[str, Any]) -> str:
    raw_kind = (
        raw.get("kind")
        or raw.get("event")
        or raw.get("type")
        or raw.get("event_type")
        or ""
    )
    text = str(raw_kind).strip().lower()
    if text in _KIND_ALIASES:
        return _KIND_ALIASES[text]

    payload = raw.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    event_type = text or str(payload.get("type") or "").strip().lower()

    # LangGraph stream_mode="debug" events carry their type on the outer object
    # and the node details inside ``payload``.
    if event_type == "task":
        return "node_start"
    if event_type == "task_result":
        return "error" if payload.get("error") else "node_end"
    if event_type in {"checkpoint", "state_snapshot"}:
        return "state"
    if event_type in {"error", "exception"}:
        return "error"
    payload_type = payload.get("type")
    if payload_type == "task":
        return "node_start"
    if payload_type == "task_result":
        return "error" if payload.get("error") else "node_end"
    if payload_type == "checkpoint":
        return "state"
    if payload_type == "error":
        return "error"

    if text in {"on_chain_start", "on_chain_end"}:
        metadata = raw.get("metadata")
        name = raw.get("name")
        if isinstance(metadata, dict) and metadata.get("langgraph_node"):
            return "node_start" if text == "on_chain_start" else "node_end"
        if name and name not in _FRAMEWORK_CHAIN_NAMES:
            return "node_start" if text == "on_chain_start" else "node_end"
    return "unknown"


def _as_mapping(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def normalise_events(raw_events: list[Any]) -> list[TraceEvent]:
    """Convert raw JSON event objects into :class:`TraceEvent` instances."""

    events: list[TraceEvent] = []
    for index, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            raise TraceParseError(f"event #{index + 1} is not a JSON object")
        metadata = _as_mapping(raw.get("metadata")) or {}
        data = _as_mapping(raw.get("data")) or {}
        payload = _as_mapping(raw.get("payload")) or {}

        kind = _normalise_kind(raw)
        event_id = raw.get("id") or raw.get("event_id") or f"e{index + 1}"
        event_id = str(event_id)

        seq = raw.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int):
            seq = index + 1

        node = raw.get("node") or raw.get("node_name") or metadata.get("langgraph_node")
        if node is None and kind.startswith("node"):
            node = raw.get("name") or payload.get("name")
        if node is None and kind == "state":
            node = metadata.get("langgraph_node") or payload.get("name")

        tool = raw.get("tool") or raw.get("tool_name")
        if tool is None and kind.startswith("tool"):
            tool = raw.get("name") or data.get("name")

        step = _first_not_none(
            raw.get("step"), raw.get("step_id"), raw.get("superstep")
        )
        if step is None:
            step = metadata.get("langgraph_step")

        args = _first_not_none(raw.get("args"), raw.get("input"), data.get("input"))
        if args is None and kind == "node_start":
            args = payload.get("input")
        result = _first_not_none(
            raw.get("result"), raw.get("output"), data.get("output"), payload.get("result")
        )
        if result is None and kind == "node_end":
            result = payload.get("result")
        writes = _first_not_none(
            raw.get("writes"), raw.get("update"), raw.get("updates")
        )
        if writes is None and kind == "state":
            writes = _first_not_none(raw.get("state"), payload.get("values"))
        if writes is None and kind == "node_end":
            writes = result
        writes = _as_mapping(writes)

        message = _first_not_none(raw.get("message"), raw.get("error"))
        if message is None and kind == "error":
            message = _first_not_none(data.get("error"), payload.get("error"))
        if message is not None and not isinstance(message, str):
            message = _short(message, limit=300)

        final = bool(raw.get("final") is True or payload.get("final") is True)
        initial = bool(
            raw.get("initial") is True
            or raw.get("scope") in {"initial", "input"}
            or kind == "state"
            and raw.get("role") in {"initial", "input"}
        )
        ts = _first_not_none(raw.get("ts"), raw.get("timestamp"), raw.get("time"))
        interrupts = _first_not_none(
            payload.get("interrupts"), raw.get("interrupts")
        )

        events.append(
            TraceEvent(
                id=event_id,
                seq=seq,
                kind=kind,
                node=str(node) if node is not None else None,
                tool=str(tool) if tool is not None else None,
                step=step,
                writes=writes,
                args=args,
                result=result,
                ts=ts,
                message=message,
                final=final,
                initial=initial,
                interrupts=interrupts,
            )
        )
    return events


def _insert_resume_markers(
    events: list[TraceEvent], resume_at: list[int] | None
) -> list[TraceEvent]:
    """Insert synthetic resume markers after the given event seq values.

    LangGraph's debug stream does not emit a resume/replay event. The user can
    point at the last event before the resume boundary with ``--resume-at``;
    this keeps ACR106 usable without inventing markers in the source log.
    """

    if not resume_at:
        return events
    markers: set[int] = set()
    for value in resume_at:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TraceParseError("--resume-at expects non-negative event seq values")
        markers.add(value)
    found: set[int] = set()
    result: list[TraceEvent] = []
    for event in events:
        result.append(event)
        if event.seq in markers:
            found.add(event.seq)
            if event.kind not in {"resume", "replay"}:
                result.append(
                    TraceEvent(
                        id=f"resume@{event.seq}",
                        seq=event.seq,
                        kind="resume",
                    )
                )
    missing = sorted(markers - found)
    if missing:
        raise TraceParseError(
            "resume marker seq not found in trace: "
            + ", ".join(str(value) for value in missing)
        )
    return result


def _load_events(text: str) -> tuple[list[Any], dict[str, Any] | None, str]:
    if not text.strip():
        raise TraceParseError("trace file is empty")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None

    if payload is not None:
        if isinstance(payload, list):
            return payload, None, "json_array"
        if isinstance(payload, dict):
            events = payload.get("events")
            if isinstance(events, list):
                return events, payload, "unified"
            if any(key in payload for key in ("kind", "event", "type", "event_type")):
                return [payload], None, "json_array"
            raise TraceParseError(
                "JSON object has no 'events' array and does not look like an event"
            )
        raise TraceParseError("trace JSON must be an array or an object")

    raw_events: list[Any] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            item = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise TraceParseError(
                f"line {line_number}: invalid JSON ({exc.msg})"
            ) from exc
        if isinstance(item, list):
            raw_events.extend(item)
        else:
            raw_events.append(item)
    if not raw_events:
        raise TraceParseError("trace file contains no JSON events")
    return raw_events, None, "jsonl"


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


@dataclass
class _Execution:
    node: str | None
    start: TraceEvent
    end: TraceEvent | None = None
    writes: dict[str, Any] | None = None

    @property
    def end_seq(self) -> int:
        return self.end.seq if self.end is not None else self.start.seq


@dataclass
class _Snapshot:
    keys: list[str]
    source: str


class TraceAnalyzer:
    """Run the ACR101-ACR105 runtime rules over normalised events."""

    def __init__(
        self,
        events: list[TraceEvent],
        top_level: dict[str, Any] | None,
    ) -> None:
        self.events = events
        self.top_level = top_level
        self.findings: list[Finding] = []
        self._seen: set[tuple[str, int, str]] = set()
        self.initial_snapshot = self._find_snapshot(initial=True)
        self.final_snapshot = self._find_snapshot(initial=False)

    # -- helpers ----------------------------------------------------------

    def _emit(
        self,
        rule_id: str,
        *,
        line: int,
        message: str,
        evidence: list[str],
    ) -> None:
        issue = build_finding(
            rule_id,
            file="",
            line=line,
            message=message,
            evidence=evidence,
        )
        key = (issue.rule, issue.line, issue.message)
        if key in self._seen:
            return
        self._seen.add(key)
        self.findings.append(issue)

    def _find_snapshot(self, *, initial: bool) -> _Snapshot | None:
        top = self.top_level or {}
        top_keys = (
            ("initial_state", "input", "inputs")
            if initial
            else ("final_state", "output", "outputs", "final")
        )
        for key in top_keys:
            value = top.get(key)
            if isinstance(value, dict):
                return _Snapshot(keys=sorted(value.keys()), source=f"top-level '{key}'")
        marker_attr = "initial" if initial else "final"
        candidates = [
            event
            for event in self.events
            if event.kind == "state"
            and event.writes
            and getattr(event, marker_attr)
        ]
        if candidates:
            event = candidates[0] if initial else candidates[-1]
            return _Snapshot(
                keys=sorted(event.writes.keys()),
                source=f"state event {event.id}",
            )
        return None

    @staticmethod
    def _describe_writes(event: TraceEvent) -> str:
        keys = sorted(event.writes.keys()) if event.writes else []
        return ", ".join(f"'{key}'" for key in keys) or "no keys"

    def run(self) -> list[Finding]:
        self._rule_acr101()
        self._rule_acr102()
        self._rule_acr103()
        self._rule_acr104()
        self._rule_acr105()
        self._rule_acr106()
        self.findings.sort(
            key=lambda finding: (
                -finding.severity_rank,
                finding.line,
                finding.rule,
            )
        )
        return self.findings

    # -- ACR101 -----------------------------------------------------------

    def _rule_acr101(self) -> None:
        calls: list[dict[str, Any]] = []
        open_calls: list[int] = []
        for event in self.events:
            if event.kind == "tool_start":
                calls.append(
                    {
                        "tool": event.tool,
                        "args": event.args,
                        "start": event,
                        "end": None,
                        "result": None,
                    }
                )
                open_calls.append(len(calls) - 1)
            elif event.kind == "tool_end":
                matched: int | None = None
                for index in reversed(open_calls):
                    call_tool = calls[index]["tool"]
                    if event.tool is None or call_tool is None or call_tool == event.tool:
                        matched = index
                        break
                if matched is not None:
                    open_calls.remove(matched)
                    calls[matched]["end"] = event
                    calls[matched]["result"] = event.result

        for previous, current in zip(calls, calls[1:]):
            tool = previous["tool"]
            if not tool or current["tool"] != tool:
                continue
            if previous["args"] is None or current["args"] is None:
                continue
            if _canonical(previous["args"]) != _canonical(current["args"]):
                continue
            previous_result = previous["result"]
            current_result = current["result"]
            if previous_result is None and current_result is None:
                same_result = True
            elif previous_result is not None and current_result is not None:
                same_result = _canonical(previous_result) == _canonical(
                    current_result
                )
            else:
                same_result = False
            if not same_result:
                continue

            start = previous["start"]
            evidence = [
                f"{previous['start'].id}: tool '{tool}' start (seq "
                f"{previous['start'].seq}, args {_short(previous['args'])})",
                f"{current['start'].id}: tool '{tool}' start again (seq "
                f"{current['start'].seq}, identical args)",
            ]
            if previous["end"] is not None and current["end"] is not None:
                evidence.append(
                    f"{previous['end'].id}/{current['end'].id}: results are identical "
                    f"({_short(previous_result)})"
                )
            else:
                evidence.append("no differing tool result was recorded between calls")
            message = (
                f"Tool '{tool}' was called twice in a row with identical arguments "
                f"({_short(previous['args'])}) and the same result. The second call "
                "did not add information."
            )
            self._emit("ACR101", line=start.seq, message=message, evidence=evidence)

    # -- ACR102 -----------------------------------------------------------

    def _rule_acr102(self) -> None:
        writes_by_node: dict[str, list[TraceEvent]] = {}
        for event in self.events:
            if (
                event.node
                and event.writes
                and event.kind in {"node_end", "state", "unknown"}
            ):
                writes_by_node.setdefault(event.node, []).append(event)

        for node, events in sorted(writes_by_node.items()):
            if len(events) < 2:
                continue
            key_counts: Counter[str] = Counter()
            for event in events:
                key_counts.update(event.writes.keys())
            repeated_keys = sorted(
                key for key, count in key_counts.items() if count >= 2
            )
            if not repeated_keys:
                continue
            focus = repeated_keys[0]
            related = [event for event in events if focus in event.writes][:2]
            keys_label = ", ".join(f"'{key}'" for key in repeated_keys)
            evidence = [
                f"{event.id}: node '{node}' wrote {self._describe_writes(event)} "
                f"(seq {event.seq})"
                for event in related
            ]
            if len(events) > len(related):
                evidence.append(
                    f"node '{node}' produced {len(events)} write events in total"
                )
            message = (
                f"Node '{node}' executed {len(events)} times and rewrote state "
                f"key(s) {keys_label}. Repeated writes to the same key usually mean "
                "a retry loop or a missing conditional exit."
            )
            self._emit(
                "ACR102",
                line=related[0].seq,
                message=message,
                evidence=evidence,
            )

    # -- ACR103 -----------------------------------------------------------

    def _rule_acr103(self) -> None:
        if any(event.step is not None for event in self.events):
            by_step: dict[Any, dict[str, dict[str, TraceEvent]]] = {}
            for event in self.events:
                if event.step is None or not event.node or not event.writes:
                    continue
                key_map = by_step.setdefault(event.step, {})
                for key in event.writes:
                    key_map.setdefault(key, {}).setdefault(event.node, event)
            for step in sorted(by_step, key=str):
                for key in sorted(by_step[step]):
                    nodes = by_step[step][key]
                    if len(nodes) < 2:
                        continue
                    related = sorted(nodes.values(), key=lambda event: event.seq)
                    node_names = ", ".join(f"'{name}'" for name in sorted(nodes))
                    evidence = [
                        f"{event.id}: node '{event.node}' wrote '{key}' in step "
                        f"{step} (seq {event.seq})"
                        for event in related
                    ]
                    message = (
                        f"Step {step}: nodes {node_names} both wrote state key "
                        f"'{key}'. Concurrent writes to a non-reduced key raise "
                        "InvalidUpdateError; with a reducer the ordering is implicit."
                    )
                    self._emit(
                        "ACR103",
                        line=related[0].seq,
                        message=message,
                        evidence=evidence,
                    )
            return

        executions = self._executions()
        for index, left in enumerate(executions):
            for right in executions[index + 1 :]:
                if left.node == right.node:
                    continue
                if left.node is None or right.node is None:
                    continue
                if not self._overlap(left, right):
                    continue
                shared = sorted(set(left.writes or {}) & set(right.writes or {}))
                for key in shared:
                    ordered = sorted((left, right), key=lambda item: item.start.seq)
                    evidence = [
                        f"{execution.start.id}: node '{execution.node}' started at "
                        f"seq {execution.start.seq}"
                        + (
                            f", ended at {execution.end.id} (seq {execution.end.seq})"
                            if execution.end is not None
                            else " and never ended before the trace ended"
                        )
                        for execution in ordered
                    ]
                    evidence.append(f"both executions wrote state key '{key}'")
                    message = (
                        f"Nodes '{ordered[0].node}' and '{ordered[1].node}' overlapped "
                        f"in execution and both wrote state key '{key}'. Without a "
                        "reducer this raises InvalidUpdateError."
                    )
                    self._emit(
                        "ACR103",
                        line=ordered[0].start.seq,
                        message=message,
                        evidence=evidence,
                    )

    def _executions(self) -> list[_Execution]:
        executions: list[_Execution] = []
        open_executions: list[_Execution] = []
        for event in self.events:
            if event.kind == "node_start":
                execution = _Execution(node=event.node, start=event)
                executions.append(execution)
                open_executions.append(execution)
            elif event.kind == "node_end":
                matched: _Execution | None = None
                for execution in reversed(open_executions):
                    if execution.node == event.node:
                        matched = execution
                        break
                if matched is None and open_executions:
                    matched = open_executions[-1]
                if matched is not None:
                    open_executions.remove(matched)
                    matched.end = event
                    matched.writes = event.writes

        for execution in executions:
            if execution.end is None:
                # An execution that never closed overlaps until the trace ends.
                execution.writes = execution.writes or self._writes_for(execution)
                continue
            if execution.writes is not None:
                continue
            for event in self.events:
                if (
                    event.kind == "state"
                    and event.node == execution.node
                    and execution.start.seq <= event.seq <= execution.end_seq
                    and event.writes
                ):
                    execution.writes = event.writes
                    break
        return executions

    def _writes_for(self, execution: _Execution) -> dict[str, Any] | None:
        for event in self.events:
            if (
                event.kind == "state"
                and event.node == execution.node
                and event.seq >= execution.start.seq
                and event.writes
            ):
                return event.writes
        return None

    def _overlap(self, left: _Execution, right: _Execution) -> bool:
        left_end = left.end_seq if left.end is not None else self.events[-1].seq
        right_end = right.end_seq if right.end is not None else self.events[-1].seq
        return left.start.seq < right_end and right.start.seq < left_end

    # -- ACR104 -----------------------------------------------------------

    def _rule_acr104(self) -> None:
        write_events = [event for event in self.events if event.writes]
        for error in self.events:
            if error.kind != "error":
                continue
            prior = [event for event in write_events if event.seq < error.seq]
            if not prior:
                continue
            recent = prior[-5:]
            evidence = [
                f"{event.id}: {event.kind} node='{event.node or '?'}' wrote "
                f"{self._describe_writes(event)} (seq {event.seq})"
                for event in recent
            ]
            evidence.append(
                f"{error.id}: error at seq {error.seq}"
                + (f" ({_short(error.message)})" if error.message else "")
            )
            message = (
                f"Error raised after {len(prior)} state write(s) had already been "
                "applied. The failed run can leave partially updated state behind."
            )
            self._emit("ACR104", line=error.seq, message=message, evidence=evidence)

    # -- ACR105 -----------------------------------------------------------

    def _rule_acr105(self) -> None:
        if self.initial_snapshot is None or self.final_snapshot is None:
            return
        lost = sorted(set(self.initial_snapshot.keys) - set(self.final_snapshot.keys))
        if not lost:
            return
        lost_label = ", ".join(f"'{key}'" for key in lost)
        evidence = [
            f"input state from {self.initial_snapshot.source} has keys: "
            f"{', '.join(self.initial_snapshot.keys)}",
            f"final state from {self.final_snapshot.source} has keys: "
            f"{', '.join(self.final_snapshot.keys)}",
            f"keys lost between input and output: {lost_label}",
        ]
        message = (
            f"Final state is missing {lost_label}, which were present in the input "
            "state. A node or subgraph dropped state the caller expected to keep."
        )
        self._emit("ACR105", line=1, message=message, evidence=evidence)

    # -- ACR106 -----------------------------------------------------------

    def _rule_acr106(self) -> None:
        """A completed node restarted after a resume/replay marker."""

        completed: dict[str, TraceEvent] = {}
        marker: TraceEvent | None = None
        reported: set[tuple[str, str]] = set()
        for event in self.events:
            if event.kind in {"resume", "replay"}:
                marker = event
                continue
            if event.kind == "node_end" and event.node:
                # An interrupted task is not a completed task: the node may
                # legitimately start again after resume.
                if not event.interrupts:
                    completed[event.node] = event
                continue
            if event.kind != "node_start" or not event.node or marker is None:
                continue
            previous = completed.get(event.node)
            if previous is None or previous.seq > marker.seq:
                continue
            key = (marker.id, event.node)
            if key in reported:
                continue
            reported.add(key)
            evidence = [
                f"{previous.id}: node '{event.node}' completed at seq {previous.seq}",
                f"{marker.id}: {marker.kind} marker at seq {marker.seq}",
                f"{event.id}: node '{event.node}' started again at seq {event.seq}",
            ]
            message = (
                f"Node '{event.node}' had already completed before {marker.kind} "
                f"marker {marker.id}, but started again after it. Resuming/replaying "
                "a completed task can repeat its side effects or overwrite newer state."
            )
            self._emit("ACR106", line=event.seq, message=message, evidence=evidence)


# ---------------------------------------------------------------------------
# Public trace API
# ---------------------------------------------------------------------------


def trace_path(
    path: str | Path, resume_at: list[int] | None = None
) -> TraceResult:
    """Analyse a trace file and return a :class:`TraceResult`.

    ``resume_at`` inserts synthetic resume markers after the given event
    sequence numbers (used when the source log has no explicit resume event).
    A unified JSON object can also carry ``resume_seq`` as an integer or list.
    """

    target = Path(path)
    if not target.exists():
        raise TraceParseError(f"trace file not found: {path}")
    if not target.is_file():
        raise TraceParseError(f"not a file: {path}")
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TraceParseError(f"cannot read {path}: {exc}") from exc

    raw_events, top_level, input_format = _load_events(text)
    events = normalise_events(raw_events)
    if resume_at is None and top_level:
        top_resume = top_level.get("resume_seq", top_level.get("resume_at"))
        if isinstance(top_resume, int) and not isinstance(top_resume, bool):
            resume_at = [top_resume]
        elif isinstance(top_resume, list) and all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in top_resume
        ):
            resume_at = list(top_resume)
    events = _insert_resume_markers(events, resume_at)
    analyzer = TraceAnalyzer(events, top_level)
    findings = analyzer.run()
    display = display_path(target)
    for finding in findings:
        finding.file = display
    if findings:
        findings.sort(
            key=lambda finding: (
                -finding.severity_rank,
                finding.line,
                finding.rule,
            )
        )
    return TraceResult(
        root=display,
        findings=findings,
        events=events,
        input_format=input_format,
        initial_state_keys=(
            analyzer.initial_snapshot.keys if analyzer.initial_snapshot else None
        ),
        final_state_keys=(
            analyzer.final_snapshot.keys if analyzer.final_snapshot else None
        ),
    )


def render_trace_text(result: TraceResult) -> str:
    """Render the default (non-JSON) ``acr trace`` output."""

    counts = {severity: 0 for severity in ("high", "medium", "low")}
    for finding in result.findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    lines = [
        f"acr trace - {result.root}",
        f"{len(result.events)} event(s) ({result.input_format}) - "
        f"{len(result.findings)} finding(s): high {counts['high']}, "
        f"medium {counts['medium']}, low {counts['low']}",
        "",
        "timeline:",
    ]
    for event in result.events:
        parts = [f"  #{event.seq:03d}", event.id.ljust(6), event.kind.ljust(11)]
        if event.node is not None:
            parts.append(f"node={event.node}")
        if event.tool is not None:
            parts.append(f"tool={event.tool}")
        if event.step is not None:
            parts.append(f"step={event.step}")
        if event.writes:
            parts.append(f"writes={_short(event.writes, limit=70)}")
        if event.args is not None:
            parts.append(f"args={_short(event.args, limit=60)}")
        if event.result is not None:
            parts.append(f"result={_short(event.result, limit=60)}")
        if event.message:
            parts.append(f"message={_short(event.message, limit=80)}")
        if event.final:
            parts.append("final=true")
        lines.append("  ".join(parts))

    if result.initial_state_keys is not None or result.final_state_keys is not None:
        lines.append("")
        lines.append(
            "state keys: "
            f"input={result.initial_state_keys or []} "
            f"final={result.final_state_keys or []}"
        )

    lines.append("")
    if result.findings:
        for finding in result.findings:
            status_tag = "" if finding.status == "grounded" else " [experimental]"
            lines.append(
                f"  {finding.severity.upper():<6} {finding.rule}{status_tag}  "
                f"{finding.title}"
            )
            lines.append(f"    event seq {finding.line}")
            lines.append(f"    {finding.message}")
            if finding.evidence:
                lines.append("    evidence:")
                for item in finding.evidence:
                    lines.append(f"      - {item}")
            lines.append(f"    fix: {finding.fix}")
            lines.append(f"    docs: {finding.docs}")
            lines.append("")
    else:
        lines.append("  No runtime composition issues found.")
    return "\n".join(lines).rstrip()
