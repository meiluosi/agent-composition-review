"""Append a batch using a caller-supplied SQLite connection."""


def write_batch(connection, rows):
    with connection:
        connection.executemany('INSERT INTO records(key, value) VALUES (?, ?)',rows)
    return len(rows)
