"""Static AST analysis for LangGraph/LangChain composition code.

``acr scan`` parses Python files (no imports, no execution, no network) and
reports composition hazards such as a compiled subgraph leaking its state into
the parent graph or parallel branches writing the same channel without a
reducer.

The analyser is intentionally conservative:

* it only reasons about graph construction that is syntactically visible in the
  scanned file;
* rules that need state-schema knowledge (ACR001 / ACR002) are skipped when the
  relevant ``TypedDict`` lives in another file or the writes cannot be resolved;
* dynamic construction (``for`` loops registering nodes, factories returning
  graphs, ...) is ignored rather than guessed.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .model import Finding, ScanResult, sort_findings
from .rules import build_finding

SKIP_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "site-packages",
    "venv",
}

INVOKE_METHODS = {"invoke", "ainvoke", "stream", "astream", "batch", "abatch", "run", "arun"}

CHECKPOINTER_METHODS = {
    "get_state",
    "aget_state",
    "get_state_history",
    "aget_state_history",
    "update_state",
    "aupdate_state",
}

CACHE_DECORATORS = {"lru_cache", "cache"}
BUILDER_METHODS = {
    "add_node",
    "add_edge",
    "add_conditional_edges",
    "set_entry_point",
    "set_finish_point",
    "compile",
}


class ScanError(RuntimeError):
    """Raised when the scan target cannot be read or parsed."""


# ---------------------------------------------------------------------------
# Small AST helpers
# ---------------------------------------------------------------------------


def expr_name(node: ast.expr | None) -> str | None:
    """Return a dotted name for a name/attribute/call expression."""

    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = expr_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return expr_name(node.func)
    if isinstance(node, ast.Subscript):
        return expr_name(node.value)
    return None


def _last_name(name: str | None) -> str | None:
    return name.split(".")[-1] if name else None


def _class_arg_name(node: ast.expr | None) -> str | None:
    """Resolve a class expression used as a schema argument."""

    if node is None:
        return None
    if isinstance(node, (ast.Name, ast.Attribute)):
        return expr_name(node)
    if isinstance(node, ast.Subscript):  # e.g. StateGraph(SomeGeneric[State])
        return expr_name(node.value)
    return None


def _is_typed_dict_base(node: ast.expr) -> bool:
    name = _last_name(expr_name(node))
    return name == "TypedDict"


def _annotated_parts(annotation: ast.expr) -> tuple[ast.expr, ...] | None:
    """Return the members of ``Annotated[...]``.

    TypedDict qualifiers (``Required``/``NotRequired``/``ReadOnly``) wrap the
    annotation without changing the reducer; unwrap them before looking for
    ``Annotated``.
    """

    if not isinstance(annotation, ast.Subscript):
        return None
    base = _last_name(expr_name(annotation.value))
    if base == "Annotated":
        slice_node = annotation.slice
        if isinstance(slice_node, ast.Tuple):
            return tuple(slice_node.elts)
        return (slice_node,)
    if base in {"Required", "NotRequired", "ReadOnly"}:
        return _annotated_parts(annotation.slice)
    return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _is_false_literal(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value in (None, False, 0, "")


def _nested_stmt_bodies(stmt: ast.stmt) -> Iterator[list[ast.stmt]]:
    for _, value in ast.iter_fields(stmt):
        if isinstance(value, list) and value and all(isinstance(item, ast.stmt) for item in value):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, ast.ExceptHandler):
                    yield item.body
                elif isinstance(item, ast.match_case):
                    yield item.body


def _shallow_body_nodes(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.AST]:
    """Yield nodes in a function body without descending into nested functions."""

    stack: list[ast.AST] = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _return_keys(value: ast.expr | None) -> set[str] | None:
    """Keys of a dict literal returned by a node.

    Returns ``None`` when the returned expression is not statically a dict
    literal (``return state``, ``return helper()``, ``return {**state}``, ...).
    """

    if value is None:
        return set()
    if isinstance(value, ast.Dict):
        keys: set[str] = set()
        for key in value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value)
            else:  # ``{**state}`` or a computed key: unknown
                return None
        return keys
    if isinstance(value, ast.Call) and _last_name(expr_name(value.func)) == "dict":
        if value.args or any(keyword.arg is None for keyword in value.keywords):
            return None
        return {keyword.arg for keyword in value.keywords if keyword.arg}
    if isinstance(value, ast.Constant) and value.value is None:
        return set()
    return None


def _graph_return_ref(value: ast.expr | None) -> tuple[str, str, str | None] | None:
    """Recognise a helper that returns a graph.

    ``return child.compile()`` -> ``("compile", "child", output_schema)``.
    ``return child`` -> ``("name", "child", None)``.
    """

    if value is None:
        return None
    if isinstance(value, ast.Call):
        if isinstance(value.func, ast.Attribute) and value.func.attr == "compile":
            base = expr_name(value.func.value)
            if base:
                output = _class_arg_name(_keyword(value, "output_schema"))
                return ("compile", base, output)
        return None
    if isinstance(value, ast.Name):
        return ("name", value.id, None)
    return None


def display_path(path: Path) -> str:
    """Return a stable, repository-relative display path when possible."""

    try:
        resolved = path.resolve()
        relative = Path(os.path.relpath(resolved, Path.cwd().resolve()))
    except (OSError, ValueError):
        return str(path)
    if str(relative).startswith(".."):
        return resolved.as_posix()
    return relative.as_posix()


def discover_python_files(target: Path) -> list[Path]:
    """Return Python files under ``target`` in deterministic order."""

    if target.is_file():
        return [target] if target.suffix == ".py" else []
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in SKIP_DIRS and not name.startswith(".")
        )
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                files.append(Path(dirpath) / filename)
    return files


# ---------------------------------------------------------------------------
# Intermediate models
# ---------------------------------------------------------------------------


@dataclass
class TypedDictKey:
    name: str
    line: int
    annotation: str
    reducer: str | None


@dataclass
class TypedDictInfo:
    name: str
    line: int
    keys: dict[str, TypedDictKey]


@dataclass
class FunctionInfo:
    name: str
    line: int
    return_keys: set[str] | None
    return_lines: list[int] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    cache_decorators: list[tuple[str, int]] = field(default_factory=list)
    retry_decorators: list[tuple[str, int]] = field(default_factory=list)
    checkpointer_calls: list[tuple[str, int]] = field(default_factory=list)
    # (kind, graph variable, output_schema) when a helper returns a compiled
    # graph: ``return child.compile()`` -> ("compile", "child", schema) and
    # ``return child`` -> ("name", "child", None). Used to resolve
    # ``add_node("child", build_child())`` and ``child = build_child()``.
    return_graph_ref: tuple[str, str, str | None] | None = None


@dataclass
class NodeSpec:
    name: str
    line: int
    action_kind: str = "unknown"  # function | subgraph | unknown
    func_name: str | None = None
    graph_var: str | None = None


@dataclass
class EdgeSpec:
    src: str
    dst: str
    line: int


@dataclass
class CompiledInfo:
    graph_var: str
    line: int
    cache: bool = False


@dataclass
class GraphSpec:
    var: str
    line: int
    state_name: str | None
    output_schema: str | None
    nodes: dict[str, NodeSpec] = field(default_factory=dict)
    edges: list[EdgeSpec] = field(default_factory=list)
    compile_line: int | None = None
    compile_cache: bool = False
    compile_output_schema: str | None = None
    compiled_var: str | None = None


# ---------------------------------------------------------------------------
# Per-file scanner
# ---------------------------------------------------------------------------


class FileScanner:
    """Collect graph construction facts for one Python file and run the rules."""

    def __init__(self, path: Path, display: str) -> None:
        self.path = path
        self.display = display
        self.typed_dicts: dict[str, TypedDictInfo] = {}
        self.functions: dict[str, FunctionInfo] = {}
        self.graphs: dict[str, GraphSpec] = {}
        self.compiled: dict[str, CompiledInfo] = {}
        self.stategraph_names: set[str] = set()
        self.has_langgraph_import = False
        self.findings: list[Finding] = []
        self._seen: set[tuple[str, int, str]] = set()

    # -- entry point ------------------------------------------------------

    def scan(self) -> list[Finding]:
        try:
            source = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ScanError(f"cannot read {self.display}: {exc}") from exc
        return self.scan_text(source)

    def scan_text(self, source: str) -> list[Finding]:
        try:
            tree = ast.parse(source, filename=str(self.path))
        except SyntaxError as exc:
            raise ScanError(
                f"cannot parse {self.display}:{exc.lineno or 0}: {exc.msg}"
            ) from exc

        self._collect_imports(tree)
        self._collect_typed_dicts(tree)
        self._collect_functions(tree)
        self._scan_scope(tree.body)
        if self.has_langgraph_import:
            self._run_rules()
        return self.findings

    # -- collection -------------------------------------------------------

    def _collect_imports(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.split(".")[0] != "langgraph":
                    continue
                self.has_langgraph_import = True
                for alias in node.names:
                    if alias.name == "StateGraph":
                        self.stategraph_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "langgraph":
                        self.has_langgraph_import = True

    def _collect_typed_dicts(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(_is_typed_dict_base(base) for base in node.bases):
                continue
            keys: dict[str, TypedDictKey] = {}
            for item in node.body:
                if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
                    continue
                key_name = item.target.id
                parts = _annotated_parts(item.annotation)
                if parts:
                    reducer: str | None = None
                    if len(parts) >= 2:
                        reducer = expr_name(parts[1]) or ast.unparse(parts[1])
                    annotation = ast.unparse(parts[0])
                else:
                    reducer = None
                    annotation = ast.unparse(item.annotation)
                keys[key_name] = TypedDictKey(
                    name=key_name,
                    line=getattr(item, "lineno", node.lineno),
                    annotation=annotation,
                    reducer=reducer,
                )
            self.typed_dicts[node.name] = TypedDictInfo(
                name=node.name, line=node.lineno, keys=keys
            )

    def _collect_functions(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = self._analyze_function(node)

    def _analyze_function(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionInfo:
        cache_decorators: list[tuple[str, int]] = []
        retry_decorators: list[tuple[str, int]] = []
        for decorator in fn.decorator_list:
            name = expr_name(decorator) or ""
            base = _last_name(name) or ""
            line = getattr(decorator, "lineno", fn.lineno)
            if base in CACHE_DECORATORS or base.endswith("lru_cache"):
                cache_decorators.append((base, line))
            if "retry" in base.lower():
                retry_decorators.append((base, line))

        return_keys: set[str] = set()
        unknown_return = False
        return_lines: list[int] = []
        return_graph_refs: list[tuple[str, str, str | None]] = []
        side_effects: list[str] = []
        checkpointer_calls: list[tuple[str, int]] = []

        for node in _shallow_body_nodes(fn):
            if isinstance(node, ast.Return):
                ref = _graph_return_ref(node.value)
                if ref is not None:
                    return_graph_refs.append(ref)
                parsed = _return_keys(node.value)
                if parsed is None:
                    unknown_return = True
                else:
                    return_keys |= parsed
                return_lines.append(node.lineno)
                continue
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
                target = expr_name(node.value) or ""
                if "state" in target.lower():
                    side_effects.append(
                        f"mutates `{target}[...]` in place at line {node.lineno}"
                    )
                continue
            if not isinstance(node, ast.Call):
                continue
            call_name = expr_name(node.func) or ""
            base = _last_name(call_name) or ""
            if base in INVOKE_METHODS:
                side_effects.append(f"calls external `. {base}()` at line {node.lineno}")
            if "tool" in call_name.lower():
                side_effects.append(f"calls tool `{call_name}()` at line {node.lineno}")
            if base in CHECKPOINTER_METHODS:
                checkpointer_calls.append((base, node.lineno))
                if base in {"update_state", "aupdate_state"}:
                    side_effects.append(f"calls `{base}()` at line {node.lineno}")
            if isinstance(node.func, ast.Attribute):
                target = expr_name(node.func.value) or ""
                if base == "update" and "state" in target.lower():
                    side_effects.append(
                        f"mutates `{target}` via `.update()` at line {node.lineno}"
                    )

        if return_keys:
            keys = ", ".join(sorted(return_keys))
            first_line = return_lines[0] if return_lines else fn.lineno
            side_effects.append(f"returns state update key(s) [{keys}] at line {first_line}")

        unique_graph_refs = list(dict.fromkeys(return_graph_refs))
        return FunctionInfo(
            name=fn.name,
            line=fn.lineno,
            return_keys=None if unknown_return else return_keys,
            return_lines=return_lines,
            side_effects=_dedupe(side_effects),
            cache_decorators=cache_decorators,
            retry_decorators=retry_decorators,
            checkpointer_calls=_dedupe_pairs(checkpointer_calls),
            return_graph_ref=(
                unique_graph_refs[0] if len(unique_graph_refs) == 1 else None
            ),
        )

    # -- graph construction ----------------------------------------------

    def _scan_scope(self, body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                self._handle_assign(stmt)
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                self._handle_expr_call(stmt.value)

            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._scan_scope(stmt.body)
            else:
                for nested in _nested_stmt_bodies(stmt):
                    self._scan_scope(nested)

    def _handle_assign(self, stmt: ast.Assign | ast.AnnAssign) -> None:
        target: str | None = None
        if isinstance(stmt, ast.Assign):
            for node in stmt.targets:
                if isinstance(node, ast.Name):
                    target = node.id
                    break
            value = stmt.value
        else:
            if stmt.value is None or not isinstance(stmt.target, ast.Name):
                return
            target = stmt.target.id
            value = stmt.value
        if value is None or not isinstance(value, ast.Call):
            return

        if self._is_stategraph_call(value):
            graph = self._new_graph(target or f"<inline@{value.lineno}>", value)
            if target:
                self.graphs[target] = graph
            return

        if self._is_compile_call(value):
            base = value.func.value
            graph = self._resolve_graph(base)
            if graph is None:
                return
            self._mark_compiled(graph, value)
            if target:
                self.compiled[target] = CompiledInfo(
                    graph_var=graph.var,
                    line=value.lineno,
                    cache=graph.compile_cache,
                )
                graph.compiled_var = target
            return

        helper = self._resolve_helper_graph(value)
        if helper is not None:
            if target:
                self.compiled[target] = CompiledInfo(
                    graph_var=helper.var,
                    line=value.lineno,
                    cache=helper.compile_cache,
                )
                helper.compiled_var = target
            return

    def _handle_expr_call(self, call: ast.Call) -> None:
        func = call.func
        if not isinstance(func, ast.Attribute):
            return
        method = func.attr
        if method not in BUILDER_METHODS:
            return
        # Chained builder calls: builder.add_node(...).add_edge(...) -> visit inner first.
        if isinstance(func.value, ast.Call):
            self._handle_expr_call(func.value)
        graph = self._resolve_graph(func.value)
        if graph is None:
            return
        if method == "add_node":
            self._add_node(graph, call)
        elif method == "add_edge":
            self._add_edge(graph, call)
        elif method == "add_conditional_edges":
            pass  # conditional fan-out is an explicit routing decision
        elif method == "set_entry_point":
            if call.args:
                dst = self._node_ref(call.args[0])
                if dst:
                    graph.edges.append(EdgeSpec("START", dst, call.lineno))
        elif method == "set_finish_point":
            if call.args:
                src = self._node_ref(call.args[0])
                if src:
                    graph.edges.append(EdgeSpec(src, "END", call.lineno))
        elif method == "compile":
            self._mark_compiled(graph, call)

    def _add_node(self, graph: GraphSpec, call: ast.Call) -> None:
        if not call.args:
            return
        first = call.args[0]
        action: ast.expr | None = None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            name = first.value
            action = call.args[1] if len(call.args) > 1 else None
        else:
            name = self._callable_name(first)
            action = first
        if not name:
            return
        node = NodeSpec(name=name, line=call.lineno)
        if action is not None:
            if isinstance(action, ast.Call) and self._is_compile_call(action):
                child = self._resolve_graph(action.func.value)
                if child is not None:
                    node.action_kind = "subgraph"
                    node.graph_var = child.var
                    self._mark_compiled(child, action)
            elif isinstance(action, ast.Call):
                # Helper returning a compiled graph, e.g.
                # add_node("child", build_child()).
                child = self._resolve_helper_graph(action)
                if child is not None:
                    node.action_kind = "subgraph"
                    node.graph_var = child.var
            elif isinstance(action, ast.Name):
                if action.id in self.functions:
                    node.action_kind = "function"
                    node.func_name = action.id
                elif action.id in self.compiled:
                    node.action_kind = "subgraph"
                    node.graph_var = self.compiled[action.id].graph_var
        graph.nodes[name] = node

    def _add_edge(self, graph: GraphSpec, call: ast.Call) -> None:
        if len(call.args) < 2:
            return
        src = self._node_ref(call.args[0])
        dst = self._node_ref(call.args[1])
        if src and dst:
            graph.edges.append(EdgeSpec(src, dst, call.lineno))

    def _mark_compiled(self, graph: GraphSpec, call: ast.Call) -> None:
        graph.compile_line = call.lineno
        cache = _keyword(call, "cache")
        if cache is not None and not _is_false_literal(cache):
            graph.compile_cache = True
        output = _keyword(call, "output_schema")
        if output is not None:
            graph.compile_output_schema = _class_arg_name(output)

    def _new_graph(self, var: str, call: ast.Call) -> GraphSpec:
        state_name = _class_arg_name(call.args[0]) if call.args else None
        if state_name is None:
            state_name = _class_arg_name(_keyword(call, "state_schema"))
        output_schema = _class_arg_name(_keyword(call, "output_schema"))
        if output_schema is None:
            output_schema = _class_arg_name(_keyword(call, "output"))
        graph = GraphSpec(
            var=var,
            line=call.lineno,
            state_name=state_name,
            output_schema=output_schema,
        )
        self.graphs[var] = graph
        return graph

    def _resolve_graph(self, expr: ast.expr | None) -> GraphSpec | None:
        if expr is None:
            return None
        if isinstance(expr, ast.Name):
            return self.graphs.get(expr.id)
        if isinstance(expr, ast.Call):
            if self._is_stategraph_call(expr):
                existing = self.graphs.get(f"<inline@{expr.lineno}>")
                if existing is not None:
                    return existing
                return self._new_graph(f"<inline@{expr.lineno}>", expr)
            func = expr.func
            if isinstance(func, ast.Attribute) and func.attr in BUILDER_METHODS:
                return self._resolve_graph(func.value)
        return None

    def _resolve_helper_graph(self, call: ast.Call) -> GraphSpec | None:
        """Resolve a helper call whose return value is a compiled graph.

        This is deliberately narrow: only zero-config helpers that return
        ``child.compile()`` or an already-compiled ``child`` are resolved. A
        helper that builds graphs dynamically or conditionally returns no
        unique graph and is skipped.
        """

        name = _last_name(expr_name(call.func))
        if not name:
            return None
        info = self.functions.get(name)
        if info is None or info.return_graph_ref is None:
            return None
        kind, graph_name, output_schema = info.return_graph_ref
        if kind == "compile":
            graph = self.graphs.get(graph_name)
            if graph is None:
                return None
            if graph.compile_line is None:
                graph.compile_line = info.line
            if output_schema and graph.compile_output_schema is None:
                graph.compile_output_schema = output_schema
            return graph
        if kind == "name":
            compiled = self.compiled.get(graph_name)
            if compiled is not None:
                return self.graphs.get(compiled.graph_var)
            graph = self.graphs.get(graph_name)
            if graph is not None and graph.compile_line is not None:
                return graph
        return None

    def _is_stategraph_call(self, call: ast.Call) -> bool:
        name = expr_name(call.func)
        if not name:
            return False
        if name in self.stategraph_names:
            return True
        return bool(self.has_langgraph_import and name.endswith(".StateGraph"))

    @staticmethod
    def _is_compile_call(call: ast.Call) -> bool:
        return isinstance(call.func, ast.Attribute) and call.func.attr == "compile"

    @staticmethod
    def _callable_name(expr: ast.expr) -> str | None:
        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Attribute):
            return expr.attr
        return None

    @staticmethod
    def _node_ref(expr: ast.expr) -> str | None:
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return expr.value
        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Attribute):
            return expr.attr
        return None

    # -- rule helpers -----------------------------------------------------

    def _typed_dict(self, name: str | None) -> TypedDictInfo | None:
        if not name:
            return None
        if name in self.typed_dicts:
            return self.typed_dicts[name]
        return self.typed_dicts.get(name.split(".")[-1])

    @staticmethod
    def _child_output_schema(graph: GraphSpec) -> str | None:
        return graph.output_schema or graph.compile_output_schema

    def _node_write_keys(self, node: NodeSpec) -> set[str] | None:
        if node.action_kind == "function":
            info = self.functions.get(node.func_name or "")
            if info is None or info.return_keys is None:
                return None
            return set(info.return_keys)
        if node.action_kind == "subgraph":
            child = self.graphs.get(node.graph_var or "")
            if child is None:
                return None
            output_schema = self._child_output_schema(child)
            if output_schema:
                schema = self._typed_dict(output_schema)
                return set(schema.keys) if schema else None
            child_state = self._typed_dict(child.state_name)
            return set(child_state.keys) if child_state else None
        return None

    def _emit(
        self,
        rule_id: str,
        *,
        line: int,
        message: str,
        evidence: list[str],
    ) -> None:
        key = (rule_id, line, message)
        if key in self._seen:
            return
        self._seen.add(key)
        self.findings.append(
            build_finding(
                rule_id,
                file=self.display,
                line=line,
                message=message,
                evidence=evidence,
            )
        )

    # -- rules ------------------------------------------------------------

    def _run_rules(self) -> None:
        acr001_sites = self._rule_acr001()
        self._rule_acr002()
        self._rule_acr003(acr001_sites)
        self._rule_acr004()
        self._rule_acr005()
        self._rule_acr006()

    def _rule_acr001(self) -> set[tuple[str, str]]:
        sites: set[tuple[str, str]] = set()
        for graph in self.graphs.values():
            parent_state = self._typed_dict(graph.state_name)
            if parent_state is None:
                continue
            for node in graph.nodes.values():
                if node.action_kind != "subgraph":
                    continue
                child = self.graphs.get(node.graph_var or "")
                if child is None or self._child_output_schema(child) is not None:
                    continue
                child_state = self._typed_dict(child.state_name)
                if child_state is None:
                    continue
                shared = sorted(set(parent_state.keys) & set(child_state.keys))
                if not shared:
                    continue
                sites.add((graph.var, node.name))

                shared_label = ", ".join(f"'{key}'" for key in shared)
                evidence: list[str] = [
                    f"shared state key(s): {shared_label}",
                    f"parent state '{parent_state.name}' declared at line "
                    f"{parent_state.line}; key line(s): "
                    + ", ".join(
                        f"'{key}' at line {parent_state.keys[key].line}" for key in shared
                    ),
                    f"child state '{child_state.name}' declared at line "
                    f"{child_state.line}; key line(s): "
                    + ", ".join(
                        f"'{key}' at line {child_state.keys[key].line}" for key in shared
                    ),
                    f"child graph compiled without output_schema at line "
                    f"{child.compile_line or child.line}",
                    f"parent node '{node.name}' registered with add_node at line {node.line}",
                ]
                message = (
                    f"Compiled subgraph registered as node '{node.name}' has no "
                    f"output_schema, and child state '{child_state.name}' shares "
                    f"{shared_label} with parent state '{parent_state.name}'. The "
                    "child's full state is merged back into the parent, so writes "
                    "from this node can collide with sibling branches."
                )
                self._emit("ACR001", line=node.line, message=message, evidence=evidence)
        return sites

    def _rule_acr002(self) -> None:
        for graph in self.graphs.values():
            parent_state = self._typed_dict(graph.state_name)
            if parent_state is None:
                continue
            outgoing: dict[str, dict[str, int]] = {}
            for edge in graph.edges:
                if edge.dst == "END":
                    continue
                outgoing.setdefault(edge.src, {}).setdefault(edge.dst, edge.line)

            for source, destinations in outgoing.items():
                if len(destinations) < 2:
                    continue
                writes: dict[str, set[str]] = {}
                for destination in destinations:
                    node = graph.nodes.get(destination)
                    if node is None:
                        continue
                    keys = self._node_write_keys(node)
                    if keys:
                        writes[destination] = keys
                if len(writes) < 2:
                    continue

                key_to_nodes: dict[str, list[str]] = {}
                for destination, keys in writes.items():
                    for key in keys:
                        key_to_nodes.setdefault(key, []).append(destination)

                for key, branch_nodes in key_to_nodes.items():
                    branch_nodes = sorted(set(branch_nodes))
                    if len(branch_nodes) < 2:
                        continue
                    typed_key = parent_state.keys.get(key)
                    if typed_key is None or typed_key.reducer is not None:
                        continue
                    edge_lines = sorted(
                        destinations[node_name] for node_name in branch_nodes
                    )
                    source_label = (
                        "START" if source == "START" else f"node '{source}'"
                    )
                    branches = ", ".join(f"'{node_name}'" for node_name in branch_nodes)
                    evidence = [
                        f"source {source_label} has unconditional edges to {branches} "
                        f"(edge line(s): {', '.join(str(line) for line in edge_lines)})",
                        f"state key '{key}' declared at line {typed_key.line} with "
                        f"annotation '{typed_key.annotation}' and no reducer",
                    ]
                    for node_name in branch_nodes:
                        node = graph.nodes[node_name]
                        evidence.append(
                            f"node '{node_name}' writes '{key}' (add_node at line "
                            f"{node.line})"
                        )
                    message = (
                        f"After {source_label}, branches {branches} can run in the "
                        f"same superstep and all write state key '{key}', which has "
                        "no reducer. LangGraph raises InvalidUpdateError for "
                        "concurrent writes to a non-reduced key."
                    )
                    self._emit(
                        "ACR002",
                        line=min(edge_lines),
                        message=message,
                        evidence=evidence,
                    )

    def _rule_acr003(self, acr001_sites: set[tuple[str, str]]) -> None:
        for graph in self.graphs.values():
            for node in graph.nodes.values():
                if node.action_kind != "subgraph":
                    continue
                if (graph.var, node.name) in acr001_sites:
                    continue
                child = self.graphs.get(node.graph_var or "")
                if child is None or self._child_output_schema(child) is not None:
                    continue
                child_state = self._typed_dict(child.state_name)
                key_label = (
                    ", ".join(sorted(child_state.keys)) if child_state else "unknown"
                )
                evidence = [
                    f"child graph compiled without output_schema at line "
                    f"{child.compile_line or child.line}",
                    f"child state keys passed back to the parent: {key_label}",
                    f"parent node '{node.name}' registered with add_node at line {node.line}",
                ]
                message = (
                    f"Subgraph node '{node.name}' has no output_schema. The complete "
                    f"child state (keys: {key_label}) is passed back to the parent "
                    "instead of a narrow, intentional output."
                )
                self._emit("ACR003", line=node.line, message=message, evidence=evidence)

    def _rule_acr004(self) -> None:
        for graph in self.graphs.values():
            if graph.compile_cache:
                side_effect_nodes = [
                    node
                    for node in graph.nodes.values()
                    if node.action_kind == "function"
                    and self.functions.get(node.func_name or "") is not None
                    and self.functions[node.func_name or ""].side_effects
                ]
                if side_effect_nodes:
                    names = ", ".join(f"'{node.name}'" for node in side_effect_nodes)
                    evidence = [
                        f"graph compiled with cache=... at line "
                        f"{graph.compile_line or graph.line}",
                        f"side-effecting node(s): {names}",
                    ]
                    message = (
                        "The graph is compiled with a cache while node(s) "
                        f"{names} have side effects. Cached results can skip the "
                        "side effect or replay an old state update."
                    )
                    self._emit(
                        "ACR004",
                        line=graph.compile_line or graph.line,
                        message=message,
                        evidence=evidence,
                    )

            for node in graph.nodes.values():
                if node.action_kind != "function":
                    continue
                info = self.functions.get(node.func_name or "")
                if info is None or not info.side_effects or not info.cache_decorators:
                    continue
                decorator, decorator_line = info.cache_decorators[0]
                evidence = [
                    f"node function '{info.name}' decorated with @{decorator} at "
                    f"line {decorator_line}",
                    f"registered as node '{node.name}' at line {node.line}",
                    *info.side_effects,
                ]
                message = (
                    f"Node '{node.name}' uses the cache decorator @{decorator} but "
                    "has side effects. A cached run can skip the side effect or "
                    "reuse a stale state update."
                )
                self._emit(
                    "ACR004", line=decorator_line, message=message, evidence=evidence
                )

    def _rule_acr005(self) -> None:
        for graph in self.graphs.values():
            for node in graph.nodes.values():
                if node.action_kind != "function":
                    continue
                info = self.functions.get(node.func_name or "")
                if info is None or not info.side_effects or not info.retry_decorators:
                    continue
                decorator, decorator_line = info.retry_decorators[0]
                evidence = [
                    f"node function '{info.name}' decorated with retry "
                    f"@{decorator} at line {decorator_line}",
                    f"registered as node '{node.name}' at line {node.line}",
                    *info.side_effects,
                ]
                message = (
                    f"Node '{node.name}' retries via @{decorator} but has side "
                    "effects. A retried attempt can apply the same write or tool "
                    "call twice."
                )
                self._emit(
                    "ACR005", line=decorator_line, message=message, evidence=evidence
                )

    def _rule_acr006(self) -> None:
        for graph in self.graphs.values():
            for node in graph.nodes.values():
                if node.action_kind != "function":
                    continue
                info = self.functions.get(node.func_name or "")
                if info is None or not info.checkpointer_calls:
                    continue
                calls = ", ".join(
                    f"{name}() at line {line}"
                    for name, line in info.checkpointer_calls
                )
                evidence = [
                    f"node function '{info.name}' interacts with the checkpointer: "
                    f"{calls}",
                    f"registered as node '{node.name}' at line {node.line}",
                ]
                message = (
                    f"Node '{node.name}' reads or writes graph state through "
                    f"checkpointer methods ({calls}) instead of the state channels. "
                    "Composition analysis and reducers cannot see those writes."
                )
                first_line = info.checkpointer_calls[0][1]
                self._emit("ACR006", line=first_line, message=message, evidence=evidence)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _dedupe_pairs(items: list[tuple[str, int]]) -> list[tuple[str, int]]:
    seen: set[tuple[str, int]] = set()
    result: list[tuple[str, int]] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# Public scan API
# ---------------------------------------------------------------------------


def scan_path(path: str | Path) -> ScanResult:
    """Scan a Python file or directory and return a :class:`ScanResult`."""

    target = Path(path)
    if not target.exists():
        raise ScanError(f"path not found: {path}")
    files = discover_python_files(target)
    if not files:
        raise ScanError(f"no Python files found: {path}")

    findings: list[Finding] = []
    errors: list[str] = []
    parsed = 0
    skipped = 0
    for file_path in files:
        display = display_path(file_path)
        try:
            file_findings = FileScanner(file_path, display).scan()
        except ScanError as exc:
            skipped += 1
            errors.append(str(exc))
            continue
        parsed += 1
        findings.extend(file_findings)

    if parsed == 0:
        detail = "; ".join(errors) if errors else "no files could be parsed"
        raise ScanError(detail)

    root = display_path(target)
    return ScanResult(
        root=root,
        findings=sort_findings(findings),
        files_scanned=len(files),
        files_parsed=parsed,
        files_skipped=skipped,
        errors=errors,
    )


def render_findings_text(findings: list[Finding], indent: str = "  ") -> str:
    """Render findings as human-readable text."""

    if not findings:
        return f"{indent}No composition issues found."
    lines: list[str] = []
    for finding in findings:
        lines.append(
            f"{indent}{finding.severity.upper():<6} {finding.rule}  {finding.title}"
        )
        lines.append(f"{indent}  {finding.file}:{finding.line}")
        lines.append(f"{indent}  {finding.message}")
        if finding.evidence:
            lines.append(f"{indent}  evidence:")
            for item in finding.evidence:
                lines.append(f"{indent}    - {item}")
        lines.append(f"{indent}  fix: {finding.fix}")
        lines.append(f"{indent}  docs: {finding.docs}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_scan_text(result: ScanResult) -> str:
    """Render the default (non-JSON) ``acr scan`` output."""

    counts = {severity: 0 for severity in ("high", "medium", "low")}
    for finding in result.findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    lines = [
        f"acr scan - {result.root}",
        f"{result.files_parsed} file(s) parsed, {result.files_skipped} skipped - "
        f"{len(result.findings)} finding(s): high {counts['high']}, "
        f"medium {counts['medium']}, low {counts['low']}",
        "",
        render_findings_text(result.findings),
    ]
    if result.errors:
        lines.append("")
        lines.append("warnings:")
        for error in result.errors:
            lines.append(f"  - {error}")
    return "\n".join(lines)


def scan_source(source: str, filename: str = "<memory>") -> list[Finding]:
    """Scan Python source text (used by tests and library callers)."""

    scanner = FileScanner(Path(filename), filename)
    return sort_findings(scanner.scan_text(source))
