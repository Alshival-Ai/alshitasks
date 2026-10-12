"""Personal local inbox and optional portal cache. Agent reads never mutate state."""

import json
import time
import threading
import weakref
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, urlsplit, parse_qs
from .store import Problem, uid, now

NAME = "notifications_read"
INPUT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {"type": "string", "maxLength": 500},
        "notification_id": {"type": "string", "format": "uuid"},
        "filter": {"type": "string", "enum": ["all", "unread", "archived"]},
        "page": {"type": "integer", "minimum": 1, "maximum": 10000},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    },
}


def migrate(s):
    s.db.executescript("""CREATE TABLE IF NOT EXISTS app_notifications(
      id TEXT PRIMARY KEY,source TEXT,account TEXT,remote_id TEXT,workspace_id TEXT,resource_id TEXT,
      event_key TEXT UNIQUE,event_type TEXT,title TEXT,body TEXT,destination TEXT,created_at TEXT,
      read_at TEXT,archived_at TEXT,toast TEXT DEFAULT 'pending',seen TEXT);
      CREATE INDEX IF NOT EXISTS app_notifications_created ON app_notifications(created_at DESC,id);
    """)


def create(s, title, body, event_type, resource_id=None, event_key=None):
    workspace = None
    if resource_id:
        r = s.db.execute("SELECT connection FROM items WHERE id=?", (resource_id,)).fetchone()
        from .workspaces import LOCAL

        workspace = (r["connection"] if r else None) or LOCAL
    ident = uid()
    s.db.execute(
        "INSERT OR IGNORE INTO app_notifications(id,source,account,workspace_id,resource_id,event_key,event_type,title,body,destination,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            ident,
            "local",
            "",
            workspace,
            resource_id,
            event_key or ident,
            event_type,
            title[:250],
            body[:12000],
            "/resource/" + resource_id if resource_id else "",
            now(),
        ),
    )
    return ident


def account(s):
    a = s.setting("active_account")
    return a, (a["origin"] + "|" + a["id"]) if a else ""


def validate(q, owner=False):
    q = dict(q)
    for key in ("page", "limit"):
        if key in q:
            try:
                if isinstance(q[key], bool):
                    raise ValueError()
                q[key] = int(q[key])
            except (TypeError, ValueError):
                raise Problem("Invalid notification pagination")
    extra = {k: q.pop(k) for k in ("source", "workspace_id") if k in q} if owner else {}
    import jsonschema

    try:
        jsonschema.validate(q, INPUT, format_checker=jsonschema.FormatChecker())
    except jsonschema.ValidationError:
        raise Problem("Invalid notification query")
    if extra.get("source", "all") not in {"all", "local", "portal"}:
        raise Problem("Invalid notification source")
    return q, extra


