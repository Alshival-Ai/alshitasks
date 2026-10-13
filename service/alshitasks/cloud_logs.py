"""Cloud-shaped logging routes over the local store, with canonical resource IDs."""

import asyncio
import base64
import hashlib
import hmac
import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, parse_qs
from pydantic import ValidationError
from .cloud_contract.events import Batch, normalize, otlp_events, read_body, MAX_BODY
from .cloud_contract.errors import APIError
from .security import authorize
from .store import Problem
from .workspaces import scope, require_item, LOCAL, workspace_id

ROUTE = re.compile(r"^/v1/workspaces/([^/]+)/resources/([^/]+)/(logs|otlp/v1/logs)$")
LEVELS = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL")
TO_CLOUD = {"warning": "WARN", "critical": "FATAL"}
TO_LOCAL = {"WARN": "warning", "FATAL": "critical"}


def severity(value):
    return TO_CLOUD.get(value, value.upper())


def identity(store, resource):
    row = store.db.execute("SELECT connection,remote_id FROM items WHERE id=?", (resource,)).fetchone()
    return workspace_id(store, row["connection"]), row["remote_id"] or resource


def resolve(store, workspace, resource):
    try:
        workspace = str(uuid.UUID(workspace))
        resource = str(uuid.UUID(resource))
    except ValueError:
        raise APIError(400, "invalid_request", "Workspace and resource must be UUIDs.")
    with scope(store, workspace):
        rows = store.db.execute(
            "SELECT id,connection,remote_id,data FROM items WHERE kind='resource' AND (remote_id=? OR (remote_id IS NULL AND id=?))",
            (resource, resource),
        ).fetchall()
        matches = []
        for row in rows:
            try:
                require_item(store, row["id"])
            except Problem:
                continue
            data = json.loads(row["data"])
            if data.get("deleted"):
                continue
            matches.append(row)
        if len(matches) != 1:
            raise APIError(404, "not_found", "Resource unavailable in this workspace.")
        return matches[0]["id"]


def access(api, match, method, owner, token):
    workspace, public, transport = match.groups()
    if method not in ("GET", "POST") or (transport != "logs" and method != "POST"):
        raise APIError(405, "method_not_allowed", "Unsupported log operation.")
    # Validate credentials before looking up resource membership.
    if not owner:
        digest = hashlib.sha256(token.encode()).hexdigest()
        grant = api.store.db.execute(
            "SELECT resource_id FROM grants WHERE digest=? AND revoked_at IS NULL", (digest,)
        ).fetchone()
        if not grant:
            raise Problem("Authentication required", 401)
    resource = resolve(api.store, workspace, public)
    if not owner:
        authorize(api.store, token, "logs:write" if method == "POST" else "logs:read", resource)
    return resource


async def decode(raw, encoding):
    class Request:
        headers = {"content-encoding": encoding}

        async def stream(self):
            yield raw

    return await read_body(Request())


def ingest(api, resource, raw, content_type, encoding, otlp, request_id):
    raw = asyncio.run(decode(raw, encoding))
    now = datetime.now(timezone.utc)
    try:
        if otlp:
            events = otlp_events(raw, content_type)
        else:
            if content_type != "application/json":
                raise APIError(415, "unsupported_type", "Use application/json.")
            events = Batch.model_validate_json(raw).events
        normalized = normalize(events, now)
    except (ValidationError, ValueError, OverflowError, RecursionError):
        raise APIError(400, "invalid_events", "Invalid log fields. Check the batch schema.") from None
    records = []
    forwarding = []
    for data, size in normalized:
        ident = str(uuid.uuid4())
        records.append(
            (
                ident,
                resource,
                data["timestamp"],
                TO_LOCAL.get(data["severity"], data["severity"].lower()),
                data["message"],
                json.dumps(data["attributes"]),
                size,
                now.isoformat(),
                ident,
                data["source"],
                data["trace_id"],
                data["span_id"],
                "cloud",
            )
        )
        forwarding.append(dict(data, id=ident))
    with api.logs.db:
        api.logs.db.executemany(
            "INSERT INTO logs(id,resource,timestamp,severity,message,attributes,bytes,received_at,cloud_id,source,trace_id,span_id,transport) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            records,
        )
    api.logs.retain(**api.store.setting("retention", {"days": 7, "max_bytes": 1024**3}))
    from .monitoring import queue_forward

    queue_forward(api.store, resource, forwarding)
    return {"accepted": len(events), "request_id": request_id}


def _time(value):
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not d.tzinfo:
            raise ValueError()
        return d.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, AttributeError):
        raise APIError(400, "invalid_time", "Time filters require a time zone.")


