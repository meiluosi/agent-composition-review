import itertools
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from langgraph.graph.state import CompiledStateGraph

from app import build_graph


def expected(s):
    count = s['count'] + 7
    return dict(count=count, label=s['label'] + '|fresh',
                left=s['left'] + count + len(s['label']),
                right=s['right'] + count - len(s['label']))


class GraphTests(unittest.TestCase):
    def test_inputs_and_reuse(self):
        calls = []
        graph = build_graph(calls=calls)
        for count, label, left, right in itertools.product(
                [-11, 0, 1, 10**30], ['', 'old', '汉🙂e\u0301', 'x|fresh'],
                [-19, 0, 23], [-17, 0, 29]):
            s = dict(count=count, label=label, left=left, right=right)
            before = s.copy()
            calls.clear()
            self.assertEqual(graph.invoke(s), expected(s))
            self.assertEqual(s, before)
            self.assertEqual(Counter(calls), Counter(['prepare', 'refresh', 'left', 'right']))
            self.assertEqual(calls[0], 'prepare')
        s = dict(count=1, label='old', left=2, right=3)
        for _ in range(5):
            result = graph.invoke(s)
            self.assertEqual(result, expected(s))
            s = result

    def test_both_parallel_levels(self):
        calls = []
        parent_gate, child_gate = Barrier(2, timeout=5), Barrier(2, timeout=5)
        graph = build_graph(parent_gate, child_gate, calls)
        for i in range(10):
            calls.clear()
            s = dict(count=i, label='snapshot', left=101, right=-202)
            self.assertEqual(graph.invoke(s), expected(s))
            self.assertEqual(Counter(calls), Counter(['prepare', 'refresh', 'left', 'right']))
            self.assertFalse(parent_gate.broken)
            self.assertFalse(child_gate.broken)

    def test_concurrent_invocations_and_graph_isolation(self):
        inputs = [dict(count=i, label=str(i), left=100+i, right=-100-i) for i in range(24)]
        calls = []
        graph = build_graph(calls=calls)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(graph.invoke, inputs))
        self.assertEqual(results, list(map(expected, inputs)))
        self.assertEqual(Counter(calls), Counter(dict.fromkeys(['prepare', 'refresh', 'left', 'right'], 24)))
        logs = [[] for _ in inputs]
        graphs = [build_graph(calls=log) for log in logs]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(g.invoke, s) for g, s in zip(graphs, inputs)]
            self.assertEqual([f.result() for f in futures], list(map(expected, inputs)))
        for log in logs:
            self.assertEqual(Counter(log), Counter(['prepare', 'refresh', 'left', 'right']))

    def test_real_graph_topology_and_reusable_child(self):
        graph = build_graph()
        self.assertIsInstance(graph, CompiledStateGraph)
        edges = {(e.source, e.target) for e in graph.get_graph().edges}
        self.assertTrue({('__start__', 'prepare'), ('prepare', 'refresh'),
                         ('prepare', 'analysis'), ('refresh', '__end__'),
                         ('analysis', '__end__')} <= edges)
        children = dict(graph.get_subgraphs())
        self.assertEqual(set(children), {'analysis'})
        child = children['analysis']
        self.assertIsInstance(child, CompiledStateGraph)
        edges = {(e.source, e.target) for e in child.get_graph().edges}
        self.assertTrue({('__start__', 'left'), ('__start__', 'right')} <= edges)
        for i in range(5):
            s = dict(count=i, label='raw', left=20, right=-30)
            self.assertEqual(child.invoke(s), {'left':20+i+3, 'right':-30+i-3})

    def test_exceptions_propagate_and_recovery(self):
        class GateError(Exception):
            pass

        class FailingGate:
            def wait(self):
                raise GateError('gate failure')

        s = dict(count=1, label='old', left=2, right=3)
        for gates in ({'parent_gate': FailingGate()}, {'child_gate': FailingGate()}):
            with self.assertRaisesRegex(GateError, 'gate failure'):
                build_graph(**gates).invoke(s)
        graph = build_graph()
        with self.assertRaises(TypeError):
            graph.invoke({**s, 'count': None})
        self.assertEqual(graph.invoke(s), expected(s))


if __name__ == '__main__':
    unittest.main(verbosity=2)