def rows(s, q, owner=False):
    q, extra = validate(q, owner)
    a, ident = account(s)
    status = s.setting("notification_portal:" + ident, {})
    if status.get("authorized") and time.time() - status.get("last_success", 0) > 90:
        status = dict(status, stale=True, error="Portal notifications need refreshing.")
    workspaces = status.get("workspaces", [])
    # Membership-revoked resources and other signed-in accounts never leak into the inbox.
    clauses = [
        "(n.source='local' OR (n.account=? AND ? AND (n.workspace_id IS NULL OR n.workspace_id IN (SELECT value FROM json_each(?)))))",
        "(n.resource_id IS NULL OR NOT EXISTS(SELECT 1 FROM settings WHERE key='unavailable:'||n.resource_id AND value='true'))",
    ]
    from .workspaces import catalog, connection_id, workspace_id

    clauses.append(
        "(n.source!='local' OR n.workspace_id IS NULL OR n.workspace_id IN (SELECT value FROM json_each(?)))"
    )
    args = [
        ident,
        bool(a) and status.get("authorized", False) and (owner or not status.get("stale", True)),
        json.dumps(workspaces),
        json.dumps([w.get("connection_id", w["id"]) for w in catalog(s) if w["state"] not in {"revoked", "unavailable"}]),
    ]
    unread_count = s.db.execute(
        "SELECT COUNT(*) FROM app_notifications n WHERE "
        + " AND ".join(clauses)
        + " AND n.read_at IS NULL AND n.archived_at IS NULL",
        args,
    ).fetchone()[0]
    state = q.get("filter", "all")
    clauses.append("n.archived_at IS NOT NULL" if state == "archived" else "n.archived_at IS NULL")
    if state == "unread":
        clauses.append("n.read_at IS NULL")
    for key, column in [("notification_id", "id")]:
        if q.get(key):
            clauses.append("n." + column + "=?")
            args.append(q[key])
    if q.get("query"):
        clauses.append("(instr(lower(n.title),lower(?))>0 OR instr(lower(n.body),lower(?))>0)")
        args.extend([q["query"]] * 2)
    if extra.get("source", "all") != "all":
        clauses.append("n.source=?")
        args.append(extra["source"])
    if extra.get("workspace_id"):
        ident = extra["workspace_id"]
        mapped = connection_id(s, ident)
        canonical = workspace_id(s, mapped)
        clauses.append("((n.source='local' AND n.workspace_id=?) OR (n.source!='local' AND n.workspace_id=?))")
        args.extend([mapped, canonical])
    limit = q.get("limit", 20)
    page = q.get("page", 1)
    result = s.db.execute(
        "SELECT n.* FROM app_notifications n WHERE "
        + " AND ".join(clauses)
        + " ORDER BY n.created_at DESC,n.id LIMIT ? OFFSET ?",
        [*args, limit + 1, (page - 1) * limit],
    ).fetchall()
    items = [
        dict(
            id=r["id"],
            title=r["title"],
            body=r["body"],
            event_type=r["event_type"],
            workspace_id=workspace_id(s, r["workspace_id"]) if r["source"] == "local" and r["workspace_id"] else r["workspace_id"],
            destination=r["destination"],
            created_at=r["created_at"],
            read=bool(r["read_at"]),
            archived=bool(r["archived_at"]),
        )
        for r in result[:limit]
    ]
    if owner:
        for item, row in zip(items, result):
            item.update(source=row["source"], resource_id=row["resource_id"])
    response = {"items": items, "next_page": page + 1 if len(result) > limit and page < 10000 else None}
    if owner:
        response.update(portal=status, unread_count=unread_count)
    return response


_sync_locks = weakref.WeakKeyDictionary()
_sync_guard = threading.Lock()


def sync_lock(s):
    with _sync_guard:
        return _sync_locks.setdefault(s, threading.Lock())


def sync_portal(s, force=False):
    with sync_lock(s):
        if force:
            with s.tx():
                _, key = account(s)
                state = s.setting("notification_portal:" + key, {})
                state.update(page=1, filter="all", seen=None, stale=True)
                if key:
                    s.set_setting("notification_portal:" + key, state)
        for _ in range(200 if force else 1):
            _sync_page(s, force)
            with s.lock:
                _, key = account(s)
                state = s.setting("notification_portal:" + key, {})
            if not key or not force or state.get("seen") is None:
                return
        with s.tx():
            state["stale"] = True
            s.set_setting("notification_portal:" + key, state)
        raise Problem("Notification refresh is still in progress", 503)