def query(api, workspace, resource, filters):
    workspace = str(uuid.UUID(workspace))
    db = api.logs.db
    try:
        limit = int(filters.get("limit", 100))
    except (ValueError, TypeError):
        raise APIError(400, "invalid_request", "Limit must be 1..200.")
    if not 1 <= limit <= 200:
        raise APIError(400, "invalid_request", "Limit must be 1..200.")
    if filters.get("severity") and filters["severity"] not in LEVELS:
        raise APIError(400, "invalid_request", "Invalid severity.")
    if len(filters.get("q", "")) > 500 or (
        filters.get("trace_id") and not re.fullmatch("[0-9a-f]{32}", filters["trace_id"])
    ):
        raise APIError(400, "invalid_request", "Invalid log filter.")
    selected = {k: v for k, v in filters.items() if k in ("start", "end", "severity", "q", "trace_id") and v}
    context = [workspace, resource, selected]
    secret = db.execute("SELECT value FROM log_meta WHERE key='cursor_secret'").fetchone()[0].encode()
    upper = datetime.now(timezone.utc).isoformat()
    clauses = ["resource=?"]
    args = [resource]
    if filters.get("cursor"):
        try:
            if len(filters["cursor"]) > 8192:
                raise ValueError()
            raw = base64.urlsafe_b64decode(filters["cursor"])
            sig, body = raw[:32], raw[32:]
            if not hmac.compare_digest(sig, hmac.new(secret, body, hashlib.sha256).digest()):
                raise ValueError()
            cur = json.loads(body)
            if cur["context"] != context or cur["expires"] < time.time():
                raise ValueError()
            upper = cur["upper"]
            clauses.append("(received_at,cloud_id)<(?,?)")
            args.extend([cur["time"], cur["id"]])
        except Exception:
            raise APIError(400, "invalid_cursor", "Invalid, expired, or mismatched cursor.") from None
    clauses.append("received_at<=?")
    args.append(upper)
    for k, op in [("start", ">="), ("end", "<=")]:
        if selected.get(k):
            clauses.append("timestamp" + op + "?")
            args.append(_time(selected[k]))
    if selected.get("severity"):
        clauses.append("severity=?")
        args.append(TO_LOCAL.get(selected["severity"], selected["severity"].lower()))
    if selected.get("trace_id"):
        clauses.append("trace_id=?")
        args.append(selected["trace_id"])
    if selected.get("q"):
        db.create_function("casefold", 1, lambda x: x.casefold())
        clauses.append("instr(casefold(message),casefold(?))>0")
        args.append(selected["q"])
    rows = db.execute(
        "SELECT * FROM logs WHERE "
        + " AND ".join(clauses)
        + " ORDER BY received_at DESC,cloud_id DESC LIMIT ?",
        [*args, limit + 1],
    ).fetchall()
    token = None
    if len(rows) > limit:
        last = rows[limit - 1]
        body = json.dumps(
            dict(
                context=context,
                expires=time.time() + 3600,
                upper=upper,
                time=last["received_at"],
                id=last["cloud_id"],
            ),
            sort_keys=True,
        ).encode()
        token = base64.urlsafe_b64encode(hmac.new(secret, body, hashlib.sha256).digest() + body).decode()
    retention = api.store.setting("retention", {"days": 7, "max_bytes": 1024**3})
    with scope(api.store, workspace):
        from .workspaces import visible

        resources = [
            r["id"]
            for r in api.store.db.execute("SELECT id,connection FROM items WHERE kind='resource'")
            if visible(api.store, r["connection"]) and not api.store.setting("unavailable:" + r["id"], False)
        ]
    usage = db.execute(
        "SELECT COALESCE(SUM(bytes),0),COUNT(*),MIN(received_at) FROM logs WHERE resource IN (SELECT value FROM json_each(?))",
        (json.dumps(resources),),
    ).fetchone()
    return {
        "items": [
            dict(
                id=r["cloud_id"],
                received_at=r["received_at"],
                timestamp=r["timestamp"],
                severity=severity(r["severity"]),
                message=r["message"],
                source=r["source"],
                attributes=json.loads(r["attributes"]),
                trace_id=r["trace_id"],
                span_id=r["span_id"],
            )
            for r in rows[:limit]
        ],
        "next_cursor": token,
        "retention": dict(
            cutoff=(datetime.now(timezone.utc) - timedelta(days=retention["days"])).isoformat(),
            retention_days=retention["days"],
            quota_bytes=retention["max_bytes"],
            used_bytes=usage[0],
            events=usage[1],
            oldest_received_at=usage[2],
        ),
    }


def dispatch(api, path, method, body, owner, token):
    match = ROUTE.fullmatch(urlsplit(path).path)
    resource = access(api, match, method, owner, token)
    if method == "GET":
        return query(api, match[1], resource, {k: v[-1] for k, v in parse_qs(urlsplit(path).query).items()})
    return ingest(
        api,
        resource,
        json.dumps(body).encode(),
        "application/json",
        "identity",
        match[3] != "logs",
        str(uuid.uuid4()),
    )
