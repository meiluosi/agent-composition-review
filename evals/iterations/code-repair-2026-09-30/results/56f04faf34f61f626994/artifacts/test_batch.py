"""Behavioral regression tests; optionally pass the module name (app/original)."""
import importlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = sys.argv.pop(1) if len(sys.argv) > 1 else 'app'
write_batch = importlib.import_module(MODULE).write_batch


class BatchTests(unittest.TestCase):
    def connect(self, isolation=''):
        c = sqlite3.connect(':memory:', isolation_level=isolation)
        self.addCleanup(c.close)
        c.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        return c

    def records(self, c):
        return c.execute('SELECT key, value FROM records ORDER BY key').fetchall()

    def outer(self, c, kind):
        if kind == 'begin':
            c.execute('BEGIN')
        elif kind == 'savepoint':
            c.execute('SAVEPOINT caller')
        c.execute("INSERT INTO records VALUES ('caller', 1)")
        c.execute('SAVEPOINT nested')

    def test_standalone_success_empty_and_special_keys(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation)
                rows = [("quote'\"; DROP TABLE records; --", 2), ('中文\n\x00', 3)]
                self.assertEqual(write_batch(c, tuple(rows)), 2)
                self.assertEqual(self.records(c), sorted(rows))
                self.assertFalse(c.in_transaction)
                self.assertEqual(write_batch(c, []), 0)
                self.assertFalse(c.in_transaction)
                c.rollback()
                self.assertEqual(self.records(c), sorted(rows))

    def test_standalone_repeated_failures_are_atomic(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation)
                for unused in range(2):
                    with self.assertRaises(sqlite3.IntegrityError):
                        write_batch(c, [('partial', 1), ('partial', 2)])
                    self.assertEqual(self.records(c), [])
                    self.assertFalse(c.in_transaction)
                self.assertEqual(write_batch(c, [('recovered', 3)]), 1)

    def test_outer_success_and_empty_preserve_savepoints(self):
        for isolation, kind in (('', 'implicit'), ('', 'begin'), ('', 'savepoint'),
                                (None, 'begin'), (None, 'savepoint')):
            for rows in ([], [('batch', 2)]):
                with self.subTest(isolation=isolation, kind=kind, rows=rows):
                    c = self.connect(isolation)
                    self.outer(c, kind)
                    self.assertEqual(write_batch(c, rows), len(rows))
                    self.assertTrue(c.in_transaction)
                    self.assertEqual(self.records(c), sorted([('caller', 1)] + rows))
                    c.execute('ROLLBACK TO nested')
                    self.assertEqual(self.records(c), [('caller', 1)])
                    c.execute('RELEASE nested')
                    if kind == 'savepoint':
                        c.execute('ROLLBACK TO caller')
                        self.assertEqual(self.records(c), [])
                        c.execute('RELEASE caller')
                    else:
                        c.rollback()
                        self.assertEqual(self.records(c), [])

    def test_outer_failure_preserves_work_and_allows_commit(self):
        for isolation, kind in (('', 'implicit'), ('', 'begin'), ('', 'savepoint'),
                                (None, 'begin'), (None, 'savepoint')):
            with self.subTest(isolation=isolation, kind=kind):
                c = self.connect(isolation)
                self.outer(c, kind)
                for unused in range(2):
                    with self.assertRaises(sqlite3.IntegrityError):
                        write_batch(c, [('partial', 2), ('caller', 3)])
                    self.assertTrue(c.in_transaction)
                    self.assertEqual(self.records(c), [('caller', 1)])
                    c.execute('ROLLBACK TO nested')
                c.execute('RELEASE nested')
                self.assertEqual(write_batch(c, [('recovered', 4)]), 1)
                c.commit()
                c.rollback()
                self.assertEqual(self.records(c), [('caller', 1), ('recovered', 4)])

    def test_binding_and_not_null_errors_rollback_partial_batch(self):
        for isolation in ('', None):
            for bad, error in ((('bad', None), sqlite3.IntegrityError),
                               (('bad', object()), sqlite3.ProgrammingError),
                               (('bad',), sqlite3.ProgrammingError)):
                with self.subTest(isolation=isolation, error=error, bad=bad):
                    c = self.connect(isolation)
                    with self.assertRaises(error):
                        write_batch(c, [('partial', 1), bad])
                    self.assertEqual(self.records(c), [])
                    self.assertFalse(c.in_transaction)

    def test_deferred_constraint_failure_at_commit(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = sqlite3.connect(':memory:', isolation_level=isolation)
                self.addCleanup(c.close)
                c.execute('PRAGMA foreign_keys=ON')
                c.execute('CREATE TABLE parent(id INTEGER PRIMARY KEY)')
                c.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER '
                          'REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)')
                with self.assertRaises(sqlite3.IntegrityError):
                    write_batch(c, [('bad', 42)])
                self.assertEqual(self.records(c), [])
                self.assertFalse(c.in_transaction)

    def test_commit_visible_to_other_connection(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation), tempfile.TemporaryDirectory(dir='.') as d:
                c = sqlite3.connect(str(Path(d) / 'db'), isolation_level=isolation)
                observer = sqlite3.connect(str(Path(d) / 'db'))
                try:
                    c.execute('CREATE TABLE records(key TEXT PRIMARY KEY,value INTEGER)')
                    write_batch(c, [('committed', 1)])
                    self.assertEqual(self.records(observer), [('committed', 1)])
                    c.execute('BEGIN')
                    c.execute("INSERT INTO records VALUES ('caller', 2)")
                    write_batch(c, [('pending', 3)])
                    self.assertEqual(self.records(observer), [('committed', 1)])
                    c.commit()
                    self.assertEqual(self.records(observer),
                                     [('caller', 2), ('committed', 1), ('pending', 3)])
                finally:
                    observer.close()
                    c.close()

    def test_original_exception_identity_even_if_cleanup_raises(self):
        primary = KeyboardInterrupt('injected insertion interruption')

        class InterruptedConnection(sqlite3.Connection):
            def executemany(self, sql, rows):
                self.execute(sql, rows[0])
                raise primary

            def execute(self, sql, *args):
                result = super().execute(sql, *args)
                if sql.startswith('RELEASE'):
                    raise RuntimeError('injected error after cleanup release')
                return result

        c = sqlite3.connect(':memory:', factory=InterruptedConnection)
        self.addCleanup(c.close)
        c.execute('CREATE TABLE records(key TEXT PRIMARY KEY,value INTEGER)')
        try:
            write_batch(c, [('partial', 1)])
        except BaseException as caught:
            self.assertIs(caught, primary)
        else:
            self.fail('Expected original exception')
        self.assertEqual(self.records(c), [])
        self.assertFalse(c.in_transaction)


if __name__ == '__main__':
    unittest.main(verbosity=2)
