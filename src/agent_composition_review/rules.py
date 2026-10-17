"""Rule registry for :command:`acr scan` and :command:`acr trace`.

Every diagnostic in the JSON output points at ``docs`` so a reader can follow a
repository-relative link to ``docs/rules/<RULE>.md``.
"""

from __future__ import annotations

from .model import Finding, Rule

SCAN_RULES: tuple[Rule, ...] = (
    Rule(
        id="ACR001",
        severity="high",
        scope="scan",
        title="Compiled subgraph shares state keys with the parent graph",
        summary=(
            "A compiled subgraph is registered with ``add_node`` on a parent graph, "
            "the subgraph never declared an ``output_schema``, and the parent and "
            "child state schemas share at least one key."
        ),
        fix=(
            "Declare ``output_schema=<SubgraphOutput>`` on the child ``StateGraph`` "
            "so only intended keys flow back to the parent, or split the shared key "
            "into distinct keys."
        ),
    ),
    Rule(
        id="ACR002",
        severity="high",
        scope="scan",
        title="Parallel branches write the same state key without a reducer",
        summary=(
            "A source node (including ``START``) has unconditional edges to two or "
            "more nodes, those nodes write the same state key, and the key's "
            "``TypedDict`` annotation has no reducer."
        ),
        fix=(
            "Give the key a reducer, e.g. ``Annotated[list, operator.add]`` (or "
            "``add_messages`` for messages), or make the branches write disjoint "
            "keys / run sequentially."
        ),
    ),
    Rule(
        id="ACR003",
        severity="medium",
        scope="scan",
        title="Subgraph node has no output_schema",
        summary=(
            "A compiled subgraph is registered as a node without an "
            "``output_schema``. The complete child state, not just the intended "
            "keys, is passed back to the parent."
        ),
        fix=(
            "Pass ``output_schema=...`` to the child ``StateGraph`` constructor "
            "(or ``compile(output_schema=...)``) to restrict what the subgraph "
            "returns."
        ),
    ),
    Rule(
        id="ACR004",
        severity="medium",
        scope="scan",
        title="Cache on a side-effecting node or graph",
        summary=(
            "A node function is decorated with ``@lru_cache``/``@cache`` (or the "
            "graph is compiled with ``cache=...``) while the node writes state or "
            "calls tools/external code. Cached side effects are replayed or lost."
        ),
        fix=(
            "Remove the cache from this node, or move the side effect outside the "
            "cached function; cache only pure computations."
        ),
    ),
    Rule(
        id="ACR005",
        severity="medium",
        scope="scan",
        title="Retry on a side-effecting node",
        summary=(
            "A node function uses a retry decorator (its name contains ``retry``) "
            "and also writes state or calls tools/external code. A retried side "
            "effect can be applied more than once."
        ),
        fix=(
            "Make the node idempotent before enabling retries (dedupe writes, key "
            "tool calls by run id), or disable retries for this side-effecting step."
        ),
    ),
    Rule(
        id="ACR006",
        severity="low",
        scope="scan",
        title="Node reads or writes graph state through the checkpointer",
        summary=(
            "A node function calls ``get_state``/``update_state`` (or an async "
            "variant) directly, bypassing the graph's own state channel. This hides "
            "reads/writes from composition analysis and from reducers."
        ),
        fix=(
            "Pass the relevant state through the graph instead of calling "
            "``get_state``/``update_state`` inside a node; keep checkpoint access "
            "outside node execution."
        ),
    ),
    Rule(
        id="ACR007",
        severity="medium",
        scope="scan",
        title="Node returns keys not declared in the state schema",
        summary=(
            "A node function (or a class-based ``__call__`` node) returns a dict "
            "literal containing a key that is not declared in the graph's "
            "``TypedDict`` state schema. LangGraph silently filters undeclared keys, "
            "so the write disappears and downstream nodes never see it."
        ),
        fix=(
            "Declare the key in the graph state ``TypedDict`` (with a reducer if "
            "multiple nodes write it), remove/rename the returned key, or — for a "
            "class node with a private input schema — pass an explicit node input "
            "schema (``add_node(..., input=...)``) so the return shape maps onto the "
            "graph state. Do not rely on undeclared keys surviving the node boundary."
        ),
    ),
    Rule(
        id="ACR008",
        severity="medium",
        scope="scan",
        title="Cache policy on a callable without a stable function identity",
        summary=(
            "A node enables ``cache_policy`` but its callable is a callable object, "
            "lambda, partial, or an unresolved parameter. The cache key can lose the "
            "module/qualname identity and collapse to a dynamic namespace; graphs "
            "sharing a cache can return each other's results."
        ),
        fix=(
            "Use a module-level named function for cached nodes, or include an "
            "explicit stable key/namespace in the cache policy. Avoid sharing one "
            "cache object across graphs with anonymous callables."
        ),
    ),
    Rule(
        id="ACR009",
        severity="medium",
        scope="scan",
        title="retry_policy on a node invoking multiple subgraphs",
        summary=(
            "A node registered with ``retry_policy`` invokes two or more subgraphs. "
            "After a failed attempt or resume, a later subgraph can reuse the first "
            "subgraph's checkpoint namespace and return a stale result."
        ),
        fix=(
            "Avoid multiple subgraph invokes inside one retried node; split them into "
            "separate nodes or make each subgraph call carry its own explicit "
            "checkpoint/namespace identity."
        ),
    ),
    Rule(
        id="ACR010",
        severity="medium",
        scope="scan",
        title="Conditional self-loop rewrites a non-reducer state key",
        summary=(
            "A node has a conditional edge back to itself and returns a state key "
            "whose schema has no reducer. Retry/re-entry can overlap with a parallel "
            "branch in the same superstep, producing multiple writes or lost updates."
        ),
        fix=(
            "Give the key a reducer, or guard the self-loop so retries cannot overlap "
            "with another writer; if the key is per-attempt, use a reducer that keeps "
            "attempts distinct instead of a single LastValue channel."
        ),
    ),
)

