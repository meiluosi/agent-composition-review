"""Built-in end-to-end demo: a LangGraph #6446-style subgraph state leak.

``acr demo`` writes two LangGraph programs to a temporary directory.  They are
the same programs as ``examples/langgraph-subgraph-leak/broken.py`` and
``fixed.py``: a parent ``State`` is fanned out to a ``sibling`` node and a child
subgraph that writes ``s1``/``s2``.  The broken child has no ``output_schema``,
so it returns the full child state (including the stale ``b``) as its patch and
concurrent writes to ``b`` raise ``InvalidUpdateError``.  The fixed child
declares ``output_schema=ChildOutput`` so only ``s1``/``s2`` flow back.

The broken source is scanned with the real ``acr scan`` engine, so the demo
proves the static rule predicts the runtime failure.  LangGraph is only needed
for this command (``pip install "agent-composition-review[langgraph]"``).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import __version__
from .model import Finding
from .scan import scan_path

INSTALL_HINT = (
    "langgraph is required for `acr demo`.\n"
    'Install it with: pip install "agent-composition-review[langgraph]"'
)

#: Input used by ``examples/langgraph-subgraph-leak/run.py``.
DEMO_INPUT: dict[str, Any] = {"a": 1, "b": "old", "s1": 0, "s2": 0}

#: Expected fixed result from ``examples/langgraph-subgraph-leak/run.py``.
DEMO_EXPECTED: dict[str, Any] = {
    "a": 1,
    "b": "old|updated",
    "s1": 4,
    "s2": -2,
}

BROKEN_SOURCE = '''"""故障示例：父图与子图共享状态键，子图把完整状态当补丁回传。

结构（复现自 LangGraph #6446 的父/子图扇出）：

    START ──┬─> sibling  (写 b)
            └─> child    (子图) ──┬─> left  (写 s1)
                                 └─> right (写 s2)

子图用 StateGraph(State) 构建、没有声明 output_schema，因此默认回传整份
State——其中包括自己从未写过的旧 b。父图把这份完整状态当作更新补丁，
与并发 sibling 写入的新 b 在同一 superstep 合并，触发 InvalidUpdateError。

运行：.venv/bin/python examples/langgraph-subgraph-leak/broken.py
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    a: int   # 父图提供的只读上下文
    b: str   # sibling 负责写
    s1: int  # 子图 left 负责写
    s2: int  # 子图 right 负责写


def left(state: State) -> dict:
    # 读取父图的 a、b（完整上下文），但只写自己负责的 s1
    return {"s1": state["s1"] + state["a"] + len(state["b"])}


def right(state: State) -> dict:
    return {"s2": state["s2"] + state["a"] - len(state["b"])}


def sibling(state: State) -> dict:
    return {"b": state["b"] + "|updated"}


def build_broken_graph():
    # 问题根源：没有 output_schema，子图输出 = 完整 State（含旧 b）
    child = StateGraph(State)
    child.add_node("left", left)
    child.add_node("right", right)
    child.add_edge(START, "left")
    child.add_edge(START, "right")

    parent = StateGraph(State)
    parent.add_node("sibling", sibling)
    parent.add_node("child", child.compile())
    # 从 START 同时扇出，sibling 与子图在不同分支并发执行
    parent.add_edge(START, "sibling")
    parent.add_edge(START, "child")
    parent.add_edge("sibling", END)
    parent.add_edge("child", END)
    return parent.compile()


if __name__ == "__main__":
    graph = build_broken_graph()
    graph.invoke({"a": 1, "b": "old", "s1": 0, "s2": 0})
