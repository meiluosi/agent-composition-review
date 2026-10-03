import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier
import unittest

from app import build_graph
from langgraph.graph.state import CompiledStateGraph


def expected(state):
    count = state['count'] + 7
    width = len(state['label'])
    return dict(count=count, label=state['label'] + '|fresh',
                left=state['left'] + count + width,
                right=state['right'] + count - width)


class ReviewTests(unittest.TestCase):
    def assert_calls(self, calls, repetitions=1):
        self.assertEqual(Counter(calls), Counter({
            name: repetitions for name in ('prepare', 'refresh', 'left', 'right')
        }))

    def test_inputs_and_repeated_invocations(self):
        calls = []
        graph = build_graph(calls=calls)
        cases = [
            dict(count=1, label='old', left=2, right=3),
            dict(count=-23, label='', left=107, right=-51),
            dict(count=0, label='汉字🙂e\u0301|fresh', left=-8, right=901),
            dict(count=10**30, label='z' * 4096, left=10**31, right=-10**32),
        ]
        for i, state in enumerate(cases * 3, 1):
            before = state.copy()
            self.assertEqual(graph.invoke(state), expected(before))
            self.assertEqual(state, before)
            self.assert_calls(calls, i)
        state = cases[0].copy()
        for _ in range(4):
            result = graph.invoke(state)
            self.assertEqual(result, expected(state))
            state = result
        self.assert_calls(calls, 16)

    def test_both_parallel_boundaries(self):
        calls = []
        parent = Barrier(2, timeout=5)
        child = Barrier(2, timeout=5)
        graph = build_graph(parent_gate=parent, child_gate=child, calls=calls)
        for i in range(5):
            state = dict(count=i - 9, label='before' * (i + 1), left=300, right=-80)
            self.assertEqual(graph.invoke(state), expected(state))
            self.assert_calls(calls, i + 1)
        self.assertFalse(parent.broken)
        self.assertFalse(child.broken)

    def test_reusable_real_subgraph_and_topology(self):
        graph = build_graph()
        self.assertIsInstance(graph, CompiledStateGraph)
        self.assertEqual({(e.source, e.target) for e in graph.get_graph().edges}, {
            ('__start__', 'prepare'), ('prepare', 'refresh'),
            ('prepare', 'analysis'), ('refresh', '__end__'), ('analysis', '__end__'),
        })
        subgraphs = dict(graph.get_subgraphs())
        self.assertEqual(set(subgraphs), {'analysis'})
        child = subgraphs['analysis']
        self.assertIsInstance(child, CompiledStateGraph)
        self.assertEqual({(e.source, e.target) for e in child.get_graph().edges}, {
            ('__start__', 'left'), ('__start__', 'right'),
            ('left', '__end__'), ('right', '__end__'),
        })
        for count in (8, -11, 42):
            state = dict(count=count, label='sub', left=19, right=-29)
            self.assertEqual(child.invoke(state), {
                'left': 19 + count + 3, 'right': -29 + count - 3,
            })

    def test_multiple_graphs_and_simultaneous_calls(self):
        logs = [[], []]
        graphs = [build_graph(calls=log) for log in logs]
        states = [dict(count=i, label=str(i) * 3, left=i * 10, right=-i * 20)
                  for i in range(24)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(graphs[i % 2].invoke, state)
                       for i, state in enumerate(states)]
            for state, future in zip(states, futures):
                self.assertEqual(future.result(timeout=15), expected(state))
        for log in logs:
            self.assert_calls(log, 12)

    def test_async_invocations(self):
        calls = []
        graph = build_graph(calls=calls)
        states = [dict(count=i, label='async', left=30, right=70) for i in range(4)]

        async def run():
            return await asyncio.gather(*(graph.ainvoke(state) for state in states))

        self.assertEqual(asyncio.run(run()), [expected(state) for state in states])
        self.assert_calls(calls, 4)

    def test_gate_exceptions_propagate(self):
        class SentinelError(Exception):
            pass

        class FailingGate:
            def wait(self):
                raise SentinelError('gate failure')

        for parameter in ('parent_gate', 'child_gate'):
            with self.subTest(parameter=parameter):
                graph = build_graph(**{parameter: FailingGate()})
                with self.assertRaisesRegex(SentinelError, 'gate failure'):
                    graph.invoke(dict(count=1, label='old', left=2, right=3))

    def test_action_exceptions_propagate(self):
        with self.assertRaises(TypeError):
            build_graph().invoke(dict(count='invalid', label='old', left=2, right=3))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReviewTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    Path('trace.json').write_text(json.dumps({
        'command': './run_python test_review.py',
        'tests_run': result.testsRun,
        'successful': result.wasSuccessful(),
        'failures': [str(test) for test, _ in result.failures],
        'errors': [str(test) for test, _ in result.errors],
        'note': 'Calls are counted without assuming ordering among parallel actions.',
    }, indent=2) + '\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
