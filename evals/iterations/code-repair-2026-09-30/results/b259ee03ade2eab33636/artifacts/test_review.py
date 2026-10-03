"""Behavior checks for the public graph and its reusable analysis boundary."""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import importlib
import itertools
from threading import Barrier
import sys
import unittest

module = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app')
from langgraph.graph.state import CompiledStateGraph


def expected(s):
    count = s['count'] + 7
    return dict(count=count, label=s['label'] + '|fresh',
                left=s['left'] + count + len(s['label']),
                right=s['right'] + count - len(s['label']))


class GraphReview(unittest.TestCase):
    def test_values_and_repeated_calls(self):
        calls = []
        graph = module.build_graph(calls=calls)
        self.assertIsInstance(graph, CompiledStateGraph)
        cases = itertools.product([-9, 0, 23], ['', 'old', '汉字🙂', 'x|fresh'],
                                  [-20, 17], [-31, 40])
        for count, label, left, right in cases:
            s = dict(count=count, label=label, left=left, right=right)
            before = s.copy()
            start = len(calls)
            self.assertEqual(graph.invoke(s), expected(s))
            self.assertEqual(s, before)
            self.assertEqual(Counter(calls[start:]),
                             Counter(['prepare', 'refresh', 'left', 'right']))
        s = dict(count=1, label='old', left=2, right=3)
        for _ in range(4):
            result = graph.invoke(s)
            self.assertEqual(result, expected(s))
            s = result

    def test_both_parallel_boundaries(self):
        calls = []
        graph = module.build_graph(parent_gate=Barrier(2, timeout=5),
                                   child_gate=Barrier(2, timeout=5), calls=calls)
        for _ in range(3):
            start = len(calls)
            s = dict(count=3, label='before', left=111, right=-222)
            self.assertEqual(graph.invoke(s), expected(s))
            self.assertEqual(Counter(calls[start:]),
                             Counter(['prepare', 'refresh', 'left', 'right']))
            self.assertEqual(calls[start], 'prepare')

    def test_reusable_child_and_topology(self):
        graph = module.build_graph()
        child = dict(graph.get_subgraphs())['analysis']
        self.assertIsInstance(child, CompiledStateGraph)
        parent_edges = {(e.source, e.target) for e in graph.get_graph().edges}
        self.assertTrue({('prepare', 'refresh'), ('prepare', 'analysis')} <= parent_edges)
        child_edges = {(e.source, e.target) for e in child.get_graph().edges}
        self.assertTrue({('__start__', 'left'), ('__start__', 'right')} <= child_edges)
        for s in [dict(count=10, label='abc', left=91, right=-13),
                  dict(count=-4, label='', left=-7, right=11)]:
            result = child.invoke(s)
            self.assertEqual(result, {'left': s['left'] + s['count'] + len(s['label']),
                                      'right': s['right'] + s['count'] - len(s['label'])})

    def test_concurrent_invocations_and_graph_isolation(self):
        calls_a, calls_b = [], []
        graphs = [module.build_graph(calls=calls_a), module.build_graph(calls=calls_b)]
        states = [dict(count=i - 10, label='z' * i, left=100 + i, right=-200 - i)
                  for i in range(20)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(graphs[i % 2].invoke, s) for i, s in enumerate(states)]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(results, [expected(s) for s in states])
        for calls in (calls_a, calls_b):
            self.assertEqual(Counter(calls), dict(prepare=10, refresh=10, left=10, right=10))

    def test_async_invocation(self):
        async def run():
            graph = module.build_graph()
            states = [dict(count=i, label='async', left=-3, right=12) for i in range(5)]
            results = await asyncio.gather(*(graph.ainvoke(s) for s in states))
            self.assertEqual(results, [expected(s) for s in states])
        asyncio.run(run())

    def test_exceptions_propagate(self):
        class GateError(RuntimeError):
            pass

        class FailingGate:
            def wait(self):
                raise GateError('gate failure')

        s = dict(count=1, label='old', left=2, right=3)
        for gates in [dict(parent_gate=FailingGate()), dict(child_gate=FailingGate())]:
            with self.assertRaisesRegex(GateError, 'gate failure'):
                module.build_graph(**gates).invoke(s)
        with self.assertRaises(TypeError):
            module.build_graph().invoke(dict(count=None, label='old', left=2, right=3))


if __name__ == '__main__':
    unittest.main(verbosity=2)