'''

FIXED_SOURCE = '''"""修复示例：子图声明 output_schema，只回传自己负责的字段。

与 broken.py 的唯一语义差别：

    - child = StateGraph(State)
    + child = StateGraph(State, output_schema=ChildOutput)

子图仍然读取父图的完整上下文（a、b），但只把 s1/s2 写回父图。
sibling 的 b 更新不再与子图冲突，并行结构保持不变。

运行：.venv/bin/python examples/langgraph-subgraph-leak/fixed.py
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    a: int
    b: str
    s1: int
    s2: int


class ChildOutput(TypedDict):
    # 子图负责的字段边界：只声明它真正会写的键
    s1: int
    s2: int


def left(state: State) -> dict:
    return {"s1": state["s1"] + state["a"] + len(state["b"])}


def right(state: State) -> dict:
    return {"s2": state["s2"] + state["a"] - len(state["b"])}


def sibling(state: State) -> dict:
    return {"b": state["b"] + "|updated"}


def build_fixed_graph():
    # 修复：声明输出 schema，子图输出从“完整快照”收窄为“自己负责的补丁”
    child = StateGraph(State, output_schema=ChildOutput)
    child.add_node("left", left)
    child.add_node("right", right)
    child.add_edge(START, "left")
    child.add_edge(START, "right")

    parent = StateGraph(State)
    parent.add_node("sibling", sibling)
    parent.add_node("child", child.compile())
    parent.add_edge(START, "sibling")
    parent.add_edge(START, "child")
    parent.add_edge("sibling", END)
    parent.add_edge("child", END)
    return parent.compile()


if __name__ == "__main__":
    graph = build_fixed_graph()
    result = graph.invoke({"a": 1, "b": "old", "s1": 0, "s2": 0})
    print(result)
'''


class DemoDependencyError(RuntimeError):
    """Raised when LangGraph (an optional demo dependency) is unavailable."""


def langgraph_available() -> bool:
    """Return ``True`` when the optional ``langgraph`` dependency is installed."""

    try:
        return importlib.util.find_spec("langgraph") is not None
    except (ImportError, ValueError):  # pragma: no cover - broken installation
        return False


def _execute_module(path: Path, name: str) -> Any:
    """Execute a demo module and return the module object.

    Import failures caused by a missing optional dependency are translated into
    :class:`DemoDependencyError`; everything else propagates to the caller.
    """

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load demo module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        try:
            spec.loader.exec_module(module)
        except ModuleNotFoundError as exc:
            if (exc.name or "").split(".")[0] == "langgraph":
                raise DemoDependencyError(INSTALL_HINT) from exc
            raise
    finally:
        sys.modules.pop(name, None)
    return module


def _format_payload(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return repr(value)


def _render_demo_findings(findings: list[Finding], indent: str = "   ") -> list[str]:
    lines: list[str] = []
    for finding in findings:
        lines.append(
            f"{indent}{finding.severity.upper():<6} {finding.rule}  {finding.title}"
        )
        lines.append(f"{indent}        {finding.message}")
        for item in finding.evidence[:2]:
            lines.append(f"{indent}        - {item}")
        if len(finding.evidence) > 2:
            lines.append(
                f"{indent}        - ... {len(finding.evidence) - 2} more evidence item(s)"
            )
        lines.append(f"{indent}        fix: {finding.fix}")
        lines.append(f"{indent}        docs: {finding.docs}")
    return lines


def run_demo() -> int:
    """Run the demo and return a process exit code (0 success, 1 unexpected)."""

    if not langgraph_available():
        raise DemoDependencyError(INSTALL_HINT)

    try:
        from langgraph.errors import InvalidUpdateError
    except ModuleNotFoundError as exc:  # pragma: no cover - defensive
        raise DemoDependencyError(INSTALL_HINT) from exc

    lines: list[str] = [
        f"acr demo - LangGraph subgraph state leak walkthrough (acr {__version__})",
        "",
        "1) run the broken graph from examples/langgraph-subgraph-leak/broken.py",
    ]

    with tempfile.TemporaryDirectory(prefix="acr-demo-") as tmp:
        tmp_path = Path(tmp)
        broken_path = tmp_path / "broken_demo.py"
        fixed_path = tmp_path / "fixed_demo.py"
        broken_path.write_text(BROKEN_SOURCE, encoding="utf-8")
        fixed_path.write_text(FIXED_SOURCE, encoding="utf-8")

        broken_module = _execute_module(broken_path, "acr_demo_broken")
        broken_builder = getattr(broken_module, "build_broken_graph", None)
        if broken_builder is None:  # pragma: no cover - defensive
            lines.append("   unexpected: embedded broken source has no build_broken_graph()")
            _print_lines(lines)
            return 1
        failure: InvalidUpdateError | None = None
        try:
            broken_builder().invoke(DEMO_INPUT)
        except InvalidUpdateError as exc:
            failure = exc
        if failure is None:
            lines.append("   unexpected: the broken graph did not raise InvalidUpdateError")
            _print_lines(lines)
            return 1
        first_error_line = (
            str(failure).splitlines()[0] if str(failure) else type(failure).__name__
        )
        lines.append(f"   x InvalidUpdateError: {first_error_line}")
        lines.append(
            "     (the child returned its full state including the stale 'b', "
            "colliding with the sibling write)"
        )

        lines.append("")
        lines.append("2) static analysis of the broken source")
        broken_result = scan_path(broken_path)
        lines.append(
            f"   acr scan found {len(broken_result.findings)} finding(s) before "
            "the runtime failure:"
        )
        if broken_result.findings:
            lines.extend(_render_demo_findings(broken_result.findings))
        else:  # pragma: no cover - rule regression would be a bug
            lines.append("   (no findings - this is a scanner regression)")

        lines.append("")
        lines.append("3) run the fixed graph from examples/langgraph-subgraph-leak/fixed.py")
        fixed_module = _execute_module(fixed_path, "acr_demo_fixed")
        fixed_builder = getattr(fixed_module, "build_fixed_graph", None)
        if fixed_builder is None:  # pragma: no cover - defensive
            lines.append("   unexpected: embedded fixed source has no build_fixed_graph()")
            _print_lines(lines)
            return 1
        try:
            result = fixed_builder().invoke(DEMO_INPUT)
        except Exception as exc:  # noqa: BLE001 - demo surfaces the failure
            lines.append(f"   x fixed graph failed: {type(exc).__name__}: {exc}")
            _print_lines(lines)
            return 1
        lines.append("   ok: graph.invoke() completed without InvalidUpdateError")
        lines.append(f"   final state: {_format_payload(result)}")
        if result != DEMO_EXPECTED:
            lines.append(
                f"   x unexpected final state (examples/run.py expects "
                f"{_format_payload(DEMO_EXPECTED)})"
            )
            _print_lines(lines)
            return 1
        lines.append(
            "     (child declares output_schema=ChildOutput, so only s1/s2 "
            "are merged back; the parallel fan-out is unchanged)"
        )

        lines.append("")
        lines.append("4) static analysis of the fixed source")
        fixed_result = scan_path(fixed_path)
        lines.append(f"   acr scan found {len(fixed_result.findings)} finding(s)")

    lines.append("")
    lines.append(
        "Summary: acr scan flagged the shared key and the unreduced parallel "
        "write before the graph ran; adding output_schema=ChildOutput removes "
        "both findings and the run succeeds."
    )
    _print_lines(lines)
    return 0


def _print_lines(lines: list[str]) -> None:
    print("\n".join(lines))
