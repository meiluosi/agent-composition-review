"""A small persistent work queue with time-bounded delivery receipts."""
import json
import math
import sqlite3
from contextlib import closing


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
        encoded = json.dumps(payload, allow_nan=False)
        with closing(sqlite3.connect(self.path)) as db, db:
            try:
                db.execute("INSERT INTO jobs(job_id, payload) VALUES (?, ?)", (job_id, encoded))
            except sqlite3.IntegrityError as exc:
                raise ValueError("job_id already exists") from exc

    def claim(self, worker, now, lease_seconds):
        if (isinstance(lease_seconds, bool)
                or not isinstance(lease_seconds, (int, float))
                or not math.isfinite(lease_seconds)
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
                (worker, generation, expires_at, job_id))
            return {"payload": json.loads(payload), "expires_at": expires_at,
                    "receipt": {"job_id": job_id, "worker": worker, "generation": generation}}

    def ack(self, receipt, now):
        with closing(sqlite3.connect(self.path)) as db, db:
            changed = db.execute("""UPDATE jobs SET state = 'done'
                WHERE job_id = ? AND worker = ?
                AND generation = ? AND state = 'leased' AND expires_at > ?""",
                (receipt["job_id"], receipt["worker"], receipt["generation"], now)).rowcount
            return changed == 1
