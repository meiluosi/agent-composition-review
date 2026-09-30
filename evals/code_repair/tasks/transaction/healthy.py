"""Append a batch using a caller-supplied SQLite connection."""
import uuid


def write_batch(connection, rows):
    name='batch_'+uuid.uuid4().hex
    connection.execute('SAVEPOINT '+name)
    try:
        connection.executemany('INSERT INTO records(key, value) VALUES (?, ?)',rows)
    except BaseException:
        connection.execute('ROLLBACK TO '+name)
        connection.execute('RELEASE '+name)
        raise
    else:
        connection.execute('RELEASE '+name)
    return len(rows)
