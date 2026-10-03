import copy
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from langgraph.graph.state import CompiledStateGraph

from app import build_graph


NAMES = Counter({'prepare': 1, 'refresh': 1, 'left': 1, 'right': 1})
CASES = [
    {'count': 1, 'label': 'old', 'left': 2, 'right': 3},
    {'count': 0, 'label': '', 'left': 0, 'right': 0},
    {'count': -20, 'label': '汉字🙂', 'left': -31, 'right': 97},
    {'count': 10**30, 'label': 'x' * 1000, 'left': 17, 'right': -53},
    {'count': -7, 'label': 'already|fresh', 'left': 80, 'right': 99},
]


def expected(s):
    n = s['count'] + 7
    return {'count': n, 'label': s['label'] + '|fresh',
            'left': s['left'] + n + len(s['label']),
            'right': s['right'] + n - len(s['label'])}


class Review(unittest.TestCase):
    def test_values_reuse_and_input_unchanged(self):
        calls = []
        graph = build_graph(calls=calls)
        self.assertIsInstance(graph, CompiledStateGraph)
        for _ in range(3):
            for s in CASES:
                before = copy.deepcopy(s)
                start = len(calls)
                self.assertEqual(graph.invoke(s), expected(s))
                self.assertEqual(s, before)
                self.assertEqual(Counter(calls[start:]), NAMES)
                self.assertEqual(calls[start], 'prepare')

    def test_chained_invocations(self):
        calls = []
        graph = build_graph(calls=calls)
        s = dict(CASES[0])
        for _ in range(5):
            want = expected(s)
            s = graph.invoke(s)
            self.assertEqual(s, want)
        self.assertEqual(Counter(calls), Counter({k: 5 for k in NAMES}))

    def test_both_parallel_regions_with_reused_barriers(self):
        # A serialized implementation cannot cross either two-party barrier.
        parent, child = Barrier(2, timeout=5), Barrier(2, timeout=5)
        calls = []
        graph = build_graph(parent_gate=parent, child_gate=child, calls=calls)
        for s in CASES:
            start = len(calls)
            self.assertEqual(graph.invoke(s), expected(s))
            self.assertEqual(Counter(calls[start:]), NAMES)
            self.assertFalse(parent.broken)
            self.assertFalse(child.broken)

    def test_reusable_real_child_and_narrow_output(self):
        calls = []
        graph = build_graph(calls=calls)
        subgraphs = dict(graph.get_subgraphs())
        self.assertEqual(set(subgraphs), {'analysis'})
        child = subgraphs['analysis']
        self.assertIsInstance(child, CompiledStateGraph)
        for s in CASES:
            start = len(calls)
            # Standalone child receives an already-prepared snapshot as input.
            prepared = dict(s, count=s['count'] + 7)
            result = child.invoke(prepared)
            want = expected(s)
            self.assertEqual(result, {k: want[k] for k in ('left', 'right')})
            self.assertEqual(Counter(calls[start:]), Counter(left=1, right=1))

    def test_concurrent_invocations_of_same_graph(self):
        calls = []
        graph = build_graph(calls=calls)
        inputs = [dict(count=i, label=str(i), left=i * 11, right=-i * 3)
                  for i in range(20)]
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(graph.invoke, inputs))
        self.assertEqual(results, list(map(expected, inputs)))
        self.assertEqual(Counter(calls), Counter({k: 20 for k in NAMES}))

    def test_multiple_graphs_have_separate_state_and_calls(self):
        logs = [[], []]
        graphs = [build_graph(Barrier(2, timeout=5), Barrier(2, timeout=5), log)
                  for log in logs]
        for _ in range(3):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(g.invoke, s)
                           for g, s in zip(graphs, CASES)]
                self.assertEqual([f.result() for f in futures],
                                 [expected(s) for s in CASES[:2]])
        for log in logs:
            self.assertEqual(Counter(log), Counter({k: 3 for k in NAMES}))

    def test_gate_exceptions_propagate(self):
        for parameter in ('parent_gate', 'child_gate'):
            with self.subTest(parameter=parameter):
                error = RuntimeError(parameter + ' failure')

                class RaisingGate:
                    def wait(self):
                        raise error

                graph = build_graph(**{parameter: RaisingGate()})
                with self.assertRaises(RuntimeError) as caught:
                    graph.invoke(CASES[0])
                self.assertIs(caught.exception, error)

    def test_action_exceptions_propagate(self):
        bad_count = dict(CASES[0], count=None)
        with self.assertRaises(TypeError):
            build_graph().invoke(bad_count)
        missing_left = dict(CASES[0])
        del missing_left['left']
        with self.assertRaises(KeyError):
            build_graph().invoke(missing_left)


if __name__ == '__main__':
    unittest.main(verbosity=2)
