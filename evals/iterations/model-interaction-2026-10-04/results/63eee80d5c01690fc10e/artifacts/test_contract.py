"""Local contract checks; optionally run against the original module."""
import copy
import importlib
import itertools
import sys
import unittest

target = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app')


def success(key='a', version=1, values=None):
    return dict(call_id=key, version=version, status='success',
                values=[3] if values is None else values)


def error(key='a', version=1, message=''):
    return dict(call_id=key, version=version, status='error', message=message)


class ContractTests(unittest.TestCase):
    def batch(self):
        return target.ToolBatch([dict(call_id='a', weight=2),
                                 dict(call_id='b', weight=-3)])

    def test_signed_score(self):
        for values in ([], [0], [3], [-5], [3, -5], [-2, 2], [10**100, -7]):
            for weight in (0, 1, -3, 10**100):
                with self.subTest(values=values, weight=weight):
                    saved = values[:]
                    self.assertEqual(target.score(values, weight), sum(values) * weight)
                    self.assertEqual(values, saved)

    def test_specs(self):
        invalid = [None, {}, (), [None], [{}],
                   [dict(call_id='', weight=1)], [dict(call_id=3, weight=1)],
                   [dict(call_id='a', weight=True)], [dict(call_id='a', weight=1.0)],
                   [dict(call_id='a')], [dict(call_id='a', weight=1, extra=0)],
                   [dict(call_id='a', weight=1), dict(call_id='a', weight=2)]]
        for specs in invalid:
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                target.ToolBatch(specs)
        for weight in (0, -1, 10**100):
            target.ToolBatch([dict(call_id='a', weight=weight)])

    def test_pending_and_order(self):
        b = self.batch()
        self.assertEqual(b.snapshot(), [
            dict(call_id=k, version=None, status='pending', value=None, error=None)
            for k in ('a', 'b')])
        b.ingest([success('b', values=[-2, 5]), success(values=[3, -5])])
        self.assertEqual(b.finalize(), [
            dict(call_id='a', version=1, status='success', value=-4, error=None),
            dict(call_id='b', version=1, status='success', value=-9, error=None)])

    def test_versions_and_list_order(self):
        for updates in itertools.permutations([success(version=1), error(version=4),
                                                success(version=9, values=[])]):
            b = self.batch()
            for update in updates:
                b.ingest([update])
            self.assertEqual(b.snapshot()[0], dict(call_id='a', version=9,
                             status='success', value=0, error=None))
        b = self.batch()
        b.ingest([success(), error(version=4), success(values=[99]), error(version=4)])
        self.assertEqual(b.snapshot()[0]['status'], 'error')
        before = b.snapshot()
        for conflict in (success(version=4), error(version=4, message='different')):
            with self.assertRaises(ValueError):
                b.ingest([success('b'), conflict])
            self.assertEqual(b.snapshot(), before)
        b.ingest([success(version=8), error(version=4, message='old conflict')])
        self.assertEqual(b.snapshot()[0]['version'], 8)
        b2 = self.batch()
        with self.assertRaises(ValueError):
            b2.ingest([success(), success(values=[99]), error(version=2)])
        self.assertEqual(b2.snapshot(), self.batch().snapshot())

    def test_validation_and_atomic_rejection(self):
        bad_items = [None, [], {}, success('unknown')]
        for field, bad_values in {
            'call_id': [None, [], 1, ''],
            'version': [None, True, 0, -1, 1.0, '1'],
            'status': [None, [], True, 'pending', 'Success'],
            'values': [None, (), '3', [True], [1.0], ['1'], [[]]],
        }.items():
            for value in bad_values:
                item = success()
                item[field] = value
                bad_items.append(item)
        for template in (success(), error()):
            for field in template:
                item = template.copy()
                del item[field]
                bad_items.append(item)
            bad_items.append(dict(template, extra=1))
        for value in (None, 1, [], True):
            bad_items.append(error(message=value))
        for item in bad_items:
            with self.subTest(item=item):
                b = self.batch()
                b.ingest([success(version=10)])
                before = b.snapshot()
                updates = [success('b'), item]
                saved = copy.deepcopy(updates)
                with self.assertRaises(ValueError):
                    b.ingest(updates)
                self.assertEqual(b.snapshot(), before)
                self.assertEqual(updates, saved)
                b.ingest([error('b')])
                b.finalize()
        for updates in (None, {}, (), 'invalid'):
            with self.assertRaises(ValueError):
                self.batch().ingest(updates)

    def test_ownership(self):
        specs = [dict(call_id='a', weight=2)]
        saved_specs = copy.deepcopy(specs)
        b = target.ToolBatch(specs)
        self.assertEqual(specs, saved_specs)
        specs[0].update(call_id='other', weight=99)
        specs.clear()
        updates = [success(values=[-2, 5])]
        saved_updates = copy.deepcopy(updates)
        b.ingest(updates)
        self.assertEqual(updates, saved_updates)
        updates[0]['values'].append(100)
        updates[0]['version'] = 99
        updates.clear()
        expected = [dict(call_id='a', version=1, status='success', value=6, error=None)]
        for read in (b.snapshot, b.finalize, b.finalize, b.snapshot):
            result = read()
            self.assertEqual(result, expected)
            result[0]['value'] = 999
            result.append({})
            self.assertEqual(b.snapshot(), expected)

    def test_finalization(self):
        b = self.batch()
        b.ingest([])
        with self.assertRaises(ValueError):
            b.finalize()
        b.ingest([success()])
        with self.assertRaises(ValueError):
            b.finalize()
        b.ingest([error('b', message='failed')])
        expected = b.snapshot()
        self.assertEqual(expected[1], dict(call_id='b', version=1,
                         status='error', value=None, error='failed'))
        self.assertEqual(b.finalize(), expected)
        self.assertEqual(b.finalize(), expected)
        for updates in ([], [success(version=2)], None):
            with self.assertRaises(RuntimeError):
                b.ingest(updates)
        empty = target.ToolBatch([])
        empty.ingest([])
        self.assertEqual(empty.finalize(), [])
        self.assertEqual(empty.finalize(), [])
        with self.assertRaises(RuntimeError):
            empty.ingest([])


if __name__ == '__main__':
    unittest.main()
