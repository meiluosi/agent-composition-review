import importlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


module_name = 'app'
if '--module' in sys.argv:
    index = sys.argv.index('--module')
    module_name = sys.argv[index + 1]
    del sys.argv[index:index + 2]
write_batch = importlib.import_module(module_name).write_batch


class BatchTests(unittest.TestCase):
    def connect(self, isolation_level='', path=':memory:', factory=sqlite3.Connection):
        connection = sqlite3.connect(path, isolation_level=isolation_level,
                                     timeout=0, factory=factory)
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        return connection

    def contents(self, connection):
        return connection.execute('SELECT key, value FROM records ORDER BY key').fetchall()

    def test_standalone_commit_empty_and_special_keys(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation), tempfile.TemporaryDirectory(dir='.') as directory:
                path = str(Path(directory) / 'records.db')
                connection = self.connect(isolation, path)
                rows = [("quote'; --", 1), ('中文\x00key', 2), ('', 3)]
                self.assertEqual(write_batch(connection, rows), 3)
                self.assertFalse(connection.in_transaction)
                self.assertEqual(write_batch(connection, []), 0)
                self.assertFalse(connection.in_transaction)
                with sqlite3.connect(path) as observer:
                    self.assertEqual(set(self.contents(observer)), set(rows))
                connection.execute('SELECT 1')

    def test_repeated_failure_rolls_back_only_batch(self):
        for isolation in ('', None):
            for outer in (False, True):
                with self.subTest(isolation=isolation, outer=outer):
                    connection = self.connect(isolation)
                    write_batch(connection, [('existing', 1)])
                    if outer:
                        connection.execute('BEGIN')
                        connection.execute("INSERT INTO records VALUES ('caller', 2)")
                    expected = self.contents(connection)
                    for attempt in range(3):
                        with self.assertRaises(sqlite3.IntegrityError) as caught:
                            write_batch(connection, [('partial', 3), ('existing', 4)])
                        self.assertEqual(caught.exception.sqlite_errorcode,
                                         sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY)
                        self.assertEqual(self.contents(connection), expected)
                        self.assertEqual(connection.in_transaction, outer)
                    self.assertEqual(write_batch(connection, [('recovery', 5)]), 1)
                    if outer:
                        connection.rollback()
                        self.assertEqual(self.contents(connection), [('existing', 1)])

    def test_outer_transaction_can_commit_or_rollback(self):
        for isolation in ('', None):
            for finish in ('commit', 'rollback'):
                with self.subTest(isolation=isolation, finish=finish):
                    connection = self.connect(isolation)
                    # Exercise the default connection's implicit transaction too.
                    if isolation is None:
                        connection.execute('BEGIN')
                    connection.execute("INSERT INTO records VALUES ('caller', 1)")
                    self.assertTrue(connection.in_transaction)
                    self.assertEqual(write_batch(connection, []), 0)
                    self.assertEqual(write_batch(connection, [('batch', 2)]), 1)
                    self.assertTrue(connection.in_transaction)
                    getattr(connection, finish)()
                    expected = [('batch', 2), ('caller', 1)] if finish == 'commit' else []
                    self.assertEqual(self.contents(connection), expected)

    def test_caller_savepoints_survive_success_empty_and_failure(self):
        for isolation in ('', None):
            for explicit_begin in (False, True):
                with self.subTest(isolation=isolation, explicit_begin=explicit_begin):
                    connection = self.connect(isolation)
                    if explicit_begin:
                        connection.execute('BEGIN')
                    connection.execute('SAVEPOINT caller_outer')
                    connection.execute("INSERT INTO records VALUES ('caller', 1)")
                    connection.execute('SAVEPOINT caller_inner')
                    self.assertEqual(write_batch(connection, []), 0)
                    self.assertEqual(write_batch(connection, [('batch', 2)]), 1)
                    with self.assertRaises(sqlite3.IntegrityError):
                        write_batch(connection, [('partial', 3), ('caller', 4)])
                    self.assertEqual(self.contents(connection), [('batch', 2), ('caller', 1)])
                    connection.execute('ROLLBACK TO caller_inner')
                    self.assertEqual(self.contents(connection), [('caller', 1)])
                    connection.execute('RELEASE caller_inner')
                    connection.execute('ROLLBACK TO caller_outer')
                    self.assertEqual(self.contents(connection), [])
                    connection.execute('RELEASE caller_outer')
                    self.assertEqual(connection.in_transaction, explicit_begin)
                    connection.rollback()

    def test_deferred_constraint_commit_failure_rolls_back_batch(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                connection = sqlite3.connect(':memory:', isolation_level=isolation)
                self.addCleanup(connection.close)
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('CREATE TABLE parents(id INTEGER PRIMARY KEY)')
                connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER '
                                   'REFERENCES parents(id) DEFERRABLE INITIALLY DEFERRED)')
                with self.assertRaises(sqlite3.IntegrityError) as caught:
                    write_batch(connection, [('invalid', 123)])
                self.assertEqual(caught.exception.sqlite_errorcode,
                                 sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY)
                self.assertEqual(self.contents(connection), [])
                self.assertFalse(connection.in_transaction)
                self.assertEqual(write_batch(connection, []), 0)

    def test_locked_commit_rolls_back_batch(self):
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = str(Path(directory) / 'locked.db')
            writer = self.connect(path=path)
            reader = sqlite3.connect(path, timeout=0)
            self.addCleanup(reader.close)
            reader.execute('BEGIN')
            reader.execute('SELECT * FROM records').fetchall()
            with self.assertRaises(sqlite3.OperationalError) as caught:
                write_batch(writer, [('blocked', 1)])
            self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)
            self.assertEqual(self.contents(writer), [])
            self.assertFalse(writer.in_transaction)
            reader.rollback()
            self.assertEqual(write_batch(writer, [('retry', 2)]), 1)

    def test_original_exception_identity(self):
        class BatchInterrupted(BaseException):
            pass

        error = BatchInterrupted('interrupted after a row')

        class InterruptedRows(list):
            def __iter__(self):
                yield ('partial', 1)
                raise error

        connection = self.connect()
        with self.assertRaises(BatchInterrupted) as caught:
            write_batch(connection, InterruptedRows([('partial', 1), ('unused', 2)]))
        self.assertIs(caught.exception, error)
        self.assertEqual(self.contents(connection), [])
        self.assertFalse(connection.in_transaction)

    def test_sqlite_rollback_preserves_original_error(self):
        connection = self.connect()
        connection.execute("CREATE TRIGGER reject BEFORE INSERT ON records "
                           "WHEN NEW.key = 'reject' BEGIN "
                           "SELECT RAISE(ROLLBACK, 'original trigger error'); END")
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            write_batch(connection, [('partial', 1), ('reject', 2)])
        self.assertEqual(str(caught.exception), 'original trigger error')
        self.assertEqual(self.contents(connection), [])
        self.assertFalse(connection.in_transaction)

    def test_invalid_value_and_duplicate_within_batch(self):
        connection = self.connect()
        for rows in ([('first', 1), ('bad', None)], [('same', 1), ('same', 2)]):
            with self.subTest(rows=rows):
                with self.assertRaises(sqlite3.IntegrityError):
                    write_batch(connection, rows)
                self.assertEqual(self.contents(connection), [])
                self.assertFalse(connection.in_transaction)


if __name__ == '__main__':
    unittest.main(verbosity=2)
