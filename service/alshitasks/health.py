"""Local named checks, fenced observations, incidents and health transitions."""

import json
import time
from datetime import datetime, timezone, timedelta
from .store import Problem, uid, now
from .workspaces import require_item, visible

KINDS = {"https", "tcp", "icmp", "postgresql", "mysql", "mariadb", "redis", "mongodb", "heartbeat"}


def migrate(s):
    s.db.executescript("""
    CREATE TABLE IF NOT EXISTS health_checks(id TEXT PRIMARY KEY,resource_id TEXT,name TEXT,config TEXT,enabled INTEGER DEFAULT 1,required INTEGER DEFAULT 1,maintenance_until REAL DEFAULT 0,revision INTEGER DEFAULT 1,health TEXT DEFAULT 'unknown',candidate TEXT DEFAULT 'unknown',streak INTEGER DEFAULT 0,last_at REAL,next_at REAL DEFAULT 0,lease TEXT,lease_until REAL DEFAULT 0,detail TEXT DEFAULT '',heartbeat_at REAL DEFAULT 0,UNIQUE(resource_id,name));
    CREATE TABLE IF NOT EXISTS health_observations(id TEXT PRIMARY KEY,check_id TEXT,resource_id TEXT,health TEXT,detail TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS health_state(resource_id TEXT PRIMARY KEY,health TEXT,observed INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS health_transitions(id TEXT PRIMARY KEY,resource_id TEXT,previous TEXT,health TEXT,suppressed INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS health_incidents(id TEXT PRIMARY KEY,resource_id TEXT,health TEXT,opened_at TEXT,closed_at TEXT,observations INTEGER DEFAULT 1,suppressed INTEGER DEFAULT 0);
    CREATE UNIQUE INDEX IF NOT EXISTS health_open ON health_incidents(resource_id) WHERE closed_at IS NULL;
    CREATE INDEX IF NOT EXISTS health_observation_time ON health_observations(created_at);
    """)
    with s.tx():
        if not s.setting("named_monitor_migration", False):
            for r in s.db.execute("SELECT * FROM monitors").fetchall():
                config = json.loads(r["config"])
                if config.get("kind") == "heartbeat":
                    config["expected_seconds"] = config.pop("timeout", 240)
                    config["timeout"] = 8
                s.db.execute(
                    "INSERT OR IGNORE INTO health_checks(id,resource_id,name,config) VALUES(?,?,?,?)",
                    (r["resource_id"], r["resource_id"], "Default check", json.dumps(config)),
                )
            for r in s.db.execute("SELECT * FROM incidents").fetchall():
                s.db.execute(
                    "INSERT OR IGNORE INTO health_transitions VALUES(?,?,?,?,?,?)",
                    (r["id"], r["resource_id"], "unknown", normalize(r["health"]), 1, r["created_at"]),
                )
            s.set_setting("named_monitor_migration", True)


def normalize(value):
    return {"failed": "unhealthy", "missing": "unknown"}.get(value, value)


def available(s, ident):
    from .workspaces import catalog, LOCAL

    item = s.db.execute("SELECT connection,data FROM items WHERE id=?", (ident,)).fetchone()
    if not item or s.setting("unavailable:" + ident, False):
        return False
    data = json.loads(item["data"])
    workspaces = {w["id"] for w in catalog(s) if w["state"] != "revoked"}
    return (
        not data.get("deleted") and not data.get("archived") and (item["connection"] or LOCAL) in workspaces
    )


def resource(s, ident):
    require_item(s, ident)
    item = s.item(ident)
    if item["kind"] != "resource" or item["data"].get("deleted"):
        raise Problem("Choose an available resource", 404)
    return item


