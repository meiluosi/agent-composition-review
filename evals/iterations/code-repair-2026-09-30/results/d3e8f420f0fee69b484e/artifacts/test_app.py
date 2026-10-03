import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from itertools import product
from threading import Barrier
import unittest

from app import build_graph
from langgraph.graph.state import CompiledStateGraph


def expected(s):
    return {
        'count': s['count'] + 7,
        'label': s['label'] + '|fresh',
        'left': s['left'] + s['count'] + 7 + len(s['label']),
        'right': s['right'] + s['count'] + 7 - len(s['label']),
    }


class TestGraph(unittest.TestCase):
    def test_finite_inputs_and_repeated_invocations(self):
        calls = []
        graph = build_graph(calls=calls)
        for count, label, left, right in product(
            [-19, 0, 1, 10**30], ['', 'old', '中文🙂', 'x|fresh'],
            [-101, 0, 203], [-307, 0, 409]
        ):
            state = dict(count=count, label=label, left=left, right=right)
            before = deepcopy(state)
            calls.clear()
            with self.subTest(state=state):
                self.assertEqual(graph.invoke(state), expected(state))
                self.assertEqual(state, before)
                self.assertEqual(Counter(calls), Counter(['prepare', 'refresh', 'left', 'right']))

    def test_chained_results(self):
        graph = build_graph()
        state = dict(count=-5, label='seed', left=73, right=-29)
        for _ in range(10):
            want = expected(state)
            state = graph.invoke(state)
            self.assertEqual(state, want)

    def test_both_parallel_boundaries(self):
        # Either serialization makes one of these timed barriers fail.
        parent_gate, child_gate = Barrier(2, timeout=5), Barrier(2, timeout=5)
        calls = []
        graph = build_graph(parent_gate, child_gate, calls)
        for i in range(8):
            state = dict(count=i, label='before', left=32, right=-42)
            self.assertEqual(graph.invoke(state), expected(state))
            self.assertFalse(parent_gate.broken)
            self.assertFalse(child_gate.broken)
        self.assertEqual(Counter(calls), Counter(dict.fromkeys(['prepare', 'refresh', 'left', 'right'], 8)))

    def test_real_graph_topology_and_child_reuse(self):
        calls = []
        graph = build_graph(calls=calls)
        child = graph.nodes['analysis'].bound
        self.assertIsInstance(graph, CompiledStateGraph)
        self.assertIsInstance(child, CompiledStateGraph)
        self.assertEqual({(e.source, e.target) for e in graph.get_graph().edges}, {
            ('__start__', 'prepare'), ('prepare', 'refresh'), ('prepare', 'analysis'),
            ('refresh', '__end__'), ('analysis', '__end__'),
        })
        self.assertEqual({(e.source, e.target) for e in child.get_graph().edges}, {
            ('__start__', 'left'), ('__start__', 'right'),
            ('left', '__end__'), ('right', '__end__'),
        })
        for count in (8, -30, 500):
            state = dict(count=count, label='snapshot', left=79, right=-17)
            self.assertEqual(child.invoke(state), {
                'left': 79 + count + len('snapshot'),
                'right': -17 + count - len('snapshot'),
            })
        self.assertEqual(Counter(calls), Counter(left=3, right=3))

    def test_concurrent_invocations_and_graph_isolation(self):
        states = [dict(count=i, label='x' * i, left=13 * i, right=-19 * i) for i in range(24)]
        logs = [[], []]
        graphs = [build_graph(calls=log) for log in logs]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(graphs[i % 2].invoke, state) for i, state in enumerate(states)]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(results, list(map(expected, states)))
        for log in logs:
            self.assertEqual(Counter(log), Counter(dict.fromkeys(['prepare', 'refresh', 'left', 'right'], 12)))

    def test_async_calls(self):
        async def run():
            graph = build_graph()
            states = [dict(count=i, label='异步', left=-12, right=29) for i in range(8)]
            results = await asyncio.gather(*(graph.ainvoke(s) for s in states))
            self.assertEqual(results, list(map(expected, states)))
        asyncio.run(run())

    def test_updates_do_not_export_stale_fields(self):
        state = dict(count=2, label='snapshot', left=44, right=88)
        updates = list(build_graph().stream(state, stream_mode='updates', subgraphs=True))
        parent = {name: patch for namespace, update in updates if not namespace for name, patch in update.items()}
        child = {name: patch for namespace, update in updates if namespace for name, patch in update.items()}
        self.assertEqual(parent, {
            'prepare': {'count': 9}, 'refresh': {'label': 'snapshot|fresh'},
            'analysis': {'left': 61, 'right': 89},
        })
        self.assertEqual(child, {'left': {'left': 61}, 'right': {'right': 89}})

    def test_gate_errors_propagate(self):
        class GateError(RuntimeError):
            pass
        class FailingGate:
            def wait(self):
                raise GateError('gate failed')
        state = dict(count=1, label='x', left=2, right=3)
        for kwargs in ({'parent_gate': FailingGate()}, {'child_gate': FailingGate()}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(GateError, 'gate failed'):
                build_graph(**kwargs).invoke(state)

    def test_invalid_inputs_raise(self):
        graph = build_graph()
        for state, error in [
            ({'count': 'bad', 'label': 'x', 'left': 0, 'right': 0}, TypeError),
            ({'count': 0, 'label': None, 'left': 0, 'right': 0}, TypeError),
            ({'count': 0, 'label': 'x', 'right': 0}, KeyError),
        ]:
            with self.subTest(state=state), self.assertRaises(error):
                graph.invoke(state)
        state = dict(count=1, label='recovered', left=23, right=41)
        self.assertEqual(graph.invoke(state), expected(state))


if __name__ == '__main__':
    unittest.main()
