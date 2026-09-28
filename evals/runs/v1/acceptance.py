"""Behavioral acceptance tests, written before candidate repairs."""

import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import threading
import unittest

os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

candidate_path = Path(sys.argv.pop(1)).resolve()
spec = importlib.util.spec_from_file_location("candidate", candidate_path)
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


def initial(a=123, b="old", s1=0, s2=0):
    return dict(a=a, b=b, s1=s1, s2=s2)


def expected(state):
    return dict(a=state["a"] + 10, b=state["b"] + "|parent",
                s1=state["s1"] + 1, s2=state["s2"] + 2)


class AcceptanceTests(unittest.TestCase):
    def test_01_final_state_preserves_each_result(self):
        graph = candidate.create_graph()
        for state in [initial(), initial(-7, "fresh", 41, -9), initial(0, "", 2, 3)]:
            with self.subTest(state=state):
                self.assertEqual(graph.invoke(state), expected(state))

    def test_02_injected_functions_can_read_full_stage_context(self):
        state = initial(5, "context", 2, 3)
        def a(s): return {"a": s["a"] * 2}
        def b(s): return {"b": f"{s['b']}:{s['a']}:{s['s1']}:{s['s2']}"}
        def s1(s): return {"s1": s["a"] + len(s["b"]) + s["s2"]}
        def s2(s): return {"s2": s["a"] - len(s["b"]) - s["s1"]}
        graph = candidate.create_graph(node_a=a, node_b=b, sub_1=s1, sub_2=s2)
        self.assertEqual(graph.invoke(state), dict(a=10, b="context:10:2:3", s1=20, s2=1))

    def test_03_each_processor_runs_once(self):
        calls = {"a": 0, "b": 0, "s1": 0, "s2": 0}
        lock = threading.Lock()
        def record(key, fn):
            def run(state):
                with lock: calls[key] += 1
                return fn(state)
            return run
        graph = candidate.create_graph(
            node_a=record("a", lambda s: {"a": s["a"]+10}),
            node_b=record("b", lambda s: {"b": s["b"]+"|parent"}),
            sub_1=record("s1", lambda s: {"s1": s["s1"]+1}),
            sub_2=record("s2", lambda s: {"s2": s["s2"]+2}),
        )
        self.assertEqual(graph.invoke(initial()), expected(initial()))
        self.assertEqual(calls, dict(a=1, b=1, s1=1, s2=1))

    def test_04_parent_and_subgraph_still_overlap(self):
        gate = threading.Barrier(2, timeout=4)
        def b(s):
            gate.wait()
            return {"b": s["b"]+"|parent"}
        def s1(s):
            gate.wait()
            return {"s1": s["s1"]+1}
        graph = candidate.create_graph(node_b=b, sub_1=s1)
        self.assertEqual(graph.invoke(initial()), expected(initial()))

    def test_05_subgraph_branches_still_overlap(self):
        gate = threading.Barrier(2, timeout=4)
        def s1(s):
            gate.wait()
            return {"s1": s["s1"]+1}
        def s2(s):
            gate.wait()
            return {"s2": s["s2"]+2}
        graph = candidate.create_graph(sub_1=s1, sub_2=s2)
        self.assertEqual(graph.invoke(initial()), expected(initial()))

    def test_06_reused_graph_has_no_cross_request_state(self):
        graph = candidate.create_graph()
        for state in [initial(), initial(90, "second", 21, 32), initial()]:
            self.assertEqual(graph.invoke(state), expected(state))

    def test_07_async_invoke_preserves_result(self):
        result = asyncio.run(candidate.create_graph().ainvoke(initial()))
        self.assertEqual(result, expected(initial()))

    def test_08_existing_subgraph_remains(self):
        graph = candidate.create_graph()
        self.assertTrue(callable(getattr(graph, "invoke", None)))
        # The repair must retain an actual compiled child graph; a wrapper may
        # expose it through closure instead of registering it directly.
        from langgraph.graph.state import CompiledStateGraph
        self.assertIsInstance(graph, CompiledStateGraph)
        self.assertIn("subgraph", graph.nodes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