def clean(value):
    from urllib.parse import urlsplit

    if not isinstance(value, dict) or value.get("kind") not in KINDS:
        raise Problem("Choose a supported check type")
    if set(value) - {
        "kind",
        "url",
        "expected_status",
        "host",
        "port",
        "database",
        "tls",
        "timeout",
        "expected_seconds",
        "credential",
        "username",
        "interval",
    }:
        raise Problem("Unknown check setting")
    c = dict(value)
    for key, default, lo, hi in [
        ("interval", 120, 30, 86400),
        ("timeout", 8, 1, 10),
        ("expected_seconds", 240, 120, 86400),
    ]:
        c.setdefault(key, default)
        if type(c[key]) is not int or not lo <= c[key] <= hi:
            raise Problem(f"{key} must be {lo}..{hi}")
    if type(c.get("tls", True)) is not bool:
        raise Problem("TLS must be a boolean")
    if c.get("expected_status") is not None and (
        type(c["expected_status"]) is not int or not 100 <= c["expected_status"] <= 599
    ):
        raise Problem("Invalid expected HTTP status")
    if c["kind"] == "https":
        parsed = urlsplit(c.get("url", ""))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or len(c["url"]) > 2000
        ):
            raise Problem("Use an HTTPS URL without embedded credentials")
    elif c["kind"] != "heartbeat":
        import ipaddress
        import re

        host = c.get("host", "")
        if not isinstance(host, str) or not 1 <= len(host) <= 250:
            raise Problem("Host is required")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", p)
                for p in host.rstrip(".").split(".")
            ):
                raise Problem("Invalid hostname")
        ports = {"postgresql": 5432, "mysql": 3306, "mariadb": 3306, "redis": 6379, "mongodb": 27017}
        c.setdefault("port", ports.get(c["kind"], 443))
        if type(c["port"]) is not int or not 1 <= c["port"] <= 65535:
            raise Problem("Invalid port")
    for key in ("credential", "username", "database"):
        if key in c and (not isinstance(c[key], str) or len(c[key]) > 255):
            raise Problem("Invalid " + key)
    return c


def save(s, body):
    r = resource(s, body.get("resource_id"))
    ident = body.get("id") or uid()
    old = s.db.execute("SELECT * FROM health_checks WHERE id=?", (ident,)).fetchone()
    if old and (old["resource_id"] != r["id"] or body.get("revision") != old["revision"]):
        raise Problem("Check changed; reload before saving", 409)
    name = body.get("name", "").strip()
    if not 1 <= len(name) <= 100:
        raise Problem("Name must be 1..100 characters")
    if type(body.get("required", True)) is not bool:
        raise Problem("Required must be a boolean")
    config = clean(body.get("config"))
    if config.get("credential"):
        from .security import Secrets

        Secrets().get(config["credential"])
    duplicate = s.db.execute(
        "SELECT id FROM health_checks WHERE resource_id=? AND name=? AND id!=?", (r["id"], name, ident)
    ).fetchone()
    if duplicate:
        raise Problem("Check name already exists", 409)
    s.db.execute(
        """INSERT INTO health_checks(id,resource_id,name,config,required) VALUES(?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET name=excluded.name,config=excluded.config,required=excluded.required,
      revision=revision+1,health='unknown',candidate='unknown',streak=0,last_at=NULL,next_at=0,lease=NULL,lease_until=0,heartbeat_at=0""",
        (ident, r["id"], name, json.dumps(config), body.get("required", True)),
    )
    aggregate(s, r["id"], suppression=True)
    return get_check(s, ident)


def get_check(s, ident):
    row = s.db.execute("SELECT * FROM health_checks WHERE id=?", (ident,)).fetchone()
    if not row:
        raise Problem("Check unavailable", 404)
    resource(s, row["resource_id"])
    return dict(row, config=json.loads(row["config"]))


def action(s, body):
    row = get_check(s, body.get("id"))
    if row["revision"] != body.get("revision"):
        raise Problem("Check changed; reload", 409)
    op = body.get("action")
    if op not in {"run", "pause", "resume", "maintenance", "end_maintenance"}:
        raise Problem("Unknown check action")
    if op == "maintenance":
        minutes = body.get("minutes", 60)
        if type(minutes) is not int or not 1 <= minutes <= 43200:
            raise Problem("Maintenance must be 1..43200 minutes")
        s.db.execute(
            "UPDATE health_checks SET maintenance_until=? WHERE id=?", (time.time() + 60 * minutes, row["id"])
        )
    elif op == "end_maintenance":
        s.db.execute("UPDATE health_checks SET maintenance_until=0 WHERE id=?", (row["id"],))
    elif op in {"pause", "resume"}:
        s.db.execute(
            "UPDATE health_checks SET enabled=?,health='unknown',candidate='unknown',streak=0,last_at=NULL WHERE id=?",
            (op == "resume", row["id"]),
        )
    s.db.execute(
        "UPDATE health_checks SET revision=revision+1,next_at=0,lease=NULL,lease_until=0 WHERE id=?",
        (row["id"],),
    )
    aggregate(s, row["resource_id"], suppression=op in {"pause", "resume", "maintenance", "end_maintenance"})
    return get_check(s, row["id"])


