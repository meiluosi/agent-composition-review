import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest

from langgraph.graph.state import CompiledStateGraph

from app import build_graph


def expected(s):
    count = s['count'] + 7
    return dict(count=count, label=s['label'] + '|fresh',
                left=s['left'] + count + len(s['label']),
                right=s['right'] + count - len(s['label']))


class GraphTests(unittest.TestCase):
    def assert_calls(self, calls, n=1):
        self.assertEqual(Counter(calls), Counter({
            'prepare': n, 'refresh': n, 'left': n, 'right': n}))

    def test_values_and_repeated_invocations(self):
        calls = []
        graph = build_graph(calls=calls)
        inputs = [
            dict(count=1, label='old', left=2, right=3),
            dict(count=-20, label='', left=-100, right=80),
            dict(count=10**30, label='中文🙂e\u0301|fresh', left=987, right=-654),
        ]
        for s in inputs * 3:
            before = s.copy()
            self.assertEqual(graph.invoke(s), expected(s))
            self.assertEqual(s, before)
        self.assert_calls(calls, 9)
        self.assertEqual(calls[0], 'prepare')

    def test_feedback_uses_current_input(self):
        graph = build_graph()
        s = dict(count=3, label='x', left=55, right=-19)
        for _ in range(5):
            actual = graph.invoke(s)
            self.assertEqual(actual, expected(s))
            s = actual

    def test_both_parallel_boundaries(self):
        calls = []
        rounds = []
        parent = Barrier(2, action=lambda: rounds.append('parent'), timeout=5)
        child = Barrier(2, action=lambda: rounds.append('child'), timeout=5)
        graph = build_graph(parent, child, calls)
        for i in range(4):
            s = dict(count=i, label='snapshot', left=100, right=-100)
            self.assertEqual(graph.invoke(s), expected(s))
        self.assertEqual(Counter(rounds), Counter(parent=4, child=4))
        self.assert_calls(calls, 4)

    def test_compiled_structure_and_reusable_child(self):
        calls = []
        graph = build_graph(calls=calls)
        self.assertIsInstance(graph, CompiledStateGraph)
        edges = {(e.source, e.target) for e in graph.get_graph().edges}
        self.assertEqual(edges, {
            ('__start__', 'prepare'), ('prepare', 'refresh'),
            ('prepare', 'analysis'), ('refresh', '__end__'),
            ('analysis', '__end__')})
        subgraphs = dict(graph.get_subgraphs())
        self.assertEqual(set(subgraphs), {'analysis'})
        child = subgraphs['analysis']
        self.assertIsInstance(child, CompiledStateGraph)
        child_edges = {(e.source, e.target) for e in child.get_graph().edges}
        self.assertEqual(child_edges, {
            ('__start__', 'left'), ('__start__', 'right'),
            ('left', '__end__'), ('right', '__end__')})
        for count in [9, -2, 0]:
            s = dict(count=count, label='raw', left=70, right=-90)
            self.assertEqual(child.invoke(s), {
                'left': 70 + count + 3, 'right': -90 + count - 3})
        self.assertEqual(Counter(calls), Counter(left=3, right=3))

    def test_parallel_invocations_and_graph_independence(self):
        calls_a, calls_b = [], []
        graphs = [build_graph(calls=calls_a), build_graph(calls=calls_b)]
        inputs = [dict(count=i-10, label='x'*i, left=i*7, right=-i*11)
                  for i in range(20)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(graphs[i % 2].invoke, s)
                       for i, s in enumerate(inputs)]
            self.assertEqual([f.result(timeout=15) for f in futures],
                             [expected(s) for s in inputs])
        self.assert_calls(calls_a, 10)
        self.assert_calls(calls_b, 10)

    def test_update_ownership(self):
        graph = build_graph()
        s = dict(count=2, label='abc', left=99, right=-11)
        updates = list(graph.stream(s, stream_mode='updates'))
        by_node = {}
        for update in updates:
            for name, value in update.items():
                self.assertNotIn(name, by_node)
                by_node[name] = value
        self.assertEqual(by_node, {
            'prepare': {'count': 9}, 'refresh': {'label': 'abc|fresh'},
            'analysis': {'left': 111, 'right': -5}})

    def test_gate_exceptions_propagate(self):
        class GateError(Exception):
            pass

        class FailingGate:
            def wait(self):
                raise GateError('gate failed')

        for argument in ['parent_gate', 'child_gate']:
            with self.subTest(argument=argument):
                graph = build_graph(**{argument: FailingGate()})
                with self.assertRaisesRegex(GateError, 'gate failed'):
                    graph.invoke(dict(count=0, label='a', left=2, right=4))

    def test_async_invocation(self):
        calls = []
        graph = build_graph(calls=calls)
        s = dict(count=-3, label='async', left=-50, right=66)
        self.assertEqual(asyncio.run(graph.ainvoke(s)), expected(s))
        self.assert_calls(calls)


if __name__ == '__main__':
    unittest.main()