def _sync_page(s, force=False):
    from .portal import Portal, remote
    from .accounts import ORIGIN

    with s.lock:
        a, key = account(s)
        if not a:
            return
        state = s.setting("notification_portal:" + key, {})
        if not force and state.get("next", 0) > time.time():
            return
        generation = s.setting("authorization_generation", 0)
        adapter = Portal(s)
    try:
        with s.lock:
            connection = adapter.connection(a["connection"])
        token = adapter.token(connection)
        # Notifications use the desktop audience, never the rate-limited public MCP.
        page = state.get("page", 1)
        filter_ = state.get("filter", "all")
        result = remote(
            ORIGIN,
            "/desktop/v1/notifications?" + urlencode({"filter": filter_, "page": page, "limit": 50}),
            token,
        )
        if not isinstance(result.get("items"), list) or not isinstance(result.get("workspaces"), list):
            raise Problem("Portal notification API unavailable", 503)
        with s.tx():
            if account(s)[1] != key or s.setting("authorization_generation", 0) != generation:
                return
            first = not state.get("initialized")
            seen = state.get("seen") or uid()
            for item in result["items"]:
                remote_id = str(uuid.UUID(item["id"]))
                ident = str(uuid.uuid5(uuid.NAMESPACE_URL, key + ":notification:" + remote_id))
                s.db.execute(
                    """INSERT INTO app_notifications(id,source,account,remote_id,workspace_id,event_key,event_type,title,body,destination,created_at,read_at,archived_at,toast,seen)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,body=excluded.body,destination=excluded.destination,read_at=excluded.read_at,archived_at=excluded.archived_at,workspace_id=excluded.workspace_id,seen=excluded.seen""",
                    (
                        ident,
                        "portal",
                        key,
                        remote_id,
                        item["workspace_id"],
                        key + ":" + remote_id,
                        item["event_type"],
                        item["title"][:250],
                        item["body"][:12000],
                        item["destination"],
                        item["created_at"],
                        now() if item["read"] else None,
                        now() if item["archived"] else None,
                        "suppressed" if first or item["read"] or item["archived"] else "pending",
                        seen,
                    ),
                )
            next_page = result.get("next_page")
            complete = not next_page and filter_ == "archived"
            if complete:
                s.db.execute(
                    "DELETE FROM app_notifications WHERE source='portal' AND account=? AND seen!=?",
                    (key, seen),
                )
            state.update(
                authorized=True,
                workspaces=result["workspaces"],
                last_success=time.time(),
                error="",
                stale=not complete,
                failures=0,
                initialized=state.get("initialized", False) or complete,
                page=next_page or 1,
                filter=filter_ if next_page else ("archived" if filter_ == "all" else "all"),
                seen=None if complete else seen,
                next=time.time() + (30 if complete else 2),
            )
            s.set_setting("notification_portal:" + key, state)
    except Exception as exc:
        with s.tx():
            if account(s)[1] != key:
                return
            status = exc.status if isinstance(exc, Problem) else 503
            state.update(
                next=time.time() + min(300, 30 * 2 ** min(state.get("failures", 0), 3)),
                failures=state.get("failures", 0) + 1,
                stale=True,
                error="Portal notifications need updated account permissions or server support."
                if status in (401, 403, 404)
                else "Portal notifications are temporarily unavailable.",
            )
            if status in (401, 403):
                state["authorized"] = False
            s.set_setting("notification_portal:" + key, state)
        if force:
            raise Problem(state["error"], status)


def mutate(s, body):
    with sync_lock(s):
        return _mutate(s, body)