def aggregate(s, ident, suppression=False):
    rows = s.db.execute(
        "SELECT * FROM health_checks WHERE resource_id=? AND enabled=1 AND required=1", (ident,)
    ).fetchall()
    health = (
        "unhealthy"
        if any(r["health"] == "unhealthy" for r in rows)
        else "healthy"
        if rows and all(r["health"] == "healthy" for r in rows)
        else "unknown"
    )
    old = s.db.execute("SELECT * FROM health_state WHERE resource_id=?", (ident,)).fetchone()
    previous = old["health"] if old else "unknown"
    observed = bool(old and old["observed"]) or any(r["last_at"] for r in rows)
    s.db.execute(
        "INSERT INTO health_state VALUES(?,?,?) ON CONFLICT(resource_id) DO UPDATE SET health=excluded.health,observed=excluded.observed",
        (ident, health, observed),
    )
    causes = [r for r in rows if r["health"] == health]
    suppressed = suppression or bool(causes) and all(r["maintenance_until"] > time.time() for r in causes)
    incident = s.db.execute(
        "SELECT * FROM health_incidents WHERE resource_id=? AND closed_at IS NULL", (ident,)
    ).fetchone()
    changed = previous != health
    if changed:
        s.db.execute(
            "INSERT INTO health_transitions VALUES(?,?,?,?,?,?)",
            (uid(), ident, previous, health, suppressed, now()),
        )
    if health != "healthy" and observed and rows:
        if incident:
            if incident["suppressed"] and not suppressed:
                notify(s, ident, health, incident["id"])
                s.db.execute("UPDATE health_incidents SET suppressed=0 WHERE id=?", (incident["id"],))
            s.db.execute(
                "UPDATE health_incidents SET observations=observations+1,health=? WHERE id=?",
                (health, incident["id"]),
            )
        elif not suppression:
            incident_id = uid()
            s.db.execute(
                "INSERT INTO health_incidents(id,resource_id,health,opened_at,suppressed) VALUES(?,?,?,?,?)",
                (incident_id, ident, health, now(), suppressed),
            )
            if not suppressed:
                notify(s, ident, health, incident_id)
    elif health == "healthy" and incident:
        s.db.execute("UPDATE health_incidents SET closed_at=? WHERE id=?", (now(), incident["id"]))
        if not suppressed and not incident["suppressed"]:
            notify(s, ident, "healthy", incident["id"])
    return health


def notify(s, ident, health, incident):
    from .notifications import create

    item = s.item(ident)
    title = {"healthy": "Recovered", "unhealthy": "Check failed", "unknown": "Monitoring unavailable"}[health]
    create(
        s,
        title + " · " + item["data"]["title"],
        "Resource health is " + health + ". Open monitoring for check details.",
        "resource_" + health,
        resource_id=ident,
        event_key=incident + ":" + health,
    )


def observe(s, row, outcome, detail, clock=None):
    clock = time.time() if clock is None else clock
    current = s.db.execute("SELECT * FROM health_checks WHERE id=?", (row["id"],)).fetchone()
    if (
        not current
        or not current["lease"]
        or current["lease_until"] < clock
        or not current["enabled"]
        or current["revision"] != row["revision"]
        or current["lease"] != row["lease"]
    ):
        return False
    item = s.item(current["resource_id"])
    if (
        item["data"].get("deleted")
        or item["data"].get("archived")
        or s.setting("unavailable:" + current["resource_id"], False)
    ):
        return False
    if not available(s, current["resource_id"]):
        return False
    c = json.loads(current["config"])
    outcome = normalize(outcome)
    if outcome not in {"healthy", "unhealthy", "unknown"}:
        raise Problem("Invalid check outcome")
    fresh = current["last_at"] is not None and clock - current["last_at"] <= max(
        c.get("interval", 120) * 3, 60
    )
    streak = min(2, current["streak"] + 1) if fresh and current["candidate"] == outcome else 1
    health = outcome if outcome == "unknown" or streak >= 2 else current["health"] if fresh else "unknown"
    s.db.execute(
        "UPDATE health_checks SET health=?,candidate=?,streak=?,last_at=?,next_at=?,detail=?,lease=NULL,lease_until=0 WHERE id=?",
        (health, outcome, streak, clock, clock + c.get("interval", 120), detail[:1000], row["id"]),
    )
    s.db.execute(
        "INSERT INTO health_observations VALUES(?,?,?,?,?,?)",
        (uid(), row["id"], row["resource_id"], outcome, detail[:1000], now()),
    )
    # A first candidate observation is not yet a loss-of-visibility incident.
    if outcome != "unknown" and streak < 2 and not fresh:
        s.db.execute("INSERT OR IGNORE INTO health_state VALUES(?,'unknown',0)", (row["resource_id"],))
    else:
        aggregate(s, row["resource_id"])
    if row["id"] == row["resource_id"]:
        s.db.execute(
            "UPDATE monitors SET health=?,last_at=?,detail=?,next_at=? WHERE resource_id=?",
            (health, now(), detail, clock + c.get("interval", 120), row["resource_id"]),
        )
    return True


