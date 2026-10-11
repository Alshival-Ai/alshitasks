import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from .store import Problem

LEVELS = {"debug", "info", "warning", "error", "critical"}


class Logs:
    def __init__(self, paths):
        self.path = paths.data / "logs.sqlite"
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS logs(id TEXT,resource TEXT,timestamp TEXT,severity TEXT,message TEXT,attributes TEXT,bytes INTEGER,PRIMARY KEY(id,resource));CREATE INDEX IF NOT EXISTS log_timestamp ON logs(timestamp);"
        )
        os.chmod(paths.data / "logs.sqlite", 0o600)

    def ingest(self, resource, events):
        if not isinstance(events, list) or not 1 <= len(events) <= 100:
            raise Problem("Supply 1..100 events")
        validated = []
        for e in events:
            if not isinstance(e, dict) or set(e) - {"id", "timestamp", "severity", "message", "attributes"}:
                raise Problem("Invalid event fields")
            if not isinstance(e.get("id"), str) or not 1 <= len(e["id"]) <= 128:
                raise Problem("Event ID required")
            if (
                e.get("severity") not in LEVELS
                or not isinstance(e.get("message"), str)
                or len(e["message"]) > 16000
            ):
                raise Problem("Invalid event")
            if not isinstance(e.get("attributes", {}), dict):
                raise Problem("Invalid attributes")
            try:
                stamp = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                if stamp.tzinfo is None or stamp > datetime.now(timezone.utc) + timedelta(minutes=5):
                    raise ValueError()
                timestamp = stamp.astimezone(timezone.utc).isoformat()
            except (ValueError, KeyError, TypeError):
                raise Problem("Timezone-aware timestamp required")
            size = len(json.dumps(e).encode())
            if size > 32000:
                raise Problem("Event too large", 413)
            validated.append(
                (
                    e["id"],
                    resource,
                    timestamp,
                    e["severity"],
                    e["message"],
                    json.dumps(e.get("attributes", {})),
                    size,
                )
            )
        with self.db:
            before = self.db.total_changes
            self.db.executemany("INSERT OR IGNORE INTO logs VALUES(?,?,?,?,?,?,?)", validated)
            added = self.db.total_changes - before
        return dict(accepted=added, duplicates=len(events) - added)

    def query(self, q):
        limit = min(100, max(1, int(q.get("limit", 100))))
        rows = self.db.execute(
            """SELECT * FROM logs WHERE (?='' OR resource=?) AND (?='' OR severity=?) AND (?='' OR timestamp>=?) AND (?='' OR timestamp<=?) AND (?='' OR instr(message,?)>0) ORDER BY timestamp DESC,id LIMIT ?""",
            (q.get("resource_id", ""),) * 2
            + (q.get("severity", ""),) * 2
            + (q.get("since", ""),) * 2
            + (q.get("until", ""),) * 2
            + (q.get("text", ""),) * 2
            + (limit,),
        ).fetchall()
        return {"events": [dict(r, attributes=json.loads(r["attributes"])) for r in rows]}

    def retain(self, days=7, max_bytes=1024**3):
        if (
            not isinstance(days, int)
            or not 1 <= days <= 365
            or not isinstance(max_bytes, int)
            or not 1024**2 <= max_bytes <= 10 * 1024**3
        ):
            raise Problem("Retention must be 1..365 days and 1 MiB..10 GiB")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.db:
            self.db.execute("DELETE FROM logs WHERE timestamp<?", (cutoff,))
            total = self.db.execute("SELECT COALESCE(SUM(bytes),0) FROM logs").fetchone()[0]
            if total > max_bytes:
                for r in self.db.execute("SELECT id,resource,bytes FROM logs ORDER BY timestamp").fetchall():
                    self.db.execute("DELETE FROM logs WHERE id=? AND resource=?", (r["id"], r["resource"]))
                    total -= r["bytes"]
                    if total <= max_bytes:
                        break
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # Account for SQLite pages and indexes, rather than message bytes alone.
        if self.path.stat().st_size > max_bytes:
            with self.db:
                while True:
                    pages = (
                        self.db.execute("PRAGMA page_count").fetchone()[0]
                        - self.db.execute("PRAGMA freelist_count").fetchone()[0]
                    )
                    page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
                    if pages * page_size <= max_bytes:
                        break
                    oldest = self.db.execute(
                        "SELECT id,resource FROM logs ORDER BY timestamp LIMIT 100"
                    ).fetchall()
                    if not oldest:
                        break
                    self.db.executemany(
                        "DELETE FROM logs WHERE id=? AND resource=?",
                        [(x["id"], x["resource"]) for x in oldest],
                    )
            self.db.execute("VACUUM")
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {
            "bytes": self.db.execute("SELECT COALESCE(SUM(bytes),0) FROM logs").fetchone()[0],
            "events": self.db.execute("SELECT COUNT(*) FROM logs").fetchone()[0],
        }
