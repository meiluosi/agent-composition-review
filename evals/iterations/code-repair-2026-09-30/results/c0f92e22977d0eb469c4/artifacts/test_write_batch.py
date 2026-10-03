import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import write_batch


class BatchTests(unittest.TestCase):
    def connect(self, isolation_level=''):
        connection = sqlite3.connect(':memory:', isolation_level=isolation_level)
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY,value INTEGER NOT NULL)')
        return connection

    def contents(self, connection):
        return connection.execute('SELECT key,value FROM records ORDER BY key').fetchall()

    def begin_outer(self, connection, kind):
        if kind == 'implicit':
            connection.execute("INSERT INTO records VALUES ('caller', 1)")
        else:
            connection.execute('BEGIN' if kind == 'begin' else 'SAVEPOINT caller_root')
            connection.execute("INSERT INTO records VALUES ('caller', 1)")
        # Deliberate name collision: release/rollback must target only the newest savepoint.
        connection.execute('SAVEPOINT _write_batch')
        connection.execute("INSERT INTO records VALUES ('saved', 2)")
        connection.execute('SAVEPOINT caller_inner')

    def test_standalone_matrix(self):
        for isolation in ('', None):
            for rows, failure in (([], False), ([('a', 1), ('b', 2)], False),
                                  ([('a', 1), ('a', 2)], True),
                                  ([('a', 1), ('b', None)], True)):
                with self.subTest(isolation=isolation, rows=rows):
                    c = self.connect(isolation)
                    if failure:
                        with self.assertRaises(sqlite3.IntegrityError):
                            write_batch(c, rows)
                        self.assertEqual(self.contents(c), [])
                    else:
                        self.assertEqual(write_batch(c, rows), len(rows))
                        self.assertEqual(self.contents(c), rows)
                    self.assertFalse(c.in_transaction)
                    c.execute('SELECT 1')

    def test_outer_matrix(self):
        for isolation, kinds in (('', ('implicit', 'begin', 'savepoint')),
                                 (None, ('begin', 'savepoint'))):
            for kind in kinds:
                for rows, failure in (([], False), ([('batch', 3)], False),
                                      ([('batch', 3), ('caller', 4)], True),
                                      ([('batch', 3), ('batch', 4)], True)):
                    for finish in ('commit', 'rollback'):
                        with self.subTest(isolation=isolation, kind=kind, rows=rows, finish=finish):
                            c = self.connect(isolation)
                            self.begin_outer(c, kind)
                            if failure:
                                # A second failure must also leave every caller savepoint alive.
                                for _ in range(2):
                                    with self.assertRaises(sqlite3.IntegrityError):
                                        write_batch(c, rows)
                            else:
                                self.assertEqual(write_batch(c, rows), len(rows))
                            expected = [('caller', 1), ('saved', 2)]
                            if not failure:
                                expected += rows
                            self.assertEqual(self.contents(c), sorted(expected))
                            self.assertTrue(c.in_transaction)
                            c.execute('ROLLBACK TO caller_inner')
                            c.execute('RELEASE caller_inner')
                            self.assertEqual(self.contents(c), [('caller', 1), ('saved', 2)])
                            c.execute('ROLLBACK TO _write_batch')
                            c.execute('RELEASE _write_batch')
                            self.assertEqual(self.contents(c), [('caller', 1)])
                            if kind == 'savepoint':
                                c.execute('RELEASE caller_root' if finish == 'commit' else 'ROLLBACK TO caller_root')
                                if finish == 'rollback':
                                    c.execute('RELEASE caller_root')
                            else:
                                getattr(c, finish)()
                            self.assertEqual(self.contents(c), [('caller', 1)] if finish == 'commit' else [])
                            self.assertFalse(c.in_transaction)

    def test_special_keys_and_reuse(self):
        c = self.connect(None)
        rows = [("x'); DROP TABLE records; --", 1), ('引号\"\'\\\n\x00', 2), ('', 3)]
        self.assertEqual(write_batch(c, rows), 3)
        self.assertEqual(self.contents(c), sorted(rows))
        for _ in range(2):
            with self.assertRaises(sqlite3.IntegrityError):
                write_batch(c, [('new', 4), (rows[0][0], 5)])
            self.assertEqual(self.contents(c), sorted(rows))
        self.assertEqual(write_batch(c, [('new', 4)]), 1)

    def test_original_exception_identity(self):
        class Interrupted(BaseException):
            pass

        for error in (RuntimeError('iteration failed'), Interrupted('interrupted')):
            for isolation in ('', None):
                with self.subTest(error=type(error).__name__, isolation=isolation):
                    class Rows(list):
                        def __iter__(self):
                            yield ('batch', 2)
                            raise error

                    c = self.connect(isolation)
                    self.begin_outer(c, 'begin')
                    try:
                        write_batch(c, Rows([('batch', 2), ('unused', 3)]))
                    except BaseException as actual:
                        self.assertIs(actual, error)
                    else:
                        self.fail('exception was swallowed')
                    self.assertEqual(self.contents(c), [('caller', 1), ('saved', 2)])
                    c.execute('RELEASE caller_inner')
                    c.rollback()

    def test_deferred_constraint_on_release(self):
        c = self.connect(None)
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('CREATE TABLE parents(id INTEGER PRIMARY KEY)')
        c.execute('DROP TABLE records')
        c.execute('CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER REFERENCES parents(id) DEFERRABLE INITIALLY DEFERRED)')
        with self.assertRaises(sqlite3.IntegrityError):
            write_batch(c, [('bad', 99)])
        self.assertEqual(self.contents(c), [])
        self.assertFalse(c.in_transaction)

    def test_caller_finishes_successful_batch(self):
        for isolation in ('', None):
            for finish in ('commit', 'rollback'):
                with self.subTest(isolation=isolation, finish=finish):
                    c = self.connect(isolation)
                    c.execute('BEGIN')
                    c.execute("INSERT INTO records VALUES ('caller', 1)")
                    self.assertEqual(write_batch(c, [('batch', 2)]), 1)
                    self.assertTrue(c.in_transaction)
                    getattr(c, finish)()
                    expected = [('batch', 2), ('caller', 1)] if finish == 'commit' else []
                    self.assertEqual(self.contents(c), expected)

    def test_cleanup_failure_keeps_original_error(self):
        c = self.connect(None)

        def authorizer(action, operation, name, database, source):
            if action == sqlite3.SQLITE_SAVEPOINT and operation == 'ROLLBACK':
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        c.set_authorizer(authorizer)
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            write_batch(c, [('a', 1), ('a', 2)])
        self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY)
        # Denied rollback is outside recoverable transaction semantics; do not
        # accidentally commit that partial transaction while handling its error.
        self.assertTrue(c.in_transaction)
        c.set_authorizer(None)
        c.rollback()
        self.assertEqual(self.contents(c), [])

    def test_commit_visible_to_other_connection(self):
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'records.db'
            for isolation in ('', None):
                c = sqlite3.connect(path, isolation_level=isolation)
                observer = sqlite3.connect(path)
                try:
                    c.execute('CREATE TABLE IF NOT EXISTS records(key TEXT PRIMARY KEY,value INTEGER NOT NULL)')
                    key = repr(isolation)
                    self.assertEqual(write_batch(c, [(key, 1)]), 1)
                    self.assertEqual(observer.execute('SELECT value FROM records WHERE key=?', (key,)).fetchone(), (1,))
                    c.execute('BEGIN')
                    self.assertEqual(write_batch(c, [('uncommitted', 2)]), 1)
                    self.assertIsNone(observer.execute("SELECT value FROM records WHERE key='uncommitted'").fetchone())
                    c.rollback()
                finally:
                    observer.close()
                    c.close()


if __name__ == '__main__':
    unittest.main()
