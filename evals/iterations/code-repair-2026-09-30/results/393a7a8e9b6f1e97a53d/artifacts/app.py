"""Append a batch using a caller-supplied SQLite connection."""

import uuid


def write_batch(connection, rows):
    # An outermost savepoint commits on release; a nested one leaves the
    # caller's transaction and savepoints intact.
    savepoint = 'batch_' + uuid.uuid4().hex
    connection.execute('SAVEPOINT ' + savepoint)
    try:
        connection.executemany('INSERT INTO records(key, value) VALUES (?, ?)', rows)
        count = len(rows)
        connection.execute('RELEASE SAVEPOINT ' + savepoint)
    except BaseException:
        try:
            connection.execute('ROLLBACK TO SAVEPOINT ' + savepoint)
            connection.execute('RELEASE SAVEPOINT ' + savepoint)
        except BaseException:
            # Cleanup failures must not replace the original batch exception.
            pass
        raise
    return count
