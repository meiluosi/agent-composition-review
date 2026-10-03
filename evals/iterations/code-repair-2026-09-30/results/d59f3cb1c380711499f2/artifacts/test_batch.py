import importlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


target = importlib.import_module(sys.argv.pop(1) if len(sys.argv) > 1 else 'app')


class BatchTests(unittest.TestCase):
    def connect(self, isolation='', factory=sqlite3.Connection):
        connection = sqlite3.connect(':memory:', isolation_level=isolation, factory=factory)
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        return connection

    def contents(self, connection):
        return connection.execute('SELECT key, value FROM records ORDER BY key').fetchall()

    def test_transaction_matrix(self):
        for isolation in ('', None):
            for outer in ('none', 'begin', 'savepoint', 'nested', 'implicit'):
                if outer == 'implicit' and isolation is None:
                    continue
                for outcome in ('success', 'empty', 'duplicate', 'null'):
                    with self.subTest(isolation=isolation, outer=outer, outcome=outcome):
                        c = self.connect(isolation)
                        c.execute('INSERT INTO records VALUES (?, ?)', ('committed', 1))
                        c.commit()
                        if outer == 'begin' or outer == 'nested':
                            c.execute('BEGIN')
                        if outer in ('savepoint', 'nested'):
                            c.execute('SAVEPOINT caller')
                        if outer != 'none':
                            c.execute('INSERT INTO records VALUES (?, ?)', ('caller', 2))
                        if outer == 'nested':
                            c.execute('SAVEPOINT inner_caller')
                        before = self.contents(c)
                        active = c.in_transaction
                        rows = [("quote'; -- 雪\x00", 3), ('another', 4)]
                        if outcome == 'empty':
                            rows = []
                        if outcome in ('duplicate', 'null'):
                            rows = [('partial', 5), ('committed', 6) if outcome == 'duplicate' else ('bad', None)]
                            for _ in range(3):
                                with self.assertRaises(sqlite3.IntegrityError):
                                    target.write_batch(c, rows)
                                self.assertEqual(self.contents(c), before)
                                self.assertEqual(c.in_transaction, active)
                        else:
                            self.assertEqual(target.write_batch(c, rows), len(rows))
                            self.assertEqual(self.contents(c), sorted(before + rows))
                            self.assertEqual(c.in_transaction, active)
                        if outer == 'nested':
                            c.execute('ROLLBACK TO inner_caller')
                            c.execute('RELEASE inner_caller')
                        if outer in ('savepoint', 'nested'):
                            c.execute('ROLLBACK TO caller')
                            c.execute('RELEASE caller')
                        c.rollback()
                        expected = [('committed', 1)]
                        if outer == 'none' and outcome == 'success':
                            expected = sorted(expected + rows)
                        self.assertEqual(self.contents(c), expected)
                        c.execute('SELECT 1')

    def test_caller_can_commit(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation)
                c.execute('SAVEPOINT caller')
                c.execute("INSERT INTO records VALUES ('caller', 1)")
                self.assertEqual(target.write_batch(c, [('batch', 2)]), 1)
                c.execute('RELEASE caller')
                c.rollback()
                self.assertEqual(self.contents(c), [('batch', 2), ('caller', 1)])

    def test_deferred_constraint_at_release(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation)
                c.execute('DROP TABLE records')
                c.execute('PRAGMA foreign_keys=ON')
                c.execute('CREATE TABLE parent(id INTEGER PRIMARY KEY)')
                c.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)')
                for _ in range(2):
                    with self.assertRaises(sqlite3.IntegrityError):
                        target.write_batch(c, [('bad', 99)])
                    self.assertEqual(self.contents(c), [])
                    self.assertFalse(c.in_transaction)

    def test_commit_busy(self):
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = str(Path(directory) / 'records.db')
            writer = sqlite3.connect(path, timeout=0)
            reader = sqlite3.connect(path, timeout=0)
            try:
                writer.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
                reader.execute('BEGIN')
                reader.execute('SELECT * FROM records').fetchall()
                with self.assertRaises(sqlite3.OperationalError) as caught:
                    target.write_batch(writer, [('blocked', 1)])
                self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)
                self.assertEqual(self.contents(writer), [])
                self.assertFalse(writer.in_transaction)
                reader.rollback()
                self.assertEqual(target.write_batch(writer, [('retry', 2)]), 1)
                self.assertEqual(self.contents(reader), [('retry', 2)])
            finally:
                reader.close()
                writer.close()

    def test_original_exception_survives_cleanup_failure(self):
        class CleanupFailure(sqlite3.Connection):
            insertion_error = None

            def executemany(self, sql, rows):
                try:
                    return super().executemany(sql, rows)
                except BaseException as exc:
                    self.insertion_error = exc
                    raise

            def execute(self, sql, *args):
                if sql.startswith('RELEASE '):
                    # Perform real cleanup, then simulate a secondary failure.
                    super().execute(sql, *args)
                    raise RuntimeError('secondary cleanup error')
                return super().execute(sql, *args)

        c = self.connect(factory=CleanupFailure)
        c.execute('BEGIN')
        c.execute("INSERT INTO records VALUES ('caller', 3)")
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            target.write_batch(c, [('duplicate', 1), ('duplicate', 2)])
        self.assertIs(caught.exception, c.insertion_error)
        self.assertEqual(self.contents(c), [('caller', 3)])
        self.assertTrue(c.in_transaction)

    def test_iteration_error_preserved(self):
        failure = KeyboardInterrupt('interrupted iteration')

        class BrokenSequence:
            def __len__(self):
                return 2

            def __getitem__(self, index):
                if index == 0:
                    return ('partial', 1)
                raise failure

        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation)
                c.execute('BEGIN')
                c.execute("INSERT INTO records VALUES ('caller', 2)")
                with self.assertRaises(KeyboardInterrupt) as caught:
                    target.write_batch(c, BrokenSequence())
                self.assertIs(caught.exception, failure)
                self.assertEqual(self.contents(c), [('caller', 2)])
                self.assertTrue(c.in_transaction)


if __name__ == '__main__':
    unittest.main(verbosity=2)
