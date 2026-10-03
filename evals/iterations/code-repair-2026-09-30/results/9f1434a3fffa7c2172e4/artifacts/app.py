"""Append a batch using a caller-supplied SQLite connection."""
import uuid


def write_batch(connection, rows):
    count = len(rows)
    name = 'batch_' + uuid.uuid4().hex
    connection.execute('SAVEPOINT ' + name)
    try:
        connection.executemany('INSERT INTO records(key, value) VALUES (?, ?)', rows)
        # Releasing the outermost savepoint commits and can itself fail.
        connection.execute('RELEASE ' + name)
    except BaseException:
        try:
            connection.execute('ROLLBACK TO ' + name)
            # Only release after rollback succeeds; never commit a failed batch.
            connection.execute('RELEASE ' + name)
        except BaseException:
            # SQLite may already have rolled back and removed this savepoint.
            # Cleanup must not replace the original exception.
            pass
        raise
    return count
