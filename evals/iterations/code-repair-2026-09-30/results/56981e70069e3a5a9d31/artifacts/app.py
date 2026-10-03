"""Append a batch using a caller-supplied SQLite connection."""
import uuid


def write_batch(connection, rows):
    count = len(rows)
    had_transaction = connection.in_transaction
    name = 'batch_' + uuid.uuid4().hex
    connection.execute('SAVEPOINT ' + name)
    try:
        connection.executemany('INSERT INTO records(key, value) VALUES (?, ?)', rows)
        # Releasing the outermost savepoint commits and can itself fail.
        connection.execute('RELEASE ' + name)
    except BaseException:
        try:
            if had_transaction:
                connection.execute('ROLLBACK TO ' + name)
                # Release only after rollback succeeds.
                connection.execute('RELEASE ' + name)
            else:
                # We own this transaction. Rollback also works when a reader
                # lock prevents RELEASE from committing an empty savepoint.
                connection.rollback()
        except BaseException:
            # SQLite may already have rolled back and removed the savepoint.
            # Cleanup failures must never replace the original exception.
            pass
        raise
    return count
