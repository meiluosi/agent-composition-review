"""Append a batch using a caller-supplied SQLite connection."""


def write_batch(connection, rows):
    count = len(rows)
    # SQLite resolves duplicate savepoint names to the innermost occurrence.
    # RELEASE commits only when this savepoint started the transaction.
    connection.execute('SAVEPOINT _write_batch')
    try:
        connection.executemany('INSERT INTO records(key, value) VALUES (?, ?)', rows)
        connection.execute('RELEASE SAVEPOINT _write_batch')
    except BaseException:
        try:
            connection.execute('ROLLBACK TO SAVEPOINT _write_batch')
            connection.execute('RELEASE SAVEPOINT _write_batch')
        except BaseException:
            # Cleanup failures must not replace the original exception. If
            # rollback fails, do not release and risk committing partial work.
            pass
        raise
    return count
