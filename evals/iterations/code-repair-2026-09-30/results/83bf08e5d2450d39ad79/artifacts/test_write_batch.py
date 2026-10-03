"""Behavioral regression checks; optional argument selects original or app."""
import importlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = sys.argv.pop(1) if len(sys.argv) > 1 else "app"
write_batch = importlib.import_module(MODULE).write_batch


class TrackingConnection(sqlite3.Connection):
    last_error = None
    fail_cleanup = False

    def executemany(self, *args, **kwargs):
        try:
            return super().executemany(*args, **kwargs)
        except BaseException as error:
            self.last_error = error
            raise

    def execute(self, sql, *args, **kwargs):
        if self.fail_cleanup and sql.startswith("ROLLBACK TO"):
            raise RuntimeError("injected cleanup failure")
        return super().execute(sql, *args, **kwargs)


class BatchTests(unittest.TestCase):
    def connection(self, isolation, filename=":memory:"):
        connection = sqlite3.connect(
            filename, isolation_level=isolation, factory=TrackingConnection
        )
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO records VALUES ('seed', 0)")
        connection.commit()
        return connection

    def records(self, connection):
        return dict(connection.execute("SELECT key, value FROM records"))

    def test_standalone_success_is_durable_and_connection_usable(self):
        for isolation in ("", None):
            with self.subTest(isolation=isolation), tempfile.TemporaryDirectory(dir=".") as directory:
                filename = str(Path(directory) / "records.sqlite")
                connection = self.connection(isolation, filename)
                rows = [("quote' ; DROP TABLE records; --", 1), ('中文\n"', 2)]
                self.assertEqual(write_batch(connection, rows), 2)
                self.assertFalse(connection.in_transaction)
                with sqlite3.connect(filename) as observer:
                    self.assertEqual(self.records(observer), dict([("seed", 0)] + rows))
                self.assertEqual(connection.execute("SELECT 1").fetchone(), (1,))

    def test_standalone_failure_is_atomic_repeatedly(self):
        for isolation in ("", None):
            with self.subTest(isolation=isolation):
                connection = self.connection(isolation)
                for _ in range(2):
                    with self.assertRaises(sqlite3.IntegrityError) as caught:
                        write_batch(connection, [("partial", 1), ("seed", 2)])
                    self.assertIs(caught.exception, connection.last_error)
                    self.assertEqual(self.records(connection), {"seed": 0})
                    self.assertFalse(connection.in_transaction)
                self.assertEqual(write_batch(connection, [("recovered", 3)]), 1)

    def test_outer_transactions_success_failure_and_empty(self):
        cases = [("", "implicit"), ("", "begin"), (None, "begin"),
                 ("", "savepoint"), (None, "savepoint")]
        for isolation, mode in cases:
            for outcome in ("success", "failure", "empty"):
                for finish in ("commit", "rollback"):
                    with self.subTest(isolation=isolation, mode=mode, outcome=outcome, finish=finish):
                        connection = self.connection(isolation)
                        if mode == "begin":
                            connection.execute("BEGIN")
                        elif mode == "savepoint":
                            connection.execute('SAVEPOINT "caller point"')
                        connection.execute("INSERT INTO records VALUES ('caller', 10)")
                        expected = {"seed": 0, "caller": 10}
                        if outcome == "failure":
                            for _ in range(2):
                                with self.assertRaises(sqlite3.IntegrityError) as caught:
                                    write_batch(connection, [("partial", 1), ("seed", 2)])
                                self.assertIs(caught.exception, connection.last_error)
                        else:
                            rows = [("batch", 20)] if outcome == "success" else []
                            self.assertEqual(write_batch(connection, rows), len(rows))
                            expected.update(rows)
                        self.assertTrue(connection.in_transaction)
                        self.assertEqual(self.records(connection), expected)
                        if mode == "savepoint":
                            if finish == "rollback":
                                connection.execute('ROLLBACK TO "caller point"')
                            connection.execute('RELEASE "caller point"')
                        elif finish == "commit":
                            connection.commit()
                        else:
                            connection.rollback()
                        self.assertEqual(self.records(connection), expected if finish == "commit" else {"seed": 0})
                        self.assertFalse(connection.in_transaction)

    def test_nested_caller_savepoints_remain_usable(self):
        for isolation in ("", None):
            with self.subTest(isolation=isolation):
                connection = self.connection(isolation)
                connection.execute("SAVEPOINT outer_caller")
                connection.execute("INSERT INTO records VALUES ('outer', 1)")
                connection.execute("SAVEPOINT inner_caller")
                connection.execute("INSERT INTO records VALUES ('inner', 2)")
                write_batch(connection, [("batch", 3)])
                connection.execute("ROLLBACK TO inner_caller")
                connection.execute("RELEASE inner_caller")
                self.assertEqual(self.records(connection), {"seed": 0, "outer": 1})
                connection.execute("ROLLBACK TO outer_caller")
                connection.execute("RELEASE outer_caller")
                self.assertEqual(self.records(connection), {"seed": 0})

    def test_empty_standalone(self):
        for isolation in ("", None):
            with self.subTest(isolation=isolation):
                connection = self.connection(isolation)
                self.assertEqual(write_batch(connection, []), 0)
                self.assertFalse(connection.in_transaction)
                self.assertEqual(self.records(connection), {"seed": 0})

    def test_bad_binding_and_not_null_failures_are_atomic(self):
        for isolation in ("", None):
            for bad_row, error_type in [(("bad", None), sqlite3.IntegrityError),
                                        (("missing",), sqlite3.ProgrammingError)]:
                with self.subTest(isolation=isolation, bad_row=bad_row):
                    connection = self.connection(isolation)
                    with self.assertRaises(error_type) as caught:
                        write_batch(connection, [("partial", 1), bad_row])
                    self.assertIs(caught.exception, connection.last_error)
                    self.assertEqual(self.records(connection), {"seed": 0})

    def test_release_failure_from_deferred_constraint(self):
        connection = self.connection(None)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        connection.execute("DROP TABLE records")
        connection.execute("CREATE TABLE records(key TEXT PRIMARY KEY, value INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")
        with self.assertRaises(sqlite3.IntegrityError):
            write_batch(connection, [("invalid", 42)])
        self.assertEqual(self.records(connection), {})
        self.assertFalse(connection.in_transaction)

    def test_cleanup_error_does_not_replace_original(self):
        connection = self.connection("")
        connection.fail_cleanup = True
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            write_batch(connection, [("partial", 1), ("seed", 2)])
        self.assertIs(caught.exception, connection.last_error)
        connection.rollback()


if __name__ == "__main__":
    unittest.main()
