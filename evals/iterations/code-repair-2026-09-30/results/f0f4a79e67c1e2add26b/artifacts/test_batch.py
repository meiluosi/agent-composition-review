import importlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


TARGET = sys.argv.pop(1) if len(sys.argv) > 1 else 'app'
write_batch = importlib.import_module(TARGET).write_batch


class BatchTests(unittest.TestCase):
    def connect(self, isolation_level='', path=':memory:'):
        connection = sqlite3.connect(path, isolation_level=isolation_level, timeout=0)
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        return connection

    def contents(self, connection):
        return connection.execute('SELECT key, value FROM records ORDER BY key').fetchall()

    def test_transaction_matrix(self):
        for isolation in ('', None):
            for outer in ('none', 'begin', 'savepoint', 'nested', 'implicit'):
                if isolation is None and outer == 'implicit':
                    continue
                for outcome in ('success', 'failure', 'empty'):
                    with self.subTest(isolation=isolation, outer=outer, outcome=outcome):
                        c = self.connect(isolation)
                        c.execute("INSERT INTO records VALUES ('existing', 1)")
                        c.commit()
                        if outer in ('begin', 'nested'):
                            c.execute('BEGIN')
                        if outer in ('savepoint', 'nested'):
                            c.execute('SAVEPOINT caller')
                        if outer != 'none':
                            c.execute("INSERT INTO records VALUES ('caller', 2)")
                        before = self.contents(c)
                        if outcome == 'failure':
                            for _ in range(3):
                                with self.assertRaises(sqlite3.IntegrityError):
                                    write_batch(c, [('partial', 3), ('existing', 4)])
                                self.assertEqual(self.contents(c), before)
                        else:
                            rows = [] if outcome == 'empty' else [("quote'; --\n雪\x00", 3), ('second', 4)]
                            self.assertEqual(write_batch(c, rows), len(rows))
                            self.assertEqual(self.contents(c), sorted(before + rows))
                        self.assertEqual(c.in_transaction, outer != 'none')
                        if outer in ('savepoint', 'nested'):
                            c.execute('ROLLBACK TO caller')
                            self.assertEqual(self.contents(c), [('existing', 1)])
                            c.execute('RELEASE caller')
                        elif outer != 'none':
                            c.rollback()
                            self.assertEqual(self.contents(c), [('existing', 1)])
                        self.assertEqual(write_batch(c, [('reused', 7)]), 1)

    def test_caller_can_commit_after_success_or_failure(self):
        for isolation in ('', None):
            for savepoint in (False, True):
                with self.subTest(isolation=isolation, savepoint=savepoint):
                    c = self.connect(isolation)
                    c.execute('BEGIN')
                    c.execute("INSERT INTO records VALUES ('caller', 1)")
                    if savepoint:
                        c.execute('SAVEPOINT caller_point')
                    self.assertEqual(write_batch(c, [('batch', 2)]), 1)
                    with self.assertRaises(sqlite3.IntegrityError):
                        write_batch(c, [('partial', 3), ('caller', 4)])
                    if savepoint:
                        c.execute('RELEASE caller_point')
                    c.commit()
                    self.assertEqual(self.contents(c), [('batch', 2), ('caller', 1)])

    def test_standalone_commit_visible_from_other_connection(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation), tempfile.TemporaryDirectory(dir='.') as folder:
                path = str(Path(folder) / 'records.db')
                c = self.connect(isolation, path)
                self.assertEqual(write_batch(c, [('persisted', 1)]), 1)
                with sqlite3.connect(path) as observer:
                    self.assertEqual(self.contents(observer), [('persisted', 1)])
                observer.close()

    def deferred_connection(self, isolation):
        c = sqlite3.connect(':memory:', isolation_level=isolation)
        self.addCleanup(c.close)
        c.execute('PRAGMA foreign_keys = ON')
        c.execute('CREATE TABLE parents(id INTEGER PRIMARY KEY)')
        c.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER REFERENCES parents(id) DEFERRABLE INITIALLY DEFERRED)')
        return c

    def test_release_failure_rolls_back_batch(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.deferred_connection(isolation)
                for _ in range(3):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
                        write_batch(c, [('bad', 123)])
                    self.assertEqual(self.contents(c), [])
                    self.assertFalse(c.in_transaction)
                c.execute('INSERT INTO parents VALUES (123)')
                c.commit()
                self.assertEqual(write_batch(c, [('valid', 123)]), 1)

    def test_deferred_constraint_remains_callers_responsibility(self):
        c = self.deferred_connection(None)
        c.execute('BEGIN')
        c.execute('SAVEPOINT caller')
        self.assertEqual(write_batch(c, [('pending', 123)]), 1)
        c.execute('INSERT INTO parents VALUES (123)')
        c.execute('RELEASE caller')
        c.commit()
        self.assertEqual(self.contents(c), [('pending', 123)])

    def test_locked_commit_rolls_back_batch(self):
        with tempfile.TemporaryDirectory(dir='.') as folder:
            path = str(Path(folder) / 'locked.db')
            c = self.connect(path=path)
            c.execute("INSERT INTO records VALUES ('existing', 1)")
            c.commit()
            reader = sqlite3.connect(path, timeout=0)
            self.addCleanup(reader.close)
            reader.execute('BEGIN')
            self.assertEqual(self.contents(reader), [('existing', 1)])
            with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                write_batch(c, [('partial', 2)])
            self.assertEqual(self.contents(c), [('existing', 1)])
            self.assertFalse(c.in_transaction)
            reader.rollback()
            self.assertEqual(write_batch(c, [('retry', 2)]), 1)

    def test_cleanup_failure_preserves_original_exception(self):
        class CleanupFails(sqlite3.Connection):
            original_error = None

            def executemany(self, sql, rows):
                try:
                    return super().executemany(sql, rows)
                except BaseException as error:
                    self.original_error = error
                    raise

            def execute(self, sql, *args):
                if sql.startswith('ROLLBACK TO '):
                    super().execute(sql, *args)
                    raise sqlite3.OperationalError('injected error after rollback')
                return super().execute(sql, *args)

        c = sqlite3.connect(':memory:', factory=CleanupFails)
        self.addCleanup(c.close)
        c.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        c.execute('BEGIN')
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            write_batch(c, [('duplicate', 1), ('duplicate', 2)])
        self.assertIs(caught.exception, c.original_error)
        self.assertEqual(self.contents(c), [])

    def test_not_null_failure_is_atomic(self):
        c = self.connect()
        with self.assertRaises(sqlite3.IntegrityError):
            write_batch(c, [('partial', 1), ('invalid', None)])
        self.assertEqual(self.contents(c), [])
        self.assertFalse(c.in_transaction)


if __name__ == '__main__':
    unittest.main(verbosity=2)
