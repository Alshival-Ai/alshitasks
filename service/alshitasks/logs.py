import base64
import hashlib
import json
import os
import uuid
import secrets
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
            "CREATE TABLE IF NOT EXISTS logs(id TEXT,resource TEXT,timestamp TEXT,severity TEXT,message TEXT,attributes TEXT,bytes INTEGER,PRIMARY KEY(id,resource));CREATE INDEX IF NOT EXISTS log_timestamp ON logs(timestamp);CREATE INDEX IF NOT EXISTS log_resource_time ON logs(resource,timestamp DESC,id);"
        )
        # Additive migration: legacy IDs and event content remain intact.
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(logs)")}
        if "cloud_id" not in columns and self.db.execute("SELECT 1 FROM logs LIMIT 1").fetchone():
            backup_path = self.path.with_suffix(".pre-cloud-v1.sqlite")
            if not backup_path.exists():
                fd = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                backup = sqlite3.connect(backup_path)
                try:
                    self.db.backup(backup)
                finally:
                    backup.close()
        with self.db:
            for column in ("received_at", "cloud_id", "source", "trace_id", "span_id", "transport"):
                if column not in columns:
                    self.db.execute(f"ALTER TABLE logs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            for row in self.db.execute(
                "SELECT id,resource,timestamp FROM logs WHERE cloud_id='' "
            ).fetchall():
                self.db.execute(
                    "UPDATE logs SET cloud_id=?,received_at=? WHERE id=? AND resource=?",
                    (
                        str(uuid.uuid5(uuid.NAMESPACE_URL, row["resource"] + ":" + row["id"])),
                        row["timestamp"],
                        row["id"],
                        row["resource"],
                    ),
                )
            self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS log_cloud_id ON logs(cloud_id)")
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS log_received ON logs(resource,received_at DESC,cloud_id DESC)"
            )
            self.db.execute("CREATE TABLE IF NOT EXISTS log_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            self.db.execute(
                "INSERT OR IGNORE INTO log_meta VALUES('cursor_secret',?)", (secrets.token_hex(32),)
            )
            self.db.execute("PRAGMA user_version=1")
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
            except (ValueError, KeyError, TypeError, AttributeError):
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
            self.db.executemany(
                "INSERT OR IGNORE INTO logs(id,resource,timestamp,severity,message,attributes,bytes,received_at,cloud_id) VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    (
                        *r,
                        datetime.now(timezone.utc).isoformat(),
                        str(uuid.uuid5(uuid.NAMESPACE_URL, resource + ":" + r[0])),
                    )
                    for r in validated
                ],
            )
            added = self.db.total_changes - before
        return dict(accepted=added, duplicates=len(events) - added)

    def query(self, q, resources=None):
        allowed = {"resource_id", "workspace_id", "since", "until", "severity", "text", "limit", "cursor"}
        if set(q) - allowed:
            raise Problem("Unknown log filter")
        try:
            limit = int(q.get("limit", 100))
            if not 1 <= limit <= 100:
                raise ValueError()
        except (ValueError, TypeError):
            raise Problem("Limit must be 1..100")
        q = dict(q)
        for key in ("since", "until"):
            if q.get(key):
                try:
                    stamp = datetime.fromisoformat(q[key].replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        raise ValueError()
                    q[key] = stamp.astimezone(timezone.utc).isoformat()
                except (ValueError, TypeError, AttributeError):
                    raise Problem("Timezone-aware query timestamp required")
        if q.get("since") and q.get("until") and q["since"] > q["until"]:
            raise Problem("Since must precede until")
        if q.get("severity") and q["severity"] not in LEVELS:
            raise Problem("Invalid severity")
        if not isinstance(q.get("text", ""), str) or len(q.get("text", "")) > 16000:
            raise Problem("Invalid search text")
        clauses, args = [], []
        for key, column, op in (
            ("resource_id", "resource", "="),
            ("severity", "severity", "="),
            ("since", "timestamp", ">="),
            ("until", "timestamp", "<="),
        ):
            if q.get(key):
                clauses.append(f"{column}{op}?")
                args.append(q[key])
        if q.get("text"):
            clauses.append("instr(message,?)>0")
            args.append(q["text"])
        if resources is not None:
            clauses.append("resource IN (SELECT value FROM json_each(?))")
            args.append(json.dumps(sorted(resources)))
        signature = hashlib.sha256(json.dumps([clauses, args, q.get("workspace_id")]).encode()).hexdigest()
        if q.get("cursor"):
            try:
                if len(q["cursor"]) > 2048:
                    raise ValueError()
                cursor = json.loads(base64.urlsafe_b64decode(q["cursor"]))
                if (
                    not isinstance(cursor, list)
                    or len(cursor) != 4
                    or cursor[0] != signature
                    or not all(isinstance(v, str) for v in cursor)
                ):
                    raise ValueError()
            except Exception:
                raise Problem("Invalid cursor for these filters")
            clauses.append("(timestamp<? OR (timestamp=? AND (id>? OR (id=? AND resource>?))))")
            args.extend([cursor[1], cursor[1], cursor[2], cursor[2], cursor[3]])
        sql = "SELECT * FROM logs" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        rows = self.db.execute(
            sql + " ORDER BY timestamp DESC,id,resource LIMIT ?", [*args, limit + 1]
        ).fetchall()
        page = rows[:limit]
        cursor = None
        if len(rows) > limit:
            last = page[-1]
            cursor = base64.urlsafe_b64encode(
                json.dumps([signature, last["timestamp"], last["id"], last["resource"]]).encode()
            ).decode()
        return {
            "events": [dict(r, attributes=json.loads(r["attributes"])) for r in page],
            "next_cursor": cursor,
        }

    @staticmethod
    def validate_retention(days=7, max_bytes=1024**3):
        if (
            not isinstance(days, int)
            or not 1 <= days <= 365
            or not isinstance(max_bytes, int)
            or not 1024**2 <= max_bytes <= 10 * 1024**3
        ):
            raise Problem("Retention must be 1..365 days and 1 MiB..10 GiB")

    def retain(self, days=7, max_bytes=1024**3):
        self.validate_retention(days, max_bytes)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.db:
            self.db.execute(
                "DELETE FROM logs WHERE (CASE WHEN transport='cloud' THEN received_at ELSE timestamp END)<?",
                (cutoff,),
            )
            total = self.db.execute("SELECT COALESCE(SUM(bytes),0) FROM logs").fetchone()[0]
            if total > max_bytes:
                for r in self.db.execute(
                    "SELECT id,resource,bytes FROM logs ORDER BY CASE WHEN transport='cloud' THEN received_at ELSE timestamp END"
                ).fetchall():
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
                        "SELECT id,resource FROM logs ORDER BY CASE WHEN transport='cloud' THEN received_at ELSE timestamp END LIMIT 100"
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
