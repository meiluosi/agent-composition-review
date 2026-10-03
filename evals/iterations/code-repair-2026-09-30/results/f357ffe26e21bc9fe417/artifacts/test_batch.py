import importlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

implementation = sys.argv.pop(1) if len(sys.argv) > 1 else 'app'
write_batch = importlib.import_module(implementation).write_batch


class BatchTests(unittest.TestCase):
    def connect(self, isolation_level=''):
        connection = sqlite3.connect(':memory:', isolation_level=isolation_level)
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        return connection

    def records(self, connection):
        return connection.execute('SELECT key, value FROM records ORDER BY key').fetchall()

    def test_standalone_success_empty_and_special_keys(self):
        for mode in ('', None):
            with self.subTest(mode=mode):
                connection = self.connect(mode)
                rows = [("quote'; --", 1), ('unicode中文', 2), ('nul\x00key', 3)]
                self.assertEqual(write_batch(connection, rows), 3)
                self.assertFalse(connection.in_transaction)
                self.assertEqual(set(self.records(connection)), set(rows))
                self.assertEqual(write_batch(connection, []), 0)
                self.assertFalse(connection.in_transaction)
                connection.rollback()
                self.assertEqual(set(self.records(connection)), set(rows))

    def test_repeated_failure_is_atomic(self):
        for mode in ('', None):
            with self.subTest(mode=mode):
                connection = self.connect(mode)
                write_batch(connection, [('old', 0)])
                for _ in range(3):
                    with self.assertRaises(sqlite3.IntegrityError):
                        write_batch(connection, [('new', 1), ('old', 2)])
                    self.assertEqual(self.records(connection), [('old', 0)])
                    self.assertFalse(connection.in_transaction)
                self.assertEqual(write_batch(connection, [('new', 3)]), 1)

    def test_outer_transactions_and_savepoints_remain_owned_by_caller(self):
        for mode in ('', None):
            for start in ('BEGIN', 'SAVEPOINT caller'):
                for finish in ('commit', 'rollback'):
                    with self.subTest(mode=mode, start=start, finish=finish):
                        connection = self.connect(mode)
                        connection.execute(start)
                        connection.execute("INSERT INTO records VALUES ('old', 0)")
                        connection.execute('SAVEPOINT inner_caller')
                        self.assertEqual(write_batch(connection, [('good', 1)]), 1)
                        self.assertEqual(write_batch(connection, []), 0)
                        for _ in range(2):
                            with self.assertRaises(sqlite3.IntegrityError):
                                write_batch(connection, [('bad', 2), ('old', 3)])
                        self.assertTrue(connection.in_transaction)
                        self.assertEqual(self.records(connection), [('good', 1), ('old', 0)])
                        connection.execute('ROLLBACK TO inner_caller')
                        connection.execute('RELEASE inner_caller')
                        self.assertEqual(self.records(connection), [('old', 0)])
                        write_batch(connection, [('final', 4)])
                        if start.startswith('SAVEPOINT'):
                            # RELEASE must still find the caller's outer savepoint.
                            connection.execute('SAVEPOINT probe')
                            connection.execute('RELEASE probe')
                            if finish == 'rollback':
                                connection.execute('ROLLBACK TO caller')
                            connection.execute('RELEASE caller')
                        else:
                            getattr(connection, finish)()
                        expected = [('final', 4), ('old', 0)] if finish == 'commit' else []
                        self.assertEqual(self.records(connection), expected)
                        self.assertFalse(connection.in_transaction)

    def test_default_implicit_outer_transaction(self):
        connection = self.connect()
        connection.execute("INSERT INTO records VALUES ('caller', 0)")
        write_batch(connection, [('batch', 1)])
        self.assertTrue(connection.in_transaction)
        connection.rollback()
        self.assertEqual(self.records(connection), [])

    def test_not_null_failure_rolls_back_prefix(self):
        connection = self.connect()
        with self.assertRaises(sqlite3.IntegrityError):
            write_batch(connection, [('prefix', 1), ('invalid', None)])
        self.assertEqual(self.records(connection), [])
        self.assertFalse(connection.in_transaction)

    def test_deferred_constraint_failure_on_release(self):
        for mode in ('', None):
            with self.subTest(mode=mode):
                connection = self.connect(mode)
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('DROP TABLE records')
                connection.execute('CREATE TABLE parents(id INTEGER PRIMARY KEY)')
                connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER REFERENCES parents(id) DEFERRABLE INITIALLY DEFERRED)')
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
                    write_batch(connection, [('invalid', 7)])
                self.assertEqual(self.records(connection), [])
                self.assertFalse(connection.in_transaction)
                connection.execute('INSERT INTO parents VALUES (7)')
                connection.commit()
                self.assertEqual(write_batch(connection, [('valid', 7)]), 1)

    def test_busy_commit_rolls_back_batch(self):
        with tempfile.TemporaryDirectory(dir='.') as directory:
            database = str(Path(directory) / 'busy.sqlite')
            writer = sqlite3.connect(database, timeout=0)
            reader = sqlite3.connect(database, timeout=0)
            try:
                writer.execute('PRAGMA journal_mode=DELETE')
                writer.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
                reader.execute('BEGIN')
                reader.execute('SELECT * FROM records').fetchall()
                with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                    write_batch(writer, [('batch', 1)])
                self.assertEqual(self.records(writer), [])
                self.assertFalse(writer.in_transaction)
                reader.rollback()
                self.assertEqual(write_batch(writer, [('next', 2)]), 1)
                self.assertEqual(self.records(reader), [('next', 2)])
            finally:
                reader.close()
                writer.close()

    def test_automatic_rollback_preserves_original_error(self):
        connection = self.connect()
        connection.execute("CREATE TRIGGER reject BEFORE INSERT ON records WHEN NEW.key='reject' BEGIN SELECT RAISE(ROLLBACK, 'original trigger failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'original trigger failure'):
            write_batch(connection, [('prefix', 1), ('reject', 2)])
        self.assertEqual(self.records(connection), [])
        self.assertFalse(connection.in_transaction)

    def test_cleanup_failure_preserves_exception_identity(self):
        original_error = RuntimeError('insertion interrupted')

        class FaultyConnection(sqlite3.Connection):
            def executemany(self, statement, parameters):
                raise original_error

            def execute(self, statement, parameters=()):
                if statement.startswith('ROLLBACK'):
                    raise sqlite3.OperationalError('cleanup unavailable')
                return super().execute(statement, parameters)

        for outer in (False, True):
            with self.subTest(outer=outer):
                connection = sqlite3.connect(':memory:', factory=FaultyConnection)
                self.addCleanup(connection.close)
                connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER)')
                if outer:
                    connection.execute('BEGIN')
                try:
                    write_batch(connection, [('key', 1)])
                except RuntimeError as error:
                    self.assertIs(error, original_error)
                else:
                    self.fail('original exception was not raised')
                # Fault injection deliberately makes transaction cleanup fail.
                connection.rollback()


if __name__ == '__main__':
    unittest.main(verbosity=2)
