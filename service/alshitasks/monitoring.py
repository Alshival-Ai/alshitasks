"""Bounded local probes; configured execution never travels through task sync."""

import importlib
import json
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from .store import Problem, now, uid
from .security import Secrets


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def probe(config, secrets=None):
    kind = config["kind"]
    try:
        if kind == "heartbeat":
            return (
                ("healthy", "Heartbeat received")
                if time.time() - config.get("received_at", 0) <= config.get("timeout", 240)
                else ("missing", "Collector heartbeat is missing")
            )
        if kind == "https":
            url = urlsplit(config["url"])
            if url.scheme != "https" or url.username or url.password:
                raise Problem("HTTPS URL without embedded credentials required")
            request = urllib.request.Request(config["url"], method="HEAD")
            with urllib.request.build_opener(NoRedirect).open(request, timeout=8) as response:
                return (
                    ("healthy", f"HTTP {response.status}")
                    if response.status < 400
                    else ("failed", f"HTTP {response.status}")
                )
        if kind == "tcp":
            with socket.create_connection((config["host"], config["port"]), timeout=5):
                return "healthy", "TCP connection accepted"
        password = (secrets or Secrets()).get(config["credential"]) if config.get("credential") else None
        if kind == "postgresql":
            module = importlib.import_module("psycopg")
            with module.connect(
                host=config["host"],
                port=config.get("port", 5432),
                dbname=config.get("database"),
                user=config.get("username"),
                password=password,
                connect_timeout=5,
            ) as connection:
                connection.execute("SELECT 1").fetchone()
        elif kind in ("mysql", "mariadb"):
            module = importlib.import_module("pymysql")
            c = module.connect(
                host=config["host"],
                port=config.get("port", 3306),
                database=config.get("database"),
                user=config.get("username"),
                password=password,
                connect_timeout=5,
                read_timeout=5,
                write_timeout=5,
            )
            try:
                with c.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            finally:
                c.close()
        elif kind == "redis":
            module = importlib.import_module("redis")
            module.Redis(
                host=config["host"],
                port=config.get("port", 6379),
                password=password,
                socket_connect_timeout=5,
                socket_timeout=5,
            ).ping()
        elif kind == "mongodb":
            module = importlib.import_module("pymongo")
            c = module.MongoClient(
                host=config["host"],
                port=config.get("port", 27017),
                username=config.get("username"),
                password=password,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
            )
            try:
                c.admin.command("ping")
            finally:
                c.close()
        else:
            raise Problem("Unknown monitoring adapter")
        return "healthy", "Database responded"
    except ModuleNotFoundError:
        return "missing", "Install the optional database adapter package"
    except Problem:
        return "missing", "Credential or collector configuration is unavailable"
    except Exception:
        return "failed", "Service probe failed (check endpoint and credentials)"


def configure(store, resource, config):
    if store.item(resource)["kind"] != "resource":
        raise Problem("Monitoring requires a resource")
    if not isinstance(config, dict) or config.get("kind") not in (
        "https",
        "tcp",
        "heartbeat",
        "postgresql",
        "mysql",
        "mariadb",
        "redis",
        "mongodb",
    ):
        raise Problem("Unsupported adapter")
    if set(config) - {
        "kind",
        "url",
        "host",
        "port",
        "database",
        "username",
        "credential",
        "timeout",
        "interval",
        "received_at",
    }:
        raise Problem("Unknown monitoring field")
    config = dict(config)
    config["interval"] = int(config.get("interval", 120))
    if not 30 <= config["interval"] <= 86400:
        raise Problem("Monitoring interval must be 30..86400 seconds")
    if "port" in config and (not isinstance(config["port"], int) or not 1 <= config["port"] <= 65535):
        raise Problem("Invalid port")
    if config.get("kind") == "https":
        u = urlsplit(config.get("url", ""))
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            raise Problem("Valid HTTPS URL required")
    elif config["kind"] != "heartbeat":
        if not isinstance(config.get("host"), str) or not config["host"] or len(config["host"]) > 255:
            raise Problem("Host required")
    config.pop("received_at", None)
    store.db.execute(
        "INSERT INTO monitors(resource_id,config) VALUES(?,?) ON CONFLICT(resource_id) DO UPDATE SET config=excluded.config,next_at=0",
        (resource, json.dumps(config)),
    )
    return {"resource_id": resource, "config": config}


def record(store, resource, result, detail):
    old = store.db.execute("SELECT * FROM monitors WHERE resource_id=?", (resource,)).fetchone()
    if not old:
        return
    streak = old["streak"] + 1 if old["candidate"] == result else 1
    health = result if streak >= 2 else old["health"]
    interval = json.loads(old["config"]).get("interval", 120)
    store.db.execute(
        "UPDATE monitors SET candidate=?,streak=?,health=?,last_at=?,detail=?,next_at=? WHERE resource_id=?",
        (result, streak, health, now(), detail, time.time() + interval, resource),
    )
    if health != old["health"]:
        store.db.execute("INSERT INTO incidents VALUES(?,?,?,?,?)", (uid(), resource, health, now(), detail))
        import shutil
        import subprocess

        if shutil.which("notify-send"):
            try:
                subprocess.run(
                    ["notify-send", "--app-name=Alshival.Ai", f"Resource {health}", detail],
                    timeout=3,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass


def queue_forward(store, resource, events):
    # Per-user bounded disk queue. Backpressure never prevents local ingestion.
    for target in store.db.execute("SELECT * FROM forwarding WHERE enabled=1 AND resource_id=?", (resource,)):
        total = store.db.execute("SELECT COALESCE(SUM(bytes),0) FROM forward_queue").fetchone()[0]
        for e in events:
            data = json.dumps({"resource_id": resource, "events": [e]})
            if total + len(data.encode()) > 16 * 1024**2:
                break
            store.db.execute(
                "INSERT OR IGNORE INTO forward_queue(id,target,data,bytes) VALUES(?,?,?,?)",
                (e["id"], target["id"], data, len(data.encode())),
            )
            total += len(data.encode())


def forward_once(store, secrets=None):
    row = store.db.execute(
        "SELECT q.*,f.destination,f.credential FROM forward_queue q JOIN forwarding f ON f.id=q.target WHERE f.enabled=1 AND q.next_at<=? ORDER BY q.next_at LIMIT 1",
        (time.time(),),
    ).fetchone()
    if not row:
        return
    try:
        token = (secrets or Secrets()).get(row["credential"])
        request = urllib.request.Request(
            row["destination"],
            data=row["data"].encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        with urllib.request.build_opener(NoRedirect).open(request, timeout=8) as response:
            if not 200 <= response.status < 300:
                raise ValueError()
        store.db.execute("DELETE FROM forward_queue WHERE id=? AND target=?", (row["id"], row["target"]))
    except Exception:
        store.db.execute(
            "UPDATE forward_queue SET attempts=attempts+1,next_at=? WHERE id=? AND target=?",
            (time.time() + min(3600, 2 ** min(12, row["attempts"] + 1)), row["id"], row["target"]),
        )
