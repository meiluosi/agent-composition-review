import sqlite3
import unittest

from app import write_batch


class BatchTests(unittest.TestCase):
    def connection(self, isolation_level=''):
        connection = sqlite3.connect(':memory:', isolation_level=isolation_level)
        self.addCleanup(connection.close)
        connection.execute(
            'CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)'
        )
        return connection

    def contents(self, connection):
        return connection.execute('SELECT key, value FROM records ORDER BY key').fetchall()

    def test_standalone_commit_and_repeated_failures(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                connection = self.connection(isolation)
                special = "quote' ; -- \" 雪\n\x00"
                self.assertEqual(write_batch(connection, [(special, 7)]), 1)
                self.assertFalse(connection.in_transaction)
                connection.rollback()
                self.assertEqual(self.contents(connection), [(special, 7)])
                for _ in range(2):
                    with self.assertRaises(sqlite3.IntegrityError):
                        write_batch(connection, [('partial', 2), (special, 3)])
                    self.assertEqual(self.contents(connection), [(special, 7)])
                    self.assertFalse(connection.in_transaction)
                self.assertEqual(write_batch(connection, []), 0)
                self.assertFalse(connection.in_transaction)
                self.assertEqual(write_batch(connection, [('later', 4)]), 1)

    def test_caller_transaction_can_commit_or_rollback(self):
        for isolation in ('', None):
            for finish in ('commit', 'rollback'):
                with self.subTest(isolation=isolation, finish=finish):
                    connection = self.connection(isolation)
                    connection.execute('BEGIN')
                    connection.execute("INSERT INTO records VALUES ('caller', 1)")
                    self.assertEqual(write_batch(connection, [('batch', 2)]), 1)
                    for _ in range(2):
                        with self.assertRaises(sqlite3.IntegrityError):
                            write_batch(connection, [('partial', 3), ('caller', 4)])
                    self.assertEqual(write_batch(connection, []), 0)
                    self.assertTrue(connection.in_transaction)
                    self.assertEqual(self.contents(connection), [('batch', 2), ('caller', 1)])
                    getattr(connection, finish)()
                    expected = [('batch', 2), ('caller', 1)] if finish == 'commit' else []
                    self.assertEqual(self.contents(connection), expected)

    def test_implicit_default_transaction(self):
        connection = self.connection()
        connection.execute("INSERT INTO records VALUES ('caller', 1)")
        self.assertEqual(write_batch(connection, [('batch', 2)]), 1)
        self.assertTrue(connection.in_transaction)
        connection.rollback()
        self.assertEqual(self.contents(connection), [])

    def test_caller_savepoints_survive(self):
        for isolation in ('', None):
            for explicit_begin in (False, True):
                with self.subTest(isolation=isolation, explicit_begin=explicit_begin):
                    connection = self.connection(isolation)
                    if explicit_begin:
                        connection.execute('BEGIN')
                    connection.execute('SAVEPOINT caller_outer')
                    connection.execute("INSERT INTO records VALUES ('caller', 1)")
                    connection.execute('SAVEPOINT caller_inner')
                    self.assertEqual(write_batch(connection, [('batch', 2)]), 1)
                    for _ in range(2):
                        with self.assertRaises(sqlite3.IntegrityError):
                            write_batch(connection, [('partial', 3), ('caller', 4)])
                    self.assertEqual(write_batch(connection, []), 0)
                    connection.execute('ROLLBACK TO caller_inner')
                    self.assertEqual(self.contents(connection), [('caller', 1)])
                    connection.execute('RELEASE caller_inner')
                    connection.execute('RELEASE caller_outer')
                    if explicit_begin:
                        self.assertTrue(connection.in_transaction)
                        connection.commit()
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(self.contents(connection), [('caller', 1)])

    def test_deferred_constraint_release_failure_rolls_back(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                connection = self.connection(isolation)
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('CREATE TABLE parents(id INTEGER PRIMARY KEY)')
                connection.execute('DROP TABLE records')
                connection.execute(
                    'CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER '
                    'REFERENCES parents(id) DEFERRABLE INITIALLY DEFERRED)'
                )
                for _ in range(2):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
                        write_batch(connection, [('orphan', 99)])
                    self.assertEqual(self.contents(connection), [])
                    self.assertFalse(connection.in_transaction)
                connection.execute('INSERT INTO parents VALUES (99)')
                connection.commit()
                self.assertEqual(write_batch(connection, [('valid', 99)]), 1)

    def test_automatic_sqlite_rollback_preserves_original_exception(self):
        connection = self.connection()
        connection.execute(
            "CREATE TRIGGER reject BEFORE INSERT ON records WHEN NEW.key = 'reject' "
            "BEGIN SELECT RAISE(ROLLBACK, 'original rejection'); END"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'original rejection'):
            write_batch(connection, [('partial', 1), ('reject', 2)])
        self.assertEqual(self.contents(connection), [])
        self.assertFalse(connection.in_transaction)

    def test_cleanup_failure_keeps_exception_identity_and_does_not_release(self):
        class FaultConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith('ROLLBACK TO '):
                    raise RuntimeError('cleanup unavailable')
                return super().execute(sql, *args)

            def executemany(self, sql, rows):
                try:
                    return super().executemany(sql, rows)
                except BaseException as error:
                    self.original_error = error
                    raise

        connection = sqlite3.connect(':memory:', factory=FaultConnection)
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER)')
        statements = []
        connection.set_trace_callback(statements.append)
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            write_batch(connection, [('duplicate', 1), ('duplicate', 2)])
        self.assertIs(caught.exception, connection.original_error)
        self.assertFalse(any(sql.startswith('RELEASE ') for sql in statements))
        self.assertTrue(connection.in_transaction)
        # A connection that rejects rollback cannot provide atomic cleanup.
        connection.rollback()
        self.assertEqual(self.contents(connection), [])

    def test_base_exception_during_iteration_rolls_back_batch(self):
        interruption = KeyboardInterrupt('interrupted input')

        class InterruptedRows(list):
            def __iter__(self):
                yield ('partial', 2)
                raise interruption

        connection = self.connection()
        connection.execute("INSERT INTO records VALUES ('caller', 1)")
        with self.assertRaises(KeyboardInterrupt) as caught:
            write_batch(connection, InterruptedRows([('partial', 2), ('next', 3)]))
        self.assertIs(caught.exception, interruption)
        self.assertEqual(self.contents(connection), [('caller', 1)])
        self.assertTrue(connection.in_transaction)
        connection.commit()


if __name__ == '__main__':
    unittest.main(verbosity=2)
