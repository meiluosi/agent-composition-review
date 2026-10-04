"""A small persistent work queue with time-bounded delivery receipts."""
import json
import math
import sqlite3
from contextlib import closing


def _text_key(value):
    """Keep existing TEXT keys; store strings SQLite cannot encode as BLOBs."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return value.encode("utf-8", "surrogatepass")
    return value


class Queue:
    def __init__(self, path):
        self.path = str(path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'ready',
                worker TEXT, generation INTEGER NOT NULL DEFAULT 0, expires_at REAL
            )""")

    def put(self, job_id, payload):
        # Literal Unicode preserves Python strings containing surrogate pairs;
        # escaped pairs would be combined by json.loads, even in object keys.
        encoded = _text_key(json.dumps(payload, allow_nan=False, ensure_ascii=False))
        with closing(sqlite3.connect(self.path)) as db, db:
            try:
                db.execute("INSERT INTO jobs(job_id, payload) VALUES (?, ?)",
                           (_text_key(job_id), encoded))
            except sqlite3.IntegrityError as exc:
                raise ValueError("job_id already exists") from exc

    def claim(self, worker, now, lease_seconds):
        if (isinstance(lease_seconds, bool) or not math.isfinite(lease_seconds)
                or lease_seconds <= 0):
            raise ValueError("lease_seconds must be finite and positive")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT job_id, payload, generation FROM jobs
                WHERE state = 'ready' OR (state = 'leased' AND expires_at <= ?)
                ORDER BY seq LIMIT 1""", (now,)).fetchone()
            if row is None:
                return None
            job_id, payload, previous_generation = row
            generation = previous_generation + 1
            expires_at = now + lease_seconds
            db.execute("""UPDATE jobs SET state = 'leased', worker = ?,
                generation = ?, expires_at = ? WHERE job_id = ?""",
                (_text_key(worker), generation, expires_at, job_id))
            if isinstance(job_id, bytes):
                job_id = job_id.decode("utf-8", "surrogatepass")
            return {"payload": json.loads(payload), "expires_at": expires_at,
                    "receipt": {"job_id": job_id, "worker": worker, "generation": generation}}

    def ack(self, receipt, now):
        with closing(sqlite3.connect(self.path)) as db, db:
            changed = db.execute("""UPDATE jobs SET state = 'done'
                WHERE job_id = ? AND worker = ? AND generation = ?
                AND state = 'leased' AND expires_at > ?""",
                (_text_key(receipt["job_id"]), _text_key(receipt["worker"]),
                 receipt["generation"], now)).rowcount
            return changed == 1
