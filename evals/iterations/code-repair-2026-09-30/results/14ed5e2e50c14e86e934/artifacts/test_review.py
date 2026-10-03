import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import product
import json
from pathlib import Path
from threading import Barrier
import unittest

from langgraph.errors import InvalidUpdateError
from langgraph.graph.state import CompiledStateGraph
from app import build_graph
from original import build_graph as original_graph


OBSERVATIONS = []
NAMES = Counter(['prepare', 'refresh', 'left', 'right'])


def expected(s):
    count = s['count'] + 7
    return dict(count=count, label=s['label'] + '|fresh',
                left=s['left'] + count + len(s['label']),
                right=s['right'] + count - len(s['label']))


class Review(unittest.TestCase):
    def verify(self, graph, state, calls=None):
        before = dict(state)
        start = len(calls) if calls is not None else 0
        result = graph.invoke(state)
        self.assertEqual(result, expected(before))
        self.assertEqual(state, before)
        observed = calls[start:] if calls is not None else None
        if observed is not None:
            self.assertEqual(Counter(observed), NAMES)
        OBSERVATIONS.append(dict(input=before, output=result, calls=observed))
        return result

    def test_original_conflict(self):
        calls = []
        with self.assertRaises(InvalidUpdateError) as caught:
            original_graph(calls=calls).invoke(dict(count=1, label='old', left=2, right=3))
        self.assertIn("'label'", str(caught.exception))
        self.assertEqual(Counter(calls), NAMES)
        OBSERVATIONS.append(dict(original_error=str(caught.exception), calls=calls))

    def test_values_and_repeated_invocation(self):
        calls = []
        graph = build_graph(calls=calls)
        for count, label, left, right in product(
                [-20, 0, 9], ['', 'old|fresh', '汉字🙂'], [-100, 0, 13], [-31, 0, 200]):
            self.verify(graph, dict(count=count, label=label, left=left, right=right), calls)
        state = dict(count=1, label='old', left=2, right=3)
        for _ in range(3):
            state = self.verify(graph, state, calls)

    def test_both_parallel_levels(self):
        parent_gate, child_gate = Barrier(2, timeout=5), Barrier(2, timeout=5)
        calls = []
        graph = build_graph(parent_gate, child_gate, calls)
        for _ in range(3):
            self.verify(graph, dict(count=-4, label='parallel', left=19, right=-20), calls)
        self.assertFalse(parent_gate.broken)
        self.assertFalse(child_gate.broken)

    def test_real_graph_topology_and_child_reuse(self):
        calls = []
        graph = build_graph(calls=calls)
        self.assertIsInstance(graph, CompiledStateGraph)
        edges = {(e.source, e.target) for e in graph.get_graph().edges}
        self.assertTrue({('__start__', 'prepare'), ('prepare', 'refresh'),
                         ('prepare', 'analysis'), ('refresh', '__end__'),
                         ('analysis', '__end__')} <= edges)
        child = dict(graph.get_subgraphs())['analysis']
        self.assertIsInstance(child, CompiledStateGraph)
        edges = {(e.source, e.target) for e in child.get_graph().edges}
        self.assertTrue({('__start__', 'left'), ('__start__', 'right')} <= edges)
        for count in [4, -20, 0]:
            calls.clear()
            s = dict(count=count, label='snapshot', left=11, right=-31)
            out = child.invoke(s)
            self.assertEqual(out, dict(left=11 + count + 8, right=-31 + count - 8))
            self.assertEqual(Counter(calls), Counter(['left', 'right']))
            OBSERVATIONS.append(dict(child_input=s, child_output=out, calls=list(calls)))

    def test_boundary_patches(self):
        state = dict(count=1, label='old', left=2, right=3)
        updates = list(build_graph().stream(state, stream_mode='updates'))
        by_node = {k: v for update in updates for k, v in update.items()}
        self.assertEqual(by_node, dict(prepare={'count': 8}, refresh={'label': 'old|fresh'},
                                      analysis={'left': 13, 'right': 8}))
        OBSERVATIONS.append(dict(parent_updates=updates))

    def test_concurrent_invocations_and_graph_isolation(self):
        calls_a, calls_b = [], []
        graphs = [build_graph(calls=calls_a), build_graph(calls=calls_b)]
        states = [dict(count=i, label=str(i), left=100+i, right=-100-i) for i in range(20)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(graphs[i % 2].invoke, s) for i, s in enumerate(states)]
            outputs = [f.result(timeout=10) for f in futures]
        self.assertEqual(outputs, [expected(s) for s in states])
        self.assertEqual(Counter(calls_a), Counter({k: 10 for k in NAMES}))
        self.assertEqual(Counter(calls_b), Counter({k: 10 for k in NAMES}))
        OBSERVATIONS.append(dict(concurrent_inputs=states, concurrent_outputs=outputs,
                                 calls_a=calls_a, calls_b=calls_b))

    def test_exceptions_propagate(self):
        class GateFailure(RuntimeError):
            pass

        class FailingGate:
            def wait(self):
                raise GateFailure('gate failed')

        state = dict(count=1, label='old', left=2, right=3)
        for keyword in ['parent_gate', 'child_gate']:
            with self.assertRaisesRegex(GateFailure, 'gate failed'):
                build_graph(**{keyword: FailingGate()}).invoke(state)
        with self.assertRaises(TypeError):
            build_graph().invoke({**state, 'count': None})
        with self.assertRaises(TypeError):
            build_graph().invoke({**state, 'label': None})
        OBSERVATIONS.append(dict(propagated_exceptions=['parent gate', 'child gate',
                                                       'invalid count', 'invalid label']))

    def test_async(self):
        calls = []
        state = dict(count=3, label='async', left=-4, right=17)
        result = asyncio.run(build_graph(calls=calls).ainvoke(state))
        self.assertEqual(result, expected(state))
        self.assertEqual(Counter(calls), NAMES)
        OBSERVATIONS.append(dict(async_input=state, async_output=result, calls=calls))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Review)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    Path('trace.json').write_text(json.dumps({
        'format': 'behavioral-observations',
        'note': 'Actual inputs, outputs and calls; list order is not a concurrent happens-before proof.',
        'tests_run': result.testsRun,
        'successful': result.wasSuccessful(),
        'observations': OBSERVATIONS,
    }, ensure_ascii=False, indent=2) + '\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
