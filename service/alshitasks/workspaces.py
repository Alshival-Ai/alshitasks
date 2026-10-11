"""Local workspace identities, independent of UI selection and portal IDs."""

from contextlib import contextmanager
from .store import Problem

LOCAL = "00000000-0000-4000-8000-000000000001"


def catalog(store):
    account = store.setting("active_account") or store.setting("last_account")
    counts = {r['connection']: r for r in store.db.execute(
        "SELECT connection, SUM(state='queued') AS pending, SUM(state IN ('draft','conflict')) AS trouble FROM outbox GROUP BY connection")}
    rows = [
        {
            "id": LOCAL,
            "name": "Local workspace",
            "local": True,
            "state": "local",
            "scopes": ["project:read", "project:write"],
        }
    ]
    for r in store.db.execute("SELECT * FROM connections WHERE workspace!='' ORDER BY rowid"):
        if account and (r["origin"] != account["origin"] or r["account"] != account["id"]):
            continue
        if not account:
            continue
        meta = store.setting("workspace:" + r["id"], {})
        tally = counts.get(r['id'])
        pending = tally['pending'] if tally else 0
        trouble = tally['trouble'] if tally else 0
        health = store.setting("sync:" + r["id"], {})
        rows.append(
            {
                "id": r["id"],
                "name": meta.get("name", r["workspace"]),
                "local": False,
                "kind": meta.get("kind", "customer"),
                "unread": meta.get("unread"),
                "portal_origin": r["origin"],
                "portal_workspace_id": r["workspace"],
                "settings_url": meta.get("settings_url", ""),
                "state": r["state"],
                "scopes": meta.get("scopes", []),
                "last_sync": r["cursor"],
                "pending": pending,
                "attention": trouble,
                "sync": health.get("state", "pending"),
                "error": health.get("error", ""),
            }
        )
    return rows


@contextmanager
def scope(store, ident):
    previous = getattr(store, "workspace", None)
    if ident is not None and ident not in {w["id"] for w in catalog(store)}:
        raise Problem("Workspace unavailable", 404)
    store.workspace = ident if ident is not None else previous
    try:
        yield
    finally:
        store.workspace = previous


def visible(store, connection):
    selected = getattr(store, "workspace", None)
    return selected is None or (connection is None if selected == LOCAL else connection == selected)


def require_item(store, ident):
    row = store.db.execute("SELECT connection,data FROM items WHERE id=?", (ident,)).fetchone()
    if not row or not visible(store, row["connection"]) or store.setting("unavailable:" + ident, False):
        raise Problem("Record unavailable in this workspace", 404)
    return row


def writable(store, ident):
    import json

    row = require_item(store, ident)
    data = json.loads(row["data"])
    board = data.get("board_id") or (
        ident
        if store.db.execute("SELECT kind FROM items WHERE id=?", (ident,)).fetchone()[0] == "board"
        else LOCAL
    )
    if store.setting("publishing:" + board):
        raise Problem("Board publication is in progress; retry after it completes", 409)
