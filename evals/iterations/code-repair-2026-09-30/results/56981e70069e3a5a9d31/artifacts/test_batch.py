import importlib
import os
import sqlite3
import tempfile
import unittest


write_batch = importlib.import_module(os.environ.get('BATCH_MODULE', 'app')).write_batch


class BatchTests(unittest.TestCase):
    def connect(self, isolation='', path=':memory:', schema=None):
        connection = sqlite3.connect(path, isolation_level=isolation, timeout=0)
        self.addCleanup(connection.close)
        connection.execute(schema or 'CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        return connection

    def snapshot(self, connection):
        return connection.execute('SELECT key, value FROM records ORDER BY key').fetchall()

    def begin_caller(self, connection, mode):
        if mode == 'begin':
            connection.execute('BEGIN')
        elif mode == 'savepoint':
            connection.execute('SAVEPOINT "caller point"')
        if mode != 'none':
            connection.execute('INSERT INTO records VALUES (?, ?)', ('caller', 7))
            connection.execute('SAVEPOINT "nested caller"')

    def cases(self):
        for isolation in ('', None):
            for mode in ('none', 'begin', 'savepoint', 'implicit'):
                if isolation is None and mode == 'implicit':
                    continue
                yield isolation, mode

    def finish_caller(self, connection, mode, decision):
        if mode == 'savepoint':
            connection.execute('RELEASE "caller point"') if decision == 'commit' else connection.rollback()
        else:
            getattr(connection, decision)()

    def test_success_and_caller_final_decision(self):
        batch = [("'; DROP TABLE records; --", 1), ('引号"\x00\n', 2)]
        for isolation, mode in self.cases():
            for decision in ('commit', 'rollback'):
                with self.subTest(isolation=isolation, mode=mode, decision=decision):
                    c = self.connect(isolation)
                    self.begin_caller(c, mode)
                    self.assertEqual(write_batch(c, batch), 2)
                    self.assertEqual(c.in_transaction, mode != 'none')
                    if mode != 'none':
                        c.execute('RELEASE "nested caller"')
                    self.finish_caller(c, mode, decision)
                    expected = batch if mode == 'none' else (batch + [('caller', 7)] if decision == 'commit' else [])
                    self.assertEqual(self.snapshot(c), sorted(expected))
                    c.execute('SELECT 1')

    def test_caller_can_rollback_to_both_savepoints(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation)
                self.begin_caller(c, 'savepoint')
                self.assertEqual(write_batch(c, [('batch', 1)]), 1)
                c.execute('ROLLBACK TO "nested caller"')
                self.assertEqual(self.snapshot(c), [('caller', 7)])
                c.execute('RELEASE "nested caller"')
                c.execute('ROLLBACK TO "caller point"')
                self.assertEqual(self.snapshot(c), [])
                c.execute('RELEASE "caller point"')
                self.assertFalse(c.in_transaction)

    def test_repeated_partial_failure_is_atomic(self):
        for isolation, mode in self.cases():
            with self.subTest(isolation=isolation, mode=mode):
                c = self.connect(isolation)
                self.begin_caller(c, mode)
                before = self.snapshot(c)
                for _ in range(3):
                    with self.assertRaises(sqlite3.IntegrityError) as caught:
                        write_batch(c, [('duplicate', 1), ('duplicate', 2)])
                    self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY)
                    self.assertEqual(self.snapshot(c), before)
                    self.assertEqual(c.in_transaction, mode != 'none')
                if mode != 'none':
                    c.execute('ROLLBACK TO "nested caller"')
                    c.execute('RELEASE "nested caller"')
                    self.finish_caller(c, mode, 'commit')
                    self.assertEqual(self.snapshot(c), [('caller', 7)])
                self.assertEqual(write_batch(c, [('recovered', 3)]), 1)

    def test_empty_batch(self):
        for isolation, mode in self.cases():
            with self.subTest(isolation=isolation, mode=mode):
                c = self.connect(isolation)
                self.begin_caller(c, mode)
                before = self.snapshot(c)
                self.assertEqual(write_batch(c, ()), 0)
                self.assertEqual(self.snapshot(c), before)
                self.assertEqual(c.in_transaction, mode != 'none')
                if mode != 'none':
                    c.execute('ROLLBACK TO "nested caller"')
                    c.execute('RELEASE "nested caller"')

    def test_standalone_commit_is_visible_to_another_connection(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation), tempfile.TemporaryDirectory(dir='.') as directory:
                path = os.path.join(directory, 'records.sqlite')
                c = self.connect(isolation, path)
                other = sqlite3.connect(path, timeout=0)
                try:
                    self.assertEqual(write_batch(c, [('visible', 8)]), 1)
                    self.assertEqual(self.snapshot(other), [('visible', 8)])
                    c.execute('BEGIN')
                    self.assertEqual(write_batch(c, [('pending', 9)]), 1)
                    self.assertEqual(self.snapshot(other), [('visible', 8)])
                    c.rollback()
                finally:
                    other.close()
                    c.close()

    def foreign_key_connection(self, isolation):
        c = sqlite3.connect(':memory:', isolation_level=isolation)
        self.addCleanup(c.close)
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('CREATE TABLE parents(id INTEGER PRIMARY KEY)')
        c.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER REFERENCES parents(id) DEFERRABLE INITIALLY DEFERRED)')
        return c

    def test_deferred_commit_failure_rolls_back(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.foreign_key_connection(isolation)
                for _ in range(2):
                    with self.assertRaises(sqlite3.IntegrityError) as caught:
                        write_batch(c, [('orphan', 99)])
                    self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY)
                    self.assertEqual(self.snapshot(c), [])
                    self.assertFalse(c.in_transaction)

    def test_nested_deferred_constraint_stays_with_caller(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.foreign_key_connection(isolation)
                c.execute('BEGIN')
                c.execute('SAVEPOINT caller')
                self.assertEqual(write_batch(c, [('pending', 99)]), 1)
                self.assertTrue(c.in_transaction)
                c.execute('INSERT INTO parents VALUES (99)')
                c.execute('RELEASE caller')
                c.commit()
                self.assertEqual(self.snapshot(c), [('pending', 99)])

    def test_busy_commit_failure_rolls_back(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation), tempfile.TemporaryDirectory(dir='.') as directory:
                path = os.path.join(directory, 'records.sqlite')
                c = self.connect(isolation, path)
                reader = sqlite3.connect(path, timeout=0)
                try:
                    reader.execute('BEGIN')
                    reader.execute('SELECT * FROM records').fetchall()
                    with self.assertRaises(sqlite3.OperationalError) as caught:
                        write_batch(c, [('locked', 1)])
                    self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)
                    self.assertEqual(self.snapshot(c), [])
                    self.assertFalse(c.in_transaction)
                    reader.rollback()
                    self.assertEqual(write_batch(c, [('retry', 2)]), 1)
                finally:
                    reader.close()
                    c.close()

    def test_sqlite_automatic_rollback_preserves_original_error(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation, schema='CREATE TABLE records(key TEXT PRIMARY KEY ON CONFLICT ROLLBACK, value INTEGER NOT NULL)')
                with self.assertRaises(sqlite3.IntegrityError) as caught:
                    write_batch(c, [('same', 1), ('same', 2)])
                self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY)
                self.assertEqual(self.snapshot(c), [])
                self.assertFalse(c.in_transaction)

    def test_exception_identity_and_base_exception(self):
        class StopBatch(BaseException):
            pass

        error = StopBatch('original failure')

        class FailingRows:
            def __len__(self):
                return 2

            def __iter__(self):
                yield ('partial', 1)
                raise error

        for isolation, mode in self.cases():
            with self.subTest(isolation=isolation, mode=mode):
                c = self.connect(isolation)
                self.begin_caller(c, mode)
                before = self.snapshot(c)
                with self.assertRaises(StopBatch) as caught:
                    write_batch(c, FailingRows())
                self.assertIs(caught.exception, error)
                self.assertEqual(self.snapshot(c), before)
                self.assertEqual(c.in_transaction, mode != 'none')


if __name__ == '__main__':
    unittest.main(verbosity=2)
