"""Append a batch using a caller-supplied SQLite connection."""

from uuid import uuid4


def write_batch(connection, rows):
    count = len(rows)
    savepoint = 'write_batch_' + uuid4().hex
    connection.execute('SAVEPOINT ' + savepoint)
    try:
        connection.executemany('INSERT INTO records(key, value) VALUES (?, ?)', rows)
        # Releasing the outermost savepoint commits; nested release leaves
        # the caller's transaction and savepoints under the caller's control.
        connection.execute('RELEASE SAVEPOINT ' + savepoint)
    except BaseException:
        try:
            connection.execute('ROLLBACK TO SAVEPOINT ' + savepoint)
            connection.execute('RELEASE SAVEPOINT ' + savepoint)
        except BaseException:
            # Cleanup errors must not replace the original batch exception.
            pass
        raise
    return count