def _mutate(s, body):
    operation = body.get("action")
    if operation not in {"read", "unread", "archive", "restore", "read_all"}:
        raise Problem("Invalid notification action")
    with s.lock:
        a, key = account(s)
        if operation == "read_all":
            # Use the same visible owner feed and all allowed workspaces, not hidden accounts.
            targets = []
            page = 1
            while True:
                result = rows(s, {"filter": "unread", "page": page, "limit": 50}, True)
                targets.extend(result["items"])
                if not result["next_page"]:
                    break
                page += 1
        else:
            target = None
            for state in ("all", "archived"):
                found = rows(s, {"notification_id": body.get("id"), "filter": state}, True)["items"]
                if found:
                    target = found[0]
                    break
            if not target:
                raise Problem("Notification unavailable", 404)
            targets = [target]
        remote_targets = [r for r in targets if r["source"] == "portal"]
        generation = s.setting("authorization_generation", 0)
    if remote_targets:
        from .portal import Portal, remote

        adapter = Portal(s)
        c = adapter.connection(a["connection"])
        token = adapter.token(c)
        for row in remote_targets:
            with s.lock:
                rid = s.db.execute(
                    "SELECT remote_id FROM app_notifications WHERE id=?", (row["id"],)
                ).fetchone()[0]
            remote(
                c["origin"],
                "/desktop/v1/notifications/actions",
                token,
                {"id": rid, "action": "read" if operation == "read_all" else operation},
            )
    with s.tx():
        if account(s)[1] != key or s.setting("authorization_generation", 0) != generation:
            raise Problem("Account changed; refresh notifications", 409)
        for row in targets:
            column = "archived_at" if operation in {"archive", "restore"} else "read_at"
            value = None if operation in {"unread", "restore"} else now()
            s.db.execute("UPDATE app_notifications SET " + column + "=? WHERE id=?", (value, row["id"]))
            if operation in {"read", "read_all", "archive"}:
                s.db.execute(
                    "UPDATE app_notifications SET toast='suppressed' WHERE id=? AND toast='pending'",
                    (row["id"],),
                )
    return {"updated": len(targets)}


def request(s, path, method, body, owner, token):
    from .security import authorize

    url = urlsplit(path)
    q = {k: v[-1] for k, v in parse_qs(url.query).items()}
    if not isinstance(body, dict):
        raise Problem("JSON object required")
    agent_read = q.pop("agent_read", None) == "1" or not owner
    if not owner:
        with s.lock:
            grant = authorize(s, token, "notifications:read")
            if grant["resource_id"] or method != "GET":
                raise Problem("Personal read access required", 403)
    if agent_read:
        # Validate current portal authorization before exposing cached portal alerts to agents.
        if account(s)[0]:
            try:
                sync_portal(s, force=True)
            except Problem:
                pass
    if method == "GET" and url.path == "/v1/notifications":
        with s.lock:
            return rows(s, q, owner and not agent_read)
    if owner and method == "POST" and url.path == "/v1/notifications/actions":
        return mutate(s, body)
    if owner and method == "POST" and url.path == "/v1/notifications/refresh":
        sync_portal(s, force=True)
        return {"refreshed": True}
    raise Problem("Notification operation unavailable", 403)


def pending(s):
    with s.tx():
        s.db.execute(
            "UPDATE app_notifications SET toast='suppressed' WHERE toast='pending' AND (read_at IS NOT NULL OR archived_at IS NOT NULL)"
        )
        if not s.setting("notification_toasts", True):
            s.db.execute("UPDATE app_notifications SET toast='suppressed' WHERE toast='pending'")
            return []
        visible_ids = []
        page = 1
        while True:
            result = rows(s, {"filter": "unread", "limit": 50, "page": page}, True)
            visible_ids.extend(
                r["id"]
                for r in result["items"]
                if r["source"] == "local" or not result.get("portal", {}).get("stale", True)
            )
            if not result["next_page"]:
                break
            page += 1
        records = s.db.execute(
            "SELECT * FROM app_notifications WHERE toast='pending' AND id IN (SELECT value FROM json_each(?)) ORDER BY created_at LIMIT 5",
            (json.dumps(visible_ids),),
        ).fetchall()
        # Claim before the external side effect: a crash can lose a toast, never its inbox item.
        s.db.executemany(
            "UPDATE app_notifications SET toast='claimed' WHERE id=?", [(r["id"],) for r in records]
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        s.db.execute(
            "DELETE FROM app_notifications WHERE created_at<? AND (resource_id IS NULL OR NOT EXISTS(SELECT 1 FROM health_incidents h WHERE h.resource_id=app_notifications.resource_id AND h.closed_at IS NULL))",
            (cutoff,),
        )
        return [dict(r) for r in records]
