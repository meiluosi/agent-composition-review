import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import write_batch


class BatchTests(unittest.TestCase):
    def connect(self, isolation_level='', path=':memory:', factory=sqlite3.Connection):
        connection = sqlite3.connect(path, isolation_level=isolation_level, factory=factory)
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        return connection

    def contents(self, connection):
        return connection.execute('SELECT key, value FROM records ORDER BY key').fetchall()

    def test_transaction_matrix(self):
        for isolation in ('', None):
            for outer in ('none', 'begin', 'savepoint', 'nested'):
                for batch in ('success', 'empty', 'duplicate', 'null'):
                    with self.subTest(isolation=isolation, outer=outer, batch=batch):
                        c = self.connect(isolation)
                        if outer in ('begin', 'nested'):
                            c.execute('BEGIN')
                        if outer == 'savepoint':
                            c.execute('SAVEPOINT caller_root')
                        if outer != 'none':
                            c.execute("INSERT INTO records VALUES ('caller', 1)")
                            c.execute('SAVEPOINT caller_inner')
                        before = self.contents(c)
                        rows = {
                            'success': [('batch', 2), ("quote' ; -- 雪\x00", 3)],
                            'empty': [],
                            'duplicate': [('batch', 2), ('batch', 3)],
                            'null': [('batch', 2), ('bad', None)],
                        }[batch]
                        failed = batch in ('duplicate', 'null')
                        # Consecutive failures must not accumulate inserted rows or lose savepoints.
                        for _ in range(3 if failed else 1):
                            if failed:
                                with self.assertRaises(sqlite3.IntegrityError):
                                    write_batch(c, rows)
                                self.assertEqual(self.contents(c), before)
                            else:
                                self.assertEqual(write_batch(c, rows), len(rows))
                                self.assertEqual(self.contents(c), sorted(before + rows))
                            self.assertEqual(c.in_transaction, outer != 'none')
                        if outer != 'none':
                            c.execute('ROLLBACK TO caller_inner')
                            self.assertEqual(self.contents(c), before)
                            c.execute('RELEASE caller_inner')
                            if outer == 'savepoint':
                                c.execute('ROLLBACK TO caller_root')
                                self.assertEqual(self.contents(c), [])
                                c.execute('RELEASE caller_root')
                            else:
                                c.rollback()
                                self.assertEqual(self.contents(c), [])
                        # The supplied connection remains usable.
                        self.assertEqual(write_batch(c, [('later', 8)]), 1)
                        self.assertFalse(c.in_transaction)

    def test_durable_commit_and_caller_control(self):
        for isolation in ('', None):
            for completion in ('standalone', 'commit', 'rollback'):
                with self.subTest(isolation=isolation, completion=completion):
                    with tempfile.TemporaryDirectory(dir='.') as folder:
                        path = str(Path(folder) / 'records.sqlite')
                        c = self.connect(isolation, path)
                        observer = sqlite3.connect(path)
                        try:
                            if completion != 'standalone':
                                c.execute('BEGIN')
                                c.execute("INSERT INTO records VALUES ('caller', 1)")
                            write_batch(c, [('batch', 2)])
                            if completion == 'standalone':
                                self.assertEqual(self.contents(observer), [('batch', 2)])
                            else:
                                self.assertEqual(self.contents(observer), [])
                                getattr(c, completion)()
                                expected = [('batch', 2), ('caller', 1)] if completion == 'commit' else []
                                self.assertEqual(self.contents(observer), expected)
                        finally:
                            observer.close()
                            c.close()

    def test_implicit_outer_transaction(self):
        c = self.connect()
        c.execute("INSERT INTO records VALUES ('caller', 1)")
        write_batch(c, [('batch', 2)])
        self.assertTrue(c.in_transaction)
        c.rollback()
        self.assertEqual(self.contents(c), [])

    def test_original_exception_identity_and_base_exception(self):
        for isolation in ('', None):
            for error in (RuntimeError('iterator failed'), KeyboardInterrupt('interrupted')):
                with self.subTest(isolation=isolation, error=type(error).__name__):
                    class BrokenRows(list):
                        def __iter__(self):
                            yield ('partial', 1)
                            raise error

                    c = self.connect(isolation)
                    with self.assertRaises(type(error)) as caught:
                        write_batch(c, BrokenRows([('partial', 1), ('unused', 2)]))
                    self.assertIs(caught.exception, error)
                    self.assertEqual(self.contents(c), [])
                    self.assertFalse(c.in_transaction)

    def test_deferred_constraint_at_commit(self):
        for isolation in ('', None):
            with self.subTest(isolation=isolation):
                c = self.connect(isolation)
                c.execute('PRAGMA foreign_keys=ON')
                c.execute('CREATE TABLE parent(id INTEGER PRIMARY KEY)')
                c.execute('DROP TABLE records')
                c.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)')
                with self.assertRaises(sqlite3.IntegrityError):
                    write_batch(c, [('invalid', 9)])
                self.assertEqual(self.contents(c), [])
                self.assertFalse(c.in_transaction)

    def test_cleanup_exception_does_not_replace_original(self):
        original = sqlite3.IntegrityError('original')

        class FaultyConnection(sqlite3.Connection):
            def executemany(self, sql, rows):
                raise original

            def execute(self, sql, *args):
                if sql.startswith('ROLLBACK TO'):
                    raise sqlite3.OperationalError('cleanup failed')
                return super().execute(sql, *args)

        c = self.connect(factory=FaultyConnection)
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            write_batch(c, [('unused', 1)])
        self.assertIs(caught.exception, original)
        c.rollback()


if __name__ == '__main__':
    unittest.main(verbosity=2)
