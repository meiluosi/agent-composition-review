import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import write_batch


def snapshot(connection):
    return connection.execute('SELECT key, value FROM records ORDER BY key').fetchall()


class BatchTests(unittest.TestCase):
    def connect(self, isolation):
        connection = sqlite3.connect(':memory:', isolation_level=isolation)
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY,value INTEGER NOT NULL)')
        connection.execute("INSERT INTO records VALUES ('committed', 0)")
        connection.commit()
        return connection

    def test_transaction_matrix(self):
        cases = {
            'empty': ([], False),
            'success': ([("quote' ; -- 雪\x00", 1), ('second', 2)], False),
            'duplicate': ([('partial', 1), ('partial', 2)], True),
            'existing': ([('partial', 1), ('committed', 2)], True),
            'null': ([('partial', 1), ('invalid', None)], True),
        }
        for isolation in ('', None):
            contexts = ['none', 'begin', 'savepoint']
            if isolation == '':
                contexts.append('implicit')
            for context in contexts:
                for case, (rows, fails) in cases.items():
                    for final_action in ('commit', 'rollback'):
                        with self.subTest(isolation=isolation, context=context,
                                          case=case, final_action=final_action):
                            connection = self.connect(isolation)
                            outer = context != 'none'
                            if context == 'begin':
                                connection.execute('BEGIN')
                            elif context == 'savepoint':
                                connection.execute('SAVEPOINT caller_root')
                            if outer:
                                connection.execute("INSERT INTO records VALUES ('caller', 9)")
                                connection.execute('SAVEPOINT write_batch')
                                connection.execute('SAVEPOINT caller_nested')
                            before = snapshot(connection)
                            if fails:
                                # Repeated failures must leave the same usable boundary.
                                for _ in range(2):
                                    with self.assertRaises(sqlite3.IntegrityError):
                                        write_batch(connection, rows)
                                    self.assertEqual(snapshot(connection), before)
                                    self.assertEqual(connection.in_transaction, outer)
                                expected = before
                            else:
                                self.assertEqual(write_batch(connection, rows), len(rows))
                                expected = sorted(before + rows)
                                self.assertEqual(snapshot(connection), expected)
                                self.assertEqual(connection.in_transaction, outer)
                            if outer:
                                # RELEASE proves that both caller savepoints still exist.
                                connection.execute('RELEASE caller_nested')
                                connection.execute('RELEASE write_batch')
                            getattr(connection, final_action)()
                            final_rows = [('committed', 0)] if outer and final_action == 'rollback' else expected
                            self.assertEqual(snapshot(connection), final_rows)
                            self.assertFalse(connection.in_transaction)
                            # The original connection remains usable after either outcome.
                            self.assertEqual(write_batch(connection, [('later', 7)]), 1)
                            self.assertFalse(connection.in_transaction)

    def test_caller_can_rollback_to_savepoint(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                connection = self.connect(isolation)
                connection.execute('SAVEPOINT caller_root')
                connection.execute("INSERT INTO records VALUES ('caller', 9)")
                connection.execute('SAVEPOINT caller_inner')
                self.assertEqual(write_batch(connection, [('batch', 1)]), 1)
                connection.execute('ROLLBACK TO caller_inner')
                self.assertEqual(snapshot(connection), [('caller', 9), ('committed', 0)])
                connection.execute('RELEASE caller_inner')
                connection.execute('ROLLBACK TO caller_root')
                self.assertEqual(snapshot(connection), [('committed', 0)])
                connection.execute('RELEASE caller_root')
                self.assertFalse(connection.in_transaction)

    def test_partial_iteration_preserves_original_exception(self):
        class SourceFailure(Exception):
            pass

        error = SourceFailure('source interrupted')

        class BrokenRows:
            def __len__(self):
                return 2

            def __iter__(self):
                yield ('partial', 1)
                raise error

        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                connection = self.connect(isolation)
                with self.assertRaises(SourceFailure) as caught:
                    write_batch(connection, BrokenRows())
                self.assertIs(caught.exception, error)
                self.assertEqual(snapshot(connection), [('committed', 0)])
                self.assertFalse(connection.in_transaction)

    def test_cleanup_error_does_not_replace_original_exception(self):
        error = ValueError('original batch failure')

        class FailingCleanup:
            def execute(self, statement):
                if statement.startswith('ROLLBACK'):
                    raise RuntimeError('cleanup failed')

            def executemany(self, statement, rows):
                raise error

        with self.assertRaises(ValueError) as caught:
            write_batch(FailingCleanup(), [('key', 1)])
        self.assertIs(caught.exception, error)

    def test_release_failure_rolls_back_batch(self):
        error = RuntimeError('release failed')
        connection = self.connect('')

        class FailingRelease:
            failed = False

            def execute(self, statement):
                if statement.startswith('RELEASE') and not self.failed:
                    self.failed = True
                    raise error
                return connection.execute(statement)

            def executemany(self, statement, rows):
                return connection.executemany(statement, rows)

        with self.assertRaises(RuntimeError) as caught:
            write_batch(FailingRelease(), [('partial', 1)])
        self.assertIs(caught.exception, error)
        self.assertEqual(snapshot(connection), [('committed', 0)])
        self.assertFalse(connection.in_transaction)

    def test_deferred_constraint_failure_at_release(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                connection = sqlite3.connect(':memory:', isolation_level=isolation)
                self.addCleanup(connection.close)
                connection.execute('PRAGMA foreign_keys = ON')
                connection.execute('CREATE TABLE parent(id INTEGER PRIMARY KEY)')
                connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY, '
                                   'value INTEGER REFERENCES parent(id) '
                                   'DEFERRABLE INITIALLY DEFERRED)')
                with self.assertRaises(sqlite3.IntegrityError):
                    write_batch(connection, [('invalid', 42)])
                self.assertEqual(snapshot(connection), [])
                self.assertFalse(connection.in_transaction)

    def test_commit_visible_to_second_connection(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation), tempfile.TemporaryDirectory(dir='.') as directory:
                path = Path(directory) / 'records.sqlite'
                connection = sqlite3.connect(path, isolation_level=isolation)
                observer = sqlite3.connect(path)
                try:
                    connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY,value INTEGER NOT NULL)')
                    self.assertEqual(write_batch(connection, [('committed', 0)]), 1)
                    self.assertEqual(snapshot(observer), [('committed', 0)])
                    connection.execute('BEGIN')
                    connection.execute("INSERT INTO records VALUES ('caller', 9)")
                    self.assertEqual(write_batch(connection, [('batch', 1)]), 1)
                    self.assertEqual(snapshot(observer), [('committed', 0)])
                    connection.commit()
                    self.assertEqual(snapshot(observer), [('batch', 1), ('caller', 9), ('committed', 0)])
                finally:
                    observer.close()
                    connection.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