TRACE_RULES: tuple[Rule, ...] = (
    Rule(
        id="ACR101",
        severity="medium",
        scope="trace",
        title="Duplicate tool call with identical arguments and result",
        summary=(
            "The same tool receives identical arguments in two consecutive calls "
            "and returns an identical result. Time and budget are wasted on a "
            "repeated call."
        ),
        fix=(
            "Cache or reuse the first tool result, or change the arguments; avoid "
            "identical repeated calls."
        ),
    ),
    Rule(
        id="ACR102",
        severity="medium",
        scope="trace",
        title="Node re-executed and rewrote the same state key",
        summary=(
            "The same node ran more than once and wrote the same state key each "
            "time. This usually signals a retry loop or a missing conditional exit."
        ),
        fix=(
            "Check loop conditions and edge routing; a node that rewrites the same "
            "key twice usually indicates a retry loop or a missing conditional exit."
        ),
    ),
    Rule(
        id="ACR103",
        severity="high",
        scope="trace",
        title="Parallel nodes in one step wrote the same state key",
        summary=(
            "Two nodes executed in the same step (or in overlapping execution "
            "windows) wrote the same state key. Without a reducer LangGraph raises "
            "``InvalidUpdateError``; with a reducer the write order can still be "
            "surprising."
        ),
        fix=(
            "Give the key a reducer (e.g. ``Annotated[list, operator.add]``) or "
            "route the parallel nodes to write different keys."
        ),
    ),
    Rule(
        id="ACR104",
        severity="high",
        scope="trace",
        title="Error raised after partial state writes",
        summary=(
            "An error event occurred after one or more state writes had already "
            "been applied. The run can leave partially updated state behind."
        ),
        fix=(
            "Wrap the step in error handling that records or rolls back partial "
            "writes; re-run from the last successful checkpoint."
        ),
    ),
    Rule(
        id="ACR105",
        severity="medium",
        scope="trace",
        title="Final state is missing keys present in the input",
        summary=(
            "Keys present in the initial/input state are absent from the final "
            "state. A node or subgraph dropped state that the caller expected to "
            "survive."
        ),
        fix=(
            "Check the final node's output for dropped keys; return a full state "
            "update or preserve keys deliberately."
        ),
    ),
    Rule(
        id="ACR106",
        severity="medium",
        scope="trace",
        title="Completed node restarted after a resume/replay marker",
        summary=(
            "A trace marks a resume or replay, then starts a node again that had "
            "already emitted a completion event before the marker. Replaying a "
            "completed task can repeat its side effects or overwrite newer state."
        ),
        fix=(
            "Check the resume/replay checkpoint boundary: do not re-run nodes whose "
            "writes were already durable. Prefer checkpoint-level resume over replay "
            "of the whole superstep."
        ),
    ),
)

RULES: dict[str, Rule] = {rule.id: rule for rule in (*SCAN_RULES, *TRACE_RULES)}

SCAN_RULE_IDS: tuple[str, ...] = tuple(rule.id for rule in SCAN_RULES)
TRACE_RULE_IDS: tuple[str, ...] = tuple(rule.id for rule in TRACE_RULES)


def get_rule(rule_id: str) -> Rule:
    """Return the registered rule or raise ``KeyError`` with a clear message."""

    try:
        return RULES[rule_id]
    except KeyError as exc:  # pragma: no cover - programming error
        raise KeyError(f"unknown rule: {rule_id}") from exc


def build_finding(
    rule_id: str,
    *,
    file: str,
    line: int,
    message: str,
    evidence: list[str] | None = None,
) -> Finding:
    """Create a :class:`Finding` populated from the rule registry."""

    rule = get_rule(rule_id)
    return Finding(
        rule=rule.id,
        severity=rule.severity,
        title=rule.title,
        message=message,
        file=file,
        line=line,
        evidence=list(evidence or []),
        fix=rule.fix,
        docs=rule.docs,
    )


def format_rules_text() -> str:
    """Render the rule table printed by ``acr rules``."""

    lines: list[str] = []

    def _table(title: str, rules: tuple[Rule, ...]) -> None:
        lines.append(title)
        lines.append("")
        lines.append(f"{'ID':<8}{'SEVERITY':<10}TITLE")
        for rule in rules:
            lines.append(f"{rule.id:<8}{rule.severity:<10}{rule.title}")
        lines.append("")
        for rule in rules:
            lines.append(f"{rule.id}: {rule.summary}")
            lines.append(f"        fix: {rule.fix}")
            lines.append(f"        docs: {rule.docs}")
        lines.append("")

    _table("Static analysis rules (acr scan)", SCAN_RULES)
    _table("Runtime trace rules (acr trace)", TRACE_RULES)
    lines.append(
        "Severities: high = likely broken composition; medium = review and "
        "confirm; low = informational. Rule docs live at docs/rules/<RULE>.md."
    )
    return "\n".join(lines)
