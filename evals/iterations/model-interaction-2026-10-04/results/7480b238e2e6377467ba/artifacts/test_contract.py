"""Business-contract regression tests; optionally pass 'original' as first argument."""
import copy
import importlib
import itertools
import sys
import unittest

module_name = sys.argv.pop(1) if len(sys.argv) > 1 and sys.argv[1] == 'original' else 'app'
app = importlib.import_module(module_name)


def success(call_id='a', version=1, values=None):
    return dict(call_id=call_id, version=version, status='success',
                values=[3] if values is None else values)


def error(call_id='a', version=1, message='failed'):
    return dict(call_id=call_id, version=version, status='error', message=message)


class ContractTests(unittest.TestCase):
    def batch(self):
        return app.ToolBatch([dict(call_id='a', weight=2), dict(call_id='b', weight=-3)])

    def test_score(self):
        for values, weight in [([], 7), ([-4, 2, -9], -5), ([10**100, -1], 10**100),
                               ([2, 3], 0), ([1, -1], 9)]:
            with self.subTest(values=values, weight=weight):
                before = values[:]
                self.assertEqual(app.score(values, weight), sum(values) * weight)
                self.assertEqual(values, before)

    def test_invalid_specs(self):
        bad_items = [None, [], {}, {'call_id': 'a'}, {'weight': 2},
                     dict(call_id='', weight=2), dict(call_id=1, weight=2),
                     dict(call_id='a', weight=True), dict(call_id='a', weight=1.0),
                     dict(call_id='a', weight='1'), dict(call_id='a', weight=None),
                     dict(call_id='a', weight=2, extra=0)]
        invalid = [None, {}, (), 'a'] + [[item] for item in bad_items]
        invalid += [[dict(call_id='a', weight=1), dict(call_id='a', weight=2)]]
        for specs in invalid:
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                app.ToolBatch(specs)

    def test_order_and_row_shapes(self):
        batch = self.batch()
        self.assertEqual(batch.snapshot(), [dict(call_id=k, version=None, status='pending',
                                                 value=None, error=None) for k in ('a', 'b')])
        batch.ingest([error('b', 9, ''), success('a', 4, [-2, 5])])
        self.assertEqual(batch.snapshot(), [
            dict(call_id='a', version=4, status='success', value=6, error=None),
            dict(call_id='b', version=9, status='error', value=None, error='')])

    def test_stale_results_and_retries(self):
        batch = self.batch()
        batch.ingest([success(version=10, values=[8])])
        expected = batch.snapshot()
        batch.ingest([error(version=2), success(version=10, values=[8]),
                      success(version=2, values=[100])])
        self.assertEqual(batch.snapshot(), expected)
        batch.ingest([error(version=11), success(version=10, values=[999])])
        self.assertEqual(batch.snapshot()[0]['status'], 'error')
        batch.ingest([success(version=20, values=[])])
        self.assertEqual(batch.snapshot()[0]['value'], 0)

    def test_conflicts_use_payload_not_score(self):
        batch = self.batch()
        batch.ingest([success(values=[1, 2])])
        for update in [success(values=[3]), success(values=[2, 1]), error()]:
            before = batch.snapshot()
            with self.subTest(update=update), self.assertRaises(ValueError):
                batch.ingest([success('b'), update])
            self.assertEqual(batch.snapshot(), before)
        batch.ingest([error(version=2, message='')])
        batch.ingest([error(version=2, message='')])
        with self.assertRaises(ValueError):
            batch.ingest([error(version=2, message='different')])

    def test_atomic_rejection_and_recovery(self):
        for seed in ([], [success(version=5)]):
            for bad in [dict(call_id='unknown'), error(version=6)]:
                batch = self.batch()
                batch.ingest(seed)
                before = batch.snapshot()
                with self.subTest(seed=seed, bad=bad), self.assertRaises(ValueError):
                    batch.ingest([success(version=6), success('b'), bad])
                self.assertEqual(batch.snapshot(), before)
                batch.ingest([error(version=7), success('b')])
                self.assertEqual([r['status'] for r in batch.finalize()], ['error', 'success'])

    def test_list_order_controls_current_version_conflicts(self):
        batch = self.batch()
        batch.ingest([success(), success(version=2), error(version=1)])
        self.assertEqual(batch.snapshot()[0]['version'], 2)
        before = batch.snapshot()
        with self.assertRaises(ValueError):
            batch.ingest([success('b'), error(version=2), success(version=3)])
        self.assertEqual(batch.snapshot(), before)

    def test_invalid_updates_even_when_stale(self):
        invalid = [None, [], 'a', {}, success('unknown'), success(call_id=1),
                   success(call_id=[]), success(version=True), success(version=0),
                   success(version=-1), success(version=1.0), success(version='1'),
                   success(values=(1,)), success(values='1'), success(values=[True]),
                   success(values=[1.0]), success(values=[None]), error(message=3)]
        for valid in [success(), error()]:
            for key in valid:
                missing = valid.copy()
                del missing[key]
                invalid.append(missing)
            invalid.append(dict(valid, extra=0))
        invalid += [dict(success(), status=status) for status in (None, '', 'pending', [], 1)]
        invalid += [dict(success(), message='unexpected'), dict(error(), values=[])]
        for update in invalid:
            batch = self.batch()
            batch.ingest([success(version=100)])
            before = batch.snapshot()
            with self.subTest(update=update), self.assertRaises(ValueError):
                batch.ingest([success('b'), update])
            self.assertEqual(batch.snapshot(), before)
            batch.ingest([success('b')])
            batch.finalize()

    def test_invalid_update_container(self):
        batch = self.batch()
        for updates in (None, {}, (), 'a', iter([])):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                batch.ingest(updates)
        batch.ingest([])
        self.assertTrue(all(r['status'] == 'pending' for r in batch.snapshot()))

    def test_input_and_output_isolation(self):
        specs = [dict(call_id='a', weight=2), dict(call_id='b', weight=-3)]
        original_specs = copy.deepcopy(specs)
        batch = app.ToolBatch(specs)
        self.assertEqual(specs, original_specs)
        specs[0]['weight'] = 100
        specs[1]['call_id'] = 'other'
        specs.clear()
        updates = [success(values=[-2, 5]), error('b', message='saved')]
        original_updates = copy.deepcopy(updates)
        batch.ingest(updates)
        self.assertEqual(updates, original_updates)
        expected = batch.snapshot()
        updates[0]['values'].append(500)
        updates[0]['version'] = 100
        updates[1]['message'] = 'changed'
        updates.clear()
        for result in (batch.snapshot(), batch.finalize(), batch.finalize()):
            self.assertEqual(result, expected)
            result[0]['value'] = 999
            result[1].clear()
            result.clear()
            self.assertEqual(batch.snapshot(), expected)

    def test_finalize_lifecycle(self):
        batch = self.batch()
        for update in ([], [error()]):
            batch.ingest(update)
            before = batch.snapshot()
            with self.assertRaises(ValueError):
                batch.finalize()
            self.assertEqual(batch.snapshot(), before)
        batch.ingest([success('b')])
        expected = batch.finalize()
        self.assertEqual(batch.finalize(), expected)
        for updates in ([], [success(version=2)], None):
            with self.assertRaises(RuntimeError):
                batch.ingest(updates)
            self.assertEqual(batch.snapshot(), expected)

    def test_empty_expected_batch(self):
        batch = app.ToolBatch([])
        batch.ingest([])
        self.assertEqual(batch.snapshot(), [])
        with self.assertRaises(ValueError):
            batch.ingest([success()])
        first = batch.finalize()
        self.assertEqual(first, [])
        self.assertIsNot(first, batch.finalize())
        with self.assertRaises(RuntimeError):
            batch.ingest([])

    def test_all_arrival_orders_and_batch_boundaries(self):
        updates = [success(version=1), error(version=3), success(version=8, values=[-9]),
                   success('b', 2), error('b', 5, '')]
        expected = [dict(call_id='a', version=8, status='success', value=-18, error=None),
                    dict(call_id='b', version=5, status='error', value=None, error='')]
        for order in itertools.permutations(updates):
            for split in range(len(order) + 1):
                batch = self.batch()
                batch.ingest(list(order[:split]))
                batch.ingest(list(order[split:]))
                self.assertEqual(batch.finalize(), expected)

    def test_zero_negative_and_large_weights(self):
        for weight in (0, -9, 10**100):
            batch = app.ToolBatch([dict(call_id='a', weight=weight)])
            batch.ingest([success(version=10**100, values=[10**100, -4])])
            self.assertEqual(batch.finalize()[0]['value'], (10**100 - 4) * weight)


if __name__ == '__main__':
    unittest.main()
