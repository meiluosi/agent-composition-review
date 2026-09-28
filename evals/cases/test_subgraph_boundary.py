"""Public-issue-derived regression, independently implemented from #6446."""

import threading
import unittest
from typing import TypedDict

from langgraph.errors import InvalidUpdateError
from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    a: int
    b: str
    s1: int
    s2: int


class ChildOutput(TypedDict):
    s1: int
    s2: int


def build_graph(fixed, parent_gate=None, sibling_gate=None, calls=None):
    def record(key):
        if calls is not None:
            calls.append(key)

    def first(s):
        record("a")
        return {"a": s["a"] + 10}

    def sibling(s):
        record("b")
        if parent_gate:
            parent_gate.wait()
        return {"b": s["b"] + "|updated"}

    def left(s):
        record("s1")
        if parent_gate:
            parent_gate.wait()
        if sibling_gate:
            sibling_gate.wait()
        return {"s1": s["s1"] + s["a"] + len(s["b"])}

    def right(s):
        record("s2")
        if sibling_gate:
            sibling_gate.wait()
        return {"s2": s["s2"] + s["a"] - len(s["b"])}

    child = StateGraph(State, output_schema=ChildOutput) if fixed else StateGraph(State)
    child.add_node("left", left)
    child.add_node("right", right)
    child.add_edge(START, "left")
    child.add_edge(START, "right")
    parent = StateGraph(State)
    parent.add_node("first", first)
    parent.add_node("sibling", sibling)
    parent.add_node("child", child.compile())
    parent.add_edge(START, "first")
    parent.add_edge("first", "sibling")
    parent.add_edge("first", "child")
    parent.add_edge("sibling", END)
    parent.add_edge("child", END)
    return parent.compile()


class SubgraphBoundaryTests(unittest.TestCase):
    def test_broken_version_reproduces_reported_error(self):
        with self.assertRaisesRegex(InvalidUpdateError, "At key 'b'"):
            build_graph(False).invoke(dict(a=1, b="old", s1=0, s2=0))

    def test_repair_keeps_context_and_results_on_reuse(self):
        graph = build_graph(True)
        for a, b in [(1, "old"), (-10, ""), (50, "another"), (1, "old")]:
            result = graph.invoke(dict(a=a, b=b, s1=3, s2=7))
            self.assertEqual(result, dict(a=a+10, b=b+"|updated",
                                         s1=3+a+10+len(b), s2=7+a+10-len(b)))

    def test_repair_preserves_both_parallel_boundaries(self):
        calls = []
        graph = build_graph(True, threading.Barrier(2, timeout=5),
                            threading.Barrier(2, timeout=5), calls)
        graph.invoke(dict(a=1, b="old", s1=0, s2=0))
        self.assertCountEqual(calls, ["a", "b", "s1", "s2"])
