import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest

from langgraph.graph.state import CompiledStateGraph

from app import build_graph


def expected(state):
    count = state['count'] + 7
    return dict(count=count, label=state['label'] + '|fresh',
                left=state['left'] + count + len(state['label']),
                right=state['right'] + count - len(state['label']))


class GraphTests(unittest.TestCase):
    def test_values_and_repeated_invocations(self):
        calls = []
        graph = build_graph(calls=calls)
        cases = [dict(count=1, label='old', left=2, right=3),
                 dict(count=-19, label='', left=100, right=-80),
                 dict(count=0, label='中文🙂|fresh', left=-5, right=9),
                 dict(count=10**50, label='x' * 1000,
                      left=-(10**60), right=10**70)]
        for state in cases:
            before = dict(state)
            calls.clear()
            result = graph.invoke(state)
            self.assertEqual(result, expected(state))
            self.assertEqual(state, before)
            self.assertEqual(Counter(calls), Counter(
                ['prepare', 'refresh', 'left', 'right']))
            self.assertEqual(calls[0], 'prepare')
            calls.clear()
            self.assertEqual(graph.invoke(result), expected(result))
            self.assertEqual(Counter(calls), Counter(
                ['prepare', 'refresh', 'left', 'right']))

    def test_both_levels_parallel(self):
        calls = []
        graph = build_graph(parent_gate=Barrier(2, timeout=5),
                            child_gate=Barrier(2, timeout=5), calls=calls)
        state = dict(count=2, label='snapshot', left=21, right=-7)
        for _ in range(3):
            calls.clear()
            self.assertEqual(graph.invoke(state), expected(state))
            self.assertEqual(Counter(calls), Counter(
                ['prepare', 'refresh', 'left', 'right']))

    def test_real_graph_and_reusable_child(self):
        graph = build_graph()
        self.assertIsInstance(graph, CompiledStateGraph)
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        self.assertTrue({('prepare', 'refresh'), ('prepare', 'analysis')}
                        .issubset(edges))
        children = dict(graph.get_subgraphs())
        self.assertEqual(set(children), {'analysis'})
        child = children['analysis']
        self.assertIsInstance(child, CompiledStateGraph)
        edges = {(edge.source, edge.target) for edge in child.get_graph().edges}
        self.assertTrue({('__start__', 'left'), ('__start__', 'right')}
                        .issubset(edges))
        for count in (1, -200, 700):
            state = dict(count=count, label='child', left=50, right=-2)
            self.assertEqual(child.invoke(state),
                             dict(left=50 + count + 5, right=-2 + count - 5))

    def test_concurrent_invocations_and_separate_graphs(self):
        logs = [[], []]
        graphs = [build_graph(calls=log) for log in logs]
        states = [dict(count=i - 20, label=str(i) * (i % 4),
                       left=i * 3, right=-i * 5) for i in range(40)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(graphs[i % 2].invoke, state)
                       for i, state in enumerate(states)]
            self.assertEqual([f.result(timeout=15) for f in futures],
                             [expected(state) for state in states])
        for log in logs:
            self.assertEqual(Counter(log), Counter(
                dict(prepare=20, refresh=20, left=20, right=20)))

    def test_async_invocations(self):
        graph = build_graph()
        states = [dict(count=i, label='async', left=-i, right=2 * i)
                  for i in range(8)]

        async def run():
            return await asyncio.gather(*(graph.ainvoke(s) for s in states))

        self.assertEqual(asyncio.run(run()), [expected(s) for s in states])

    def test_exceptions_propagate(self):
        class GateFailure(RuntimeError):
            pass

        class FailingGate:
            def wait(self):
                raise GateFailure('gate failed')

        state = dict(count=1, label='old', left=2, right=3)
        for keyword in ('parent_gate', 'child_gate'):
            with self.subTest(keyword=keyword):
                with self.assertRaisesRegex(GateFailure, 'gate failed'):
                    build_graph(**{keyword: FailingGate()}).invoke(state)
        with self.assertRaises(TypeError):
            build_graph().invoke(dict(state, count='invalid'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
