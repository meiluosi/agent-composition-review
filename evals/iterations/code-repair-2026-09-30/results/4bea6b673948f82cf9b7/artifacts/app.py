"""Append a batch using a caller-supplied SQLite connection."""
import uuid


def write_batch(connection, rows):
    count = len(rows)
    caller_transaction = connection.in_transaction
    name='batch_'+uuid.uuid4().hex
    connection.execute('SAVEPOINT '+name)
    try:
        connection.executemany('INSERT INTO records(key, value) VALUES (?, ?)',rows)
        # An outermost RELEASE commits and can itself fail.
        connection.execute('RELEASE '+name)
    except BaseException:
        try:
            if caller_transaction:
                connection.execute('ROLLBACK TO '+name)
                connection.execute('RELEASE '+name)
            else:
                # End our own transaction without retrying a failing commit.
                connection.execute('ROLLBACK')
        except BaseException:
            # SQLite may already have rolled back and removed the savepoint.
            # Keep the original insertion/commit exception in either case.
            pass
        raise
    return count
