"""Behavioral regression tests; run with --original to audit the baseline."""
import importlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

module_name = 'original' if '--original' in sys.argv else 'app'
if '--original' in sys.argv:
    sys.argv.remove('--original')
write_batch = importlib.import_module(module_name).write_batch


class BatchTests(unittest.TestCase):
    def connect(self, isolation='', factory=sqlite3.Connection):
        connection = sqlite3.connect(':memory:', isolation_level=isolation,
                                     factory=factory)
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        return connection

    def contents(self, connection):
        return connection.execute('SELECT key, value FROM records ORDER BY key').fetchall()

    def test_idle_success_is_committed_and_keys_are_bound(self):
        rows = [("quote'; DROP TABLE records; --", 1), ('中文\x00\n"', 2)]
        for isolation in ('', None):
            with self.subTest(isolation=isolation), tempfile.TemporaryDirectory(dir='.') as folder:
                path = str(Path(folder) / 'records.sqlite')
                c = sqlite3.connect(path, isolation_level=isolation)
                try:
                    c.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
                    self.assertEqual(write_batch(c, rows), 2)
                    self.assertFalse(c.in_transaction)
                    observer = sqlite3.connect(path)
                    try:
                        self.assertEqual(self.contents(observer), sorted(rows))
                    finally:
                        observer.close()
                    self.assertEqual(c.execute('SELECT 1').fetchone(), (1,))
                finally:
                    c.close()

    def test_idle_failure_is_atomic_and_repeatable(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation)
                write_batch(c, [('seed', 0)])
                for attempt in range(3):
                    with self.assertRaises(sqlite3.IntegrityError):
                        write_batch(c, [('prefix', attempt), ('seed', 9)])
                    self.assertEqual(self.contents(c), [('seed', 0)])
                    self.assertFalse(c.in_transaction)
                self.assertEqual(write_batch(c, [('recovered', 4)]), 1)

    def test_outer_success_can_be_rolled_back(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation)
                c.execute('BEGIN')
                c.execute("INSERT INTO records VALUES ('caller', 1)")
                self.assertEqual(write_batch(c, [('batch', 2)]), 1)
                self.assertTrue(c.in_transaction)
                self.assertEqual(self.contents(c), [('batch', 2), ('caller', 1)])
                c.rollback()
                self.assertEqual(self.contents(c), [])

    def test_outer_failure_preserves_work_and_can_commit(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation)
                c.execute('BEGIN')
                c.execute("INSERT INTO records VALUES ('caller', 1)")
                for _ in range(3):
                    with self.assertRaises(sqlite3.IntegrityError):
                        write_batch(c, [('prefix', 2), ('caller', 3)])
                    self.assertTrue(c.in_transaction)
                    self.assertEqual(self.contents(c), [('caller', 1)])
                c.commit()
                self.assertEqual(self.contents(c), [('caller', 1)])

    def test_implicit_outer_transaction_is_preserved(self):
        c = self.connect()
        c.execute("INSERT INTO records VALUES ('caller', 1)")
        self.assertEqual(write_batch(c, [('batch', 2)]), 1)
        self.assertTrue(c.in_transaction)
        c.rollback()
        self.assertEqual(self.contents(c), [])

    def test_caller_nested_savepoints_survive_all_outcomes(self):
        for isolation in ('', None):
            for outcome in ('success', 'failure', 'empty'):
                with self.subTest(isolation=isolation, outcome=outcome):
                    c = self.connect(isolation)
                    c.execute('SAVEPOINT write_batch')
                    c.execute("INSERT INTO records VALUES ('caller', 1)")
                    c.execute('SAVEPOINT "caller nested"')
                    if outcome == 'failure':
                        with self.assertRaises(sqlite3.IntegrityError):
                            write_batch(c, [('prefix', 2), ('caller', 3)])
                    else:
                        rows = [('batch', 2)] if outcome == 'success' else []
                        self.assertEqual(write_batch(c, rows), len(rows))
                    c.execute('ROLLBACK TO "caller nested"')
                    self.assertEqual(self.contents(c), [('caller', 1)])
                    c.execute('RELEASE "caller nested"')
                    c.execute('ROLLBACK TO write_batch')
                    self.assertEqual(self.contents(c), [])
                    c.execute('RELEASE write_batch')
                    self.assertFalse(c.in_transaction)

    def test_empty_batch_preserves_transaction_state(self):
        for isolation in ('', None):
            for outer in (False, True):
                with self.subTest(isolation=isolation, outer=outer):
                    c = self.connect(isolation)
                    if outer:
                        c.execute('BEGIN')
                        c.execute("INSERT INTO records VALUES ('caller', 1)")
                    before = self.contents(c)
                    self.assertEqual(write_batch(c, []), 0)
                    self.assertEqual(c.in_transaction, outer)
                    self.assertEqual(self.contents(c), before)

    def test_binding_and_not_null_errors_rollback_prefix(self):
        for isolation in ('', None):
            for bad, error in ((('null', None), sqlite3.IntegrityError),
                               (('missing-value',), sqlite3.ProgrammingError)):
                with self.subTest(isolation=isolation, bad=bad):
                    c = self.connect(isolation)
                    with self.assertRaises(error):
                        write_batch(c, [('prefix', 1), bad])
                    self.assertEqual(self.contents(c), [])
                    self.assertFalse(c.in_transaction)

    def test_original_database_exception_identity(self):
        class RecordingConnection(sqlite3.Connection):
            error = None

            def executemany(self, *args, **kwargs):
                try:
                    return super().executemany(*args, **kwargs)
                except BaseException as error:
                    self.error = error
                    raise

        c = self.connect(factory=RecordingConnection)
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            write_batch(c, [('duplicate', 1), ('duplicate', 2)])
        self.assertIs(caught.exception, c.error)
        self.assertEqual(self.contents(c), [])

    def test_interruption_rolls_back_and_is_preserved(self):
        interruption = KeyboardInterrupt('injected after a real insert')

        class InterruptedConnection(sqlite3.Connection):
            def executemany(self, sql, rows):
                super().execute(sql, rows[0])
                raise interruption

        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation, InterruptedConnection)
                c.execute('BEGIN')
                c.execute("INSERT INTO records VALUES ('caller', 1)")
                with self.assertRaises(KeyboardInterrupt) as caught:
                    write_batch(c, [('prefix', 2)])
                self.assertIs(caught.exception, interruption)
                self.assertEqual(self.contents(c), [('caller', 1)])
                self.assertTrue(c.in_transaction)

    def test_cleanup_failure_does_not_replace_original_exception(self):
        original_error = ValueError('injected insertion error')

        class FailingConnection(sqlite3.Connection):
            def executemany(self, *args):
                raise original_error

            def execute(self, sql, *args):
                if sql.startswith('ROLLBACK TO'):
                    raise sqlite3.OperationalError('injected cleanup error')
                return super().execute(sql, *args)

        c = self.connect(factory=FailingConnection)
        with self.assertRaises(ValueError) as caught:
            write_batch(c, [('unused', 1)])
        self.assertIs(caught.exception, original_error)
        c.rollback()

    def test_deferred_constraint_commit_failure_rolls_back(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation)
                c.execute('PRAGMA foreign_keys=ON')
                c.execute('CREATE TABLE parent(id INTEGER PRIMARY KEY)')
                c.execute('DROP TABLE records')
                c.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER '
                          'REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)')
                with self.assertRaises(sqlite3.IntegrityError):
                    write_batch(c, [('invalid-reference', 1)])
                self.assertEqual(self.contents(c), [])
                self.assertFalse(c.in_transaction)


if __name__ == '__main__':
    unittest.main(verbosity=2)
