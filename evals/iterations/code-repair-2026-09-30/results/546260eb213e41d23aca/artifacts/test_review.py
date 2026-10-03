import copy
import json
import random
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from langgraph.graph.state import CompiledStateGraph
from app import build_graph


ACTIONS = Counter(['prepare', 'refresh', 'left', 'right'])


def expected(s):
    return dict(count=s['count'] + 7, label=s['label'] + '|fresh',
                left=s['left'] + s['count'] + 7 + len(s['label']),
                right=s['right'] + s['count'] + 7 - len(s['label']))


class ExplodingGate:
    def wait(self):
        raise RuntimeError('gate failed')


class GraphReview(unittest.TestCase):
    def test_values_and_repeated_invocations(self):
        calls = []
        graph = build_graph(calls=calls)
        rng = random.Random(41)
        for i in range(60):
            s = dict(count=rng.randint(-100, 100),
                     label=['', 'old', '中文🙂', '|fresh', 'a' * 100][i % 5],
                     left=rng.randint(-1000, 1000), right=rng.randint(-1000, 1000))
            before = copy.deepcopy(s)
            calls.clear()
            self.assertEqual(graph.invoke(s), expected(s))
            self.assertEqual(s, before)
            self.assertEqual(Counter(calls), ACTIONS)
            self.assertEqual(calls[0], 'prepare')
        for _ in range(5):
            want = expected(s)
            s = graph.invoke(s)
            self.assertEqual(s, want)

    def test_both_parallel_forks(self):
        calls = []
        parent_gate, child_gate = Barrier(2, timeout=5), Barrier(2, timeout=5)
        graph = build_graph(parent_gate, child_gate, calls)
        for i in range(8):
            calls.clear()
            s = dict(count=i, label='before', left=100, right=-90)
            self.assertEqual(graph.invoke(s), expected(s))
            self.assertEqual(Counter(calls), ACTIONS)
            self.assertFalse(parent_gate.broken)
            self.assertFalse(child_gate.broken)

    def test_same_graph_concurrent_invocations(self):
        calls = []
        graph = build_graph(calls=calls)
        states = [dict(count=i - 10, label=str(i) * (i % 4),
                       left=100 + i, right=-100 - i) for i in range(24)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(graph.invoke, states))
        self.assertEqual(results, list(map(expected, states)))
        self.assertEqual(Counter(calls), Counter({k: 24 for k in ACTIONS}))

    def test_distinct_graphs_are_independent(self):
        logs = [[], [], []]
        graphs = [build_graph(Barrier(2, timeout=5), Barrier(2, timeout=5), log)
                  for log in logs]
        states = [dict(count=i, label=str(i), left=30 * i, right=-40 * i)
                  for i in range(3)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(g.invoke, s) for g, s in zip(graphs, states)]
            self.assertEqual([f.result() for f in futures], list(map(expected, states)))
        for log in logs:
            self.assertEqual(Counter(log), ACTIONS)

    def test_real_graph_topology_and_reusable_child(self):
        calls = []
        graph = build_graph(calls=calls)
        self.assertIsInstance(graph, CompiledStateGraph)
        subgraphs = dict(graph.get_subgraphs())
        self.assertEqual(set(subgraphs), {'analysis'})
        child = subgraphs['analysis']
        self.assertIsInstance(child, CompiledStateGraph)
        parent_edges = {(e.source, e.target) for e in graph.get_graph().edges}
        self.assertTrue({('__start__', 'prepare'), ('prepare', 'refresh'),
                         ('prepare', 'analysis'), ('refresh', '__end__'),
                         ('analysis', '__end__')} <= parent_edges)
        child_edges = {(e.source, e.target) for e in child.get_graph().edges}
        self.assertTrue({('__start__', 'left'), ('__start__', 'right')} <= child_edges)
        for i in range(4):
            calls.clear()
            s = dict(count=i, label='raw', left=7, right=8)
            self.assertEqual(child.invoke(s), {'left': 7 + i + 3, 'right': 8 + i - 3})
            self.assertEqual(Counter(calls), Counter(['left', 'right']))
        s = dict(count=1, label='old', left=2, right=3)
        self.assertEqual(graph.invoke(s), expected(s))

    def test_gate_exceptions_propagate(self):
        for kwargs in ({'parent_gate': ExplodingGate()}, {'child_gate': ExplodingGate()}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(RuntimeError, 'gate failed'):
                    build_graph(**kwargs).invoke(dict(count=1, label='x', left=2, right=3))

    def test_action_exceptions_propagate(self):
        for s, error in [
            (dict(count='bad', label='x', left=2, right=3), TypeError),
            (dict(count=1, label='x', left='bad', right=3), TypeError),
            (dict(count=1, label='x', left=2), KeyError),
        ]:
            with self.subTest(s=s):
                with self.assertRaises(error):
                    build_graph().invoke(s)


if __name__ == '__main__':
    unittest.main(verbosity=2)