def claim(s):
    clock = time.time()
    # Suspend and long gaps break consecutive observations without fabricating failure.
    for r in s.db.execute("SELECT * FROM health_checks WHERE enabled=1 AND last_at IS NOT NULL").fetchall():
        if not available(s, r["resource_id"]):
            continue
        c = json.loads(r["config"])
        if clock - r["last_at"] > max(3 * c.get("interval", 120), 60):
            s.db.execute(
                "UPDATE health_checks SET health='unknown',candidate='unknown',streak=0,last_at=NULL WHERE id=?",
                (r["id"],),
            )
            aggregate(s, r["resource_id"])
    selected = []
    for row in s.db.execute(
        "SELECT c.* FROM health_checks c JOIN items i ON i.id=c.resource_id WHERE c.enabled=1 AND c.next_at<=? AND c.lease_until<=? AND COALESCE(json_extract(i.data,'$.deleted'),0)=0 AND COALESCE(json_extract(i.data,'$.archived'),0)=0 ORDER BY c.next_at",
        (clock, clock),
    ).fetchall():
        if not available(s, row["resource_id"]):
            continue
        lease = uid()
        s.db.execute(
            "UPDATE health_checks SET lease=?,lease_until=? WHERE id=?", (lease, clock + 45, row["id"])
        )
        selected.append(dict(row, lease=lease, config=json.loads(row["config"])))
        if len(selected) == 4:
            break
    return selected


def tick(s):
    from concurrent.futures import ThreadPoolExecutor

    with s.tx():
        rows = claim(s)

    def run(row):
        c = dict(row["config"])
        if c["kind"] == "heartbeat":
            result = (
                ("healthy", "Heartbeat received")
                if time.time() - row["heartbeat_at"] <= c.get("expected_seconds", 240)
                else ("unknown", "Application heartbeat is missing")
            )
        else:
            import subprocess
            import sys
            from pathlib import Path

            try:
                completed = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve().parents[1] / "health-probe.py")],
                    input=json.dumps(c),
                    capture_output=True,
                    text=True,
                    timeout=3 * c.get("timeout", 8) + 2,
                    check=True,
                )
                result = json.loads(completed.stdout)
            except Exception:
                result = ("unknown", "Check exceeded its execution limit or could not run")
        with s.tx():
            observe(s, row, *result)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, rows))
    with s.tx():
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        s.db.execute("DELETE FROM health_observations WHERE created_at<?", (cutoff,))
        s.db.execute("DELETE FROM health_transitions WHERE created_at<?", (cutoff,))
        s.db.execute("DELETE FROM health_incidents WHERE closed_at IS NOT NULL AND closed_at<?", (cutoff,))


def snapshot(s, ident=None):
    if ident:
        resource(s, ident)
    ids = [
        r["id"]
        for r in s.db.execute("SELECT id,connection FROM items WHERE kind='resource'")
        if (not ident or ident == r["id"])
        and visible(s, r["connection"])
        and not s.setting("unavailable:" + r["id"], False)
    ]
    q = json.dumps(ids)
    checks = [
        dict(r, config=json.loads(r["config"]))
        for r in s.db.execute(
            "SELECT * FROM health_checks WHERE resource_id IN (SELECT value FROM json_each(?)) ORDER BY name",
            (q,),
        )
    ]
    return {
        "checks": checks,
        "monitors": checks,
        "resources": [
            dict(r)
            for r in s.db.execute(
                "SELECT * FROM health_state WHERE resource_id IN (SELECT value FROM json_each(?))", (q,)
            )
        ],
        "incidents": [
            dict(r)
            for r in s.db.execute(
                "SELECT * FROM health_incidents WHERE resource_id IN (SELECT value FROM json_each(?)) ORDER BY opened_at DESC LIMIT 100",
                (q,),
            )
        ],
        "history": [
            dict(r)
            for r in s.db.execute(
                "SELECT * FROM health_transitions WHERE resource_id IN (SELECT value FROM json_each(?)) ORDER BY created_at DESC LIMIT 100",
                (q,),
            )
        ],
    }
