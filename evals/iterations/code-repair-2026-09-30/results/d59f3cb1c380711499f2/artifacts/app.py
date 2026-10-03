"""Append a batch using a caller-supplied SQLite connection."""
import uuid


def write_batch(connection, rows):
    outer_transaction = connection.in_transaction
    name='batch_'+uuid.uuid4().hex
    connection.execute('SAVEPOINT '+name)
    try:
        connection.executemany('INSERT INTO records(key, value) VALUES (?, ?)',rows)
        # Releasing the outermost savepoint commits and can itself fail.
        connection.execute('RELEASE '+name)
    except BaseException:
        try:
            if outer_transaction:
                connection.execute('ROLLBACK TO '+name)
                connection.execute('RELEASE '+name)
            else:
                # An owned transaction must be rolled back directly: even an
                # empty write transaction can fail RELEASE under a read lock.
                connection.rollback()
        except BaseException:
            # Cleanup must not replace the original failure. Do not release
            # after a failed rollback, which could commit partial writes.
            pass
        raise
    return len(rows)
