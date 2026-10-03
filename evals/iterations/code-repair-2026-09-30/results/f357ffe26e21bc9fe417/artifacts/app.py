"""Append a batch using a caller-supplied SQLite connection."""
import uuid


def write_batch(connection, rows):
    outer_transaction = connection.in_transaction
    name = 'batch_' + uuid.uuid4().hex
    connection.execute('SAVEPOINT ' + name)
    try:
        connection.executemany('INSERT INTO records(key, value) VALUES (?, ?)', rows)
        # Releasing the outermost savepoint can fail during commit.
        connection.execute('RELEASE ' + name)
    except BaseException:
        try:
            if connection.in_transaction:
                if outer_transaction:
                    connection.execute('ROLLBACK TO ' + name)
                    connection.execute('RELEASE ' + name)
                else:
                    # This transaction belongs entirely to this call. A full
                    # rollback also works when a reader prevents RELEASE.
                    connection.execute('ROLLBACK')
        except BaseException:
            # Cleanup must never replace the original exception. SQLite may
            # itself have rolled back the transaction or become unavailable.
            pass
        raise
    return len(rows)
