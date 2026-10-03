import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from threading import Barrier
import unittest

from langgraph.graph.state import CompiledStateGraph
from app import build_graph


OBSERVATIONS = []
NAMES = Counter(['prepare', 'refresh', 'left', 'right'])


def expected(s):
    count = s['count'] + 7
    return dict(count=count, label=s['label'] + '|fresh',
                left=s['left'] + count + len(s['label']),
                right=s['right'] + count - len(s['label']))


class GateFailure(RuntimeError):
    pass


class FailingGate:
    def wait(self):
        raise GateFailure('injected gate failure')


class ReviewTests(unittest.TestCase):
    def verify(self, graph, state, calls=None, category='invoke'):
        before = deepcopy(state)
        start = len(calls) if calls is not None else 0
        result = graph.invoke(state)
        self.assertEqual(result, expected(before))
        self.assertEqual(state, before)
        observed = calls[start:] if calls is not None else None
        if observed is not None:
            self.assertEqual(Counter(observed), NAMES)
            self.assertEqual(observed[0], 'prepare')
        OBSERVATIONS.append(dict(category=category, input=before,
                                 output=result, calls=observed))
        return result

    def test_values_and_repeated_calls(self):
        calls = []
        graph = build_graph(calls=calls)
        self.assertIsInstance(graph, CompiledStateGraph)
        for count in (-19, 0, 1, 10**30):
            for label in ('', 'old', '汉字🙂', 'a|fresh', 'x' * 1000):
                for left, right in ((0, 0), (51, -73)):
                    self.verify(graph, dict(count=count, label=label,
                                            left=left, right=right), calls)

    def test_chained_calls_preserve_accumulated_values(self):
        calls = []
        graph = build_graph(calls=calls)
        state = dict(count=-7, label='seed', left=19, right=-23)
        for _ in range(5):
            state = self.verify(graph, state, calls, 'chained')

    def test_both_parallel_levels(self):
        calls = []
        parent_gate, child_gate = Barrier(2, timeout=5), Barrier(2, timeout=5)
        graph = build_graph(parent_gate, child_gate, calls)
        for i in range(4):
            self.verify(graph, dict(count=i, label='snapshot', left=71,
                                    right=-21), calls, 'two_barriers')
        self.assertFalse(parent_gate.broken)
        self.assertFalse(child_gate.broken)

    def test_graph_instances_are_independent(self):
        logs = [[], []]
        graphs = [build_graph(calls=log) for log in logs]
        for i in range(6):
            which = i % 2
            other_before = list(logs[1 - which])
            self.verify(graphs[which], dict(count=i, label=str(which),
                        left=i * 17, right=-i), logs[which], 'separate_graphs')
            self.assertEqual(logs[1 - which], other_before)

    def test_concurrent_invocations_same_graph(self):
        calls = []
        graph = build_graph(calls=calls)
        inputs = [dict(count=i, label='q' * i, left=100+i, right=-100-i)
                  for i in range(16)]
        before = deepcopy(inputs)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(graph.invoke, inputs))
        self.assertEqual(results, [expected(s) for s in before])
        self.assertEqual(inputs, before)
        self.assertEqual(Counter(calls), Counter({k: 16 for k in NAMES}))
        OBSERVATIONS.append(dict(category='concurrent_same_graph',
                                 inputs=inputs, outputs=results, calls=calls))

    def test_reusable_child_and_topology(self):
        calls = []
        graph = build_graph(calls=calls)
        subgraphs = dict(graph.get_subgraphs())
        self.assertEqual(set(subgraphs), {'analysis'})
        child = subgraphs['analysis']
        self.assertIsInstance(child, CompiledStateGraph)
        parent_edges = {(e.source, e.target) for e in graph.get_graph().edges}
        self.assertTrue({('prepare', 'refresh'), ('prepare', 'analysis'),
                         ('refresh', '__end__'), ('analysis', '__end__')}
                        <= parent_edges)
        child_edges = {(e.source, e.target) for e in child.get_graph().edges}
        self.assertTrue({('__start__', 'left'), ('__start__', 'right')}
                        <= child_edges)
        for i in range(3):
            state = dict(count=i, label='child', left=73, right=-91)
            result = child.invoke(state)
            self.assertEqual(result, dict(left=73+i+5, right=-91+i-5))
            self.assertEqual(Counter(calls[-2:]), Counter(['left', 'right']))
            OBSERVATIONS.append(dict(category='direct_child', input=state,
                                     output=result, calls=calls[-2:]))
        self.verify(graph, dict(count=4, label='parent', left=9, right=3), calls)

    def test_gate_errors_propagate(self):
        for parameter in ('parent_gate', 'child_gate'):
            with self.subTest(parameter=parameter):
                graph = build_graph(**{parameter: FailingGate()})
                with self.assertRaisesRegex(GateFailure, 'injected gate failure'):
                    graph.invoke(dict(count=1, label='x', left=2, right=3))
                OBSERVATIONS.append(dict(category='exception', site=parameter,
                                         observed_exception='GateFailure'))

    def test_invalid_input_error_propagates_and_next_call_succeeds(self):
        calls = []
        graph = build_graph(calls=calls)
        with self.assertRaises(TypeError):
            graph.invoke(dict(count='bad', label='x', left=2, right=3))
        self.assertEqual(calls, ['prepare'])
        self.verify(graph, dict(count=1, label='x', left=2, right=3), calls)
        OBSERVATIONS.append(dict(category='exception', site='prepare',
                                 observed_exception='TypeError'))

    def test_async_invocation(self):
        calls = []
        graph = build_graph(Barrier(2, timeout=5), Barrier(2, timeout=5), calls)
        state = dict(count=11, label='async', left=31, right=42)
        result = asyncio.run(graph.ainvoke(state))
        self.assertEqual(result, expected(state))
        self.assertEqual(Counter(calls), NAMES)
        OBSERVATIONS.append(dict(category='async_two_barriers', input=state,
                                 output=result, calls=calls))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReviewTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    Path('trace.json').write_text(json.dumps({
        'kind': 'behavior_test_observations',
        'description': 'Actual invocation results and call logs; not a sequential action-state replay.',
        'tests_run': result.testsRun,
        'successful': result.wasSuccessful(),
        'observations': OBSERVATIONS,
    }, ensure_ascii=False, indent=2) + '\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
