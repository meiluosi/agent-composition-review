import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
import unittest

from langgraph.graph.state import CompiledStateGraph
from app import build_graph


ACTIONS = Counter({'prepare': 1, 'refresh': 1, 'left': 1, 'right': 1})


def expected(s):
    count = s['count'] + 7
    return dict(count=count, label=s['label'] + '|fresh',
                left=s['left'] + count + len(s['label']),
                right=s['right'] + count - len(s['label']))


def sample(i):
    return dict(count=i - 20, label=['', 'old', '中文🙂', 'x|fresh'][i % 4],
                left=1000 + i, right=-300 - i)


class GraphReview(unittest.TestCase):
    def test_values_and_input_preservation(self):
        calls = []
        graph = build_graph(calls=calls)
        inputs = [sample(i) for i in range(40)]
        inputs.append(dict(count=10**40, label='a' * 1000, left=-10**50, right=10**60))
        for s in inputs:
            before = deepcopy(s)
            calls.clear()
            self.assertEqual(graph.invoke(s), expected(s))
            self.assertEqual(s, before)
            self.assertEqual(Counter(calls), ACTIONS)

    def test_chained_invocations(self):
        graph = build_graph()
        actual = sample(1)
        oracle = deepcopy(actual)
        for _ in range(12):
            actual = graph.invoke(actual)
            oracle = expected(oracle)
            self.assertEqual(actual, oracle)

    def test_both_parallel_boundaries(self):
        calls = []
        # Serial execution at either fork breaks its timed barrier.
        parent_gate, child_gate = Barrier(2, timeout=5), Barrier(2, timeout=5)
        graph = build_graph(parent_gate, child_gate, calls)
        for i in range(6):
            calls.clear()
            self.assertEqual(graph.invoke(sample(i)), expected(sample(i)))
            self.assertEqual(Counter(calls), ACTIONS)
            self.assertEqual(calls[0], 'prepare')
        self.assertFalse(parent_gate.broken)
        self.assertFalse(child_gate.broken)

    def test_concurrent_same_graph(self):
        calls = []
        graph = build_graph(calls=calls)
        inputs = [sample(i) for i in range(24)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            outputs = list(pool.map(graph.invoke, inputs))
        self.assertEqual(outputs, [expected(s) for s in inputs])
        self.assertEqual(Counter(calls), Counter({k: 24 for k in ACTIONS}))

    def test_independent_graphs(self):
        logs = [[] for _ in range(6)]
        graphs = [build_graph(Barrier(2, timeout=5), Barrier(2, timeout=5), log)
                  for log in logs]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(g.invoke, sample(i)) for i, g in enumerate(graphs)]
            for i, future in enumerate(futures):
                self.assertEqual(future.result(timeout=15), expected(sample(i)))
        for log in logs:
            self.assertEqual(Counter(log), ACTIONS)

    def test_real_reusable_child_and_topology(self):
        graph = build_graph()
        self.assertIsInstance(graph, CompiledStateGraph)
        children = dict(graph.get_subgraphs())
        self.assertEqual(set(children), {'analysis'})
        child = children['analysis']
        self.assertIsInstance(child, CompiledStateGraph)
        parent_edges = {(e.source, e.target) for e in graph.get_graph().edges}
        self.assertTrue({('prepare', 'refresh'), ('prepare', 'analysis')} <= parent_edges)
        child_edges = {(e.source, e.target) for e in child.get_graph().edges}
        self.assertTrue({('__start__', 'left'), ('__start__', 'right')} <= child_edges)
        for i in range(5):
            s = sample(i)
            self.assertEqual(child.invoke(s), {
                'left': s['left'] + s['count'] + len(s['label']),
                'right': s['right'] + s['count'] - len(s['label'])})

    def test_parent_update_ownership(self):
        s = sample(3)
        updates = list(build_graph().stream(s, stream_mode='updates'))
        by_node = {name: patch for update in updates for name, patch in update.items()}
        self.assertEqual(len(updates), 3)
        self.assertEqual(by_node, {
            'prepare': {'count': s['count'] + 7},
            'refresh': {'label': s['label'] + '|fresh'},
            'analysis': {k: expected(s)[k] for k in ('left', 'right')}})

    def test_exceptions_propagate(self):
        class GateFailure(Exception):
            pass

        failure = GateFailure('gate failure')

        class FailingGate:
            def wait(self):
                raise failure

        for kwargs in ({'parent_gate': FailingGate()}, {'child_gate': FailingGate()}):
            with self.assertRaises(GateFailure) as caught:
                build_graph(**kwargs).invoke(sample(0))
            self.assertIs(caught.exception, failure)
        with self.assertRaises(KeyError):
            build_graph().invoke({'count': 1, 'label': '', 'left': 0})
        with self.assertRaises(TypeError):
            build_graph().invoke(dict(count=None, label='', left=0, right=0))

    def test_async_invocations(self):
        async def run():
            calls = []
            graph = build_graph(calls=calls)
            inputs = [sample(i) for i in range(8)]
            outputs = await asyncio.gather(*(graph.ainvoke(s) for s in inputs))
            self.assertEqual(outputs, [expected(s) for s in inputs])
            self.assertEqual(Counter(calls), Counter({k: 8 for k in ACTIONS}))
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main(verbosity=2)
