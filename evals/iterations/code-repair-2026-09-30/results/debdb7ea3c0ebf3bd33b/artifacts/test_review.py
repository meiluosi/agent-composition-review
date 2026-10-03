import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
import unittest

from langgraph.graph.state import CompiledStateGraph
from app import build_graph


ACTIONS = Counter({'prepare': 1, 'refresh': 1, 'left': 1, 'right': 1})


def expected(state):
    count = state['count'] + 7
    return {
        'count': count,
        'label': state['label'] + '|fresh',
        'left': state['left'] + count + len(state['label']),
        'right': state['right'] + count - len(state['label']),
    }


class ReviewTests(unittest.TestCase):
    def test_boundary_values_and_repeated_calls(self):
        calls = []
        graph = build_graph(calls=calls)
        states = [
            {'count': c, 'label': label, 'left': -31, 'right': 47}
            for c in (-100, -7, 0, 1, 10**30)
            for label in ('', 'old', '中文🐍', 'x|fresh')
        ]
        states += [states[0].copy(), states[1].copy()]
        for state in states:
            with self.subTest(state=state):
                before = deepcopy(state)
                start = len(calls)
                self.assertEqual(graph.invoke(state), expected(state))
                self.assertEqual(state, before)
                self.assertEqual(Counter(calls[start:]), ACTIONS)
                self.assertEqual(calls[start], 'prepare')
        # Feeding the previous output back in must retain accumulated fields.
        state = states[2]
        for _ in range(3):
            start = len(calls)
            result = graph.invoke(state)
            self.assertEqual(result, expected(state))
            self.assertEqual(Counter(calls[start:]), ACTIONS)
            state = result

    def test_both_parallel_levels_with_reusable_barriers(self):
        calls = []
        parent_gate, child_gate = Barrier(2, timeout=5), Barrier(2, timeout=5)
        graph = build_graph(parent_gate, child_gate, calls)
        state = {'count': 5, 'label': 'before', 'left': 101, 'right': -201}
        for _ in range(3):
            start = len(calls)
            self.assertEqual(graph.invoke(state), expected(state))
            self.assertEqual(Counter(calls[start:]), ACTIONS)
            self.assertFalse(parent_gate.broken)
            self.assertFalse(child_gate.broken)

    def test_real_compiled_graph_and_reusable_child(self):
        calls = []
        graph = build_graph(calls=calls)
        self.assertIsInstance(graph, CompiledStateGraph)
        children = dict(graph.get_subgraphs())
        self.assertEqual(set(children), {'analysis'})
        child = children['analysis']
        self.assertIsInstance(child, CompiledStateGraph)
        parent_edges = {(e.source, e.target) for e in graph.get_graph().edges}
        self.assertTrue({('prepare', 'refresh'), ('prepare', 'analysis')} <= parent_edges)
        child_edges = {(e.source, e.target) for e in child.get_graph().edges}
        self.assertTrue({('__start__', 'left'), ('__start__', 'right')} <= child_edges)
        for count in (7, -20, 7):
            state = {'count': count, 'label': 'raw', 'left': 90, 'right': -60}
            start = len(calls)
            self.assertEqual(child.invoke(state), {'left': 90 + count + 3, 'right': -60 + count - 3})
            self.assertEqual(Counter(calls[start:]), Counter({'left': 1, 'right': 1}))

    def test_concurrent_invocations_of_same_graph(self):
        calls = []
        graph = build_graph(calls=calls)
        states = [{'count': i, 'label': str(i) * i, 'left': i * 100, 'right': -i * 51}
                  for i in range(16)]
        before = deepcopy(states)
        with ThreadPoolExecutor(max_workers=4) as pool:
            outputs = list(pool.map(graph.invoke, states))
        self.assertEqual(outputs, [expected(s) for s in states])
        self.assertEqual(states, before)
        self.assertEqual(Counter(calls), Counter({name: 16 for name in ACTIONS}))

    def test_independent_graphs_with_distinct_gates_and_logs(self):
        logs = [[], []]
        graphs = [build_graph(Barrier(2, timeout=5), Barrier(2, timeout=5), log)
                  for log in logs]
        states = [{'count': 19, 'label': 'first', 'left': 99, 'right': -75},
                  {'count': -88, 'label': 'second', 'left': -91, 'right': 333}]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(graph.invoke, state) for graph, state in zip(graphs, states)]
            outputs = [f.result(timeout=15) for f in futures]
        self.assertEqual(outputs, [expected(s) for s in states])
        for log in logs:
            self.assertEqual(Counter(log), ACTIONS)

    def test_exceptions_propagate(self):
        class GateError(RuntimeError):
            pass

        class FailingGate:
            def wait(self):
                raise GateError('gate failure')

        state = {'count': 1, 'label': 'old', 'left': 2, 'right': 3}
        for kwargs in ({'parent_gate': FailingGate()}, {'child_gate': FailingGate()}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(GateError, 'gate failure'):
                    build_graph(**kwargs).invoke(state)
        with self.assertRaises(TypeError):
            build_graph().invoke(dict(state, count='invalid'))

    def test_async_invocation(self):
        calls = []
        graph = build_graph(Barrier(2, timeout=5), Barrier(2, timeout=5), calls)
        state = {'count': -4, 'label': '异步', 'left': 777, 'right': -999}
        self.assertEqual(asyncio.run(graph.ainvoke(state)), expected(state))
        self.assertEqual(Counter(calls), ACTIONS)


if __name__ == '__main__':
    unittest.main(verbosity=2)
