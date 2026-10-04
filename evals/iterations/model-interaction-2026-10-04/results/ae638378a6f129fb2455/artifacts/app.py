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
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "expires_exact" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN expires_exact TEXT")

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
                or not math.isfinite(lease_seconds) or lease_seconds <= 0):
            raise ValueError("lease_seconds must be finite and positive")
        expires_at = now + lease_seconds
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("""SELECT job_id, payload, generation, state,
                expires_at, expires_exact FROM jobs ORDER BY seq""").fetchall()
            row = None
            for candidate in rows:
                if candidate[3] == "ready":
                    row = candidate
                    break
                if candidate[3] == "leased":
                    expiry = (json.loads(candidate[5]) if candidate[5] is not None
                              else candidate[4])
                    if expiry <= now:
                        row = candidate
                        break
            if row is None:
                return None
            job_id, payload, previous_generation = row[:3]
            generation = previous_generation + 1
            db.execute("""UPDATE jobs SET state = 'leased', worker = ?,
                generation = ?, expires_at = ?, expires_exact = ? WHERE job_id = ?""",
                (worker, generation, expires_at, json.dumps(expires_at), job_id))
            return {"payload": json.loads(payload), "expires_at": expires_at,
                    "receipt": {"job_id": job_id, "worker": worker, "generation": generation}}

    def ack(self, receipt, now):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT state, worker, generation, expires_at,
                expires_exact FROM jobs WHERE job_id = ?""",
                (receipt["job_id"],)).fetchone()
            if (row is None or row[0] != "leased" or row[1] != receipt["worker"]
                    or row[2] != receipt["generation"]):
                return False
            expires_at = json.loads(row[4]) if row[4] is not None else row[3]
            if not now < expires_at:
                return False
            changed = db.execute("""UPDATE jobs SET state = 'done'
                WHERE job_id = ? AND state = 'leased' AND worker = ? AND generation = ?""",
                (receipt["job_id"], receipt["worker"], receipt["generation"])).rowcount
            return changed == 1
