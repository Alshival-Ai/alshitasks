"""Canonical workspace identities with private, account-specific sync connections."""

from contextlib import contextmanager
import json
from .store import Problem

LOCAL = "00000000-0000-4000-8000-000000000001"


def catalog(store):
    from .workspace_icons import local_selection, selection
    local_art = local_selection(store)
    account = store.setting("active_account") or store.setting("last_account")
    counts = {r['connection']: r for r in store.db.execute(
        "SELECT connection, SUM(state='queued') AS pending, SUM(state IN ('draft','conflict')) AS trouble FROM outbox GROUP BY connection")}
    notice_state = store.setting("notification_portal:" + (account['origin'] + '|' + account['id'] if account else ''), {})
    notices_allowed = bool(store.setting('active_account') and notice_state.get('authorized')
                           and notice_state.get('generation') == store.setting('authorization_generation', 0))
    notice_counts = {r['workspace_id']: r['n'] for r in store.db.execute(
        "SELECT workspace_id, COUNT(*) n FROM app_notifications WHERE source='portal' AND account=? AND read_at IS NULL AND archived_at IS NULL GROUP BY workspace_id",
        ((account['origin'] + '|' + account['id']) if account else '',))}
    rows = [
        {
            "id": LOCAL,
            "name": "Local workspace",
            "icon": local_art['icon'],
            "icon_color": local_art['icon_color'],
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
        if store.setting('worker:graph_loop', {}).get('state') == 'retrying':
            health = dict(health, state='offline', error='Sync worker interrupted; retrying')
        live = store.setting("live:" + store.setting("credential:" + r["id"], r["id"]), {})
        incoming = int(store.setting("sync_notice:" + r["id"], 0)) > store.setting("sync_ack:" + r["id"], 0)
        rows.append(
            {
                "id": r["workspace"],
                "connection_id": r["id"],
                "name": meta.get("name", r["workspace"]),
                **{key: selection(store, r['id'])[key] for key in ('icon', 'icon_color')},
                "icon_sync": selection(store, r['id'])['sync_state'],
                "local": False,
                "kind": meta.get("kind", "customer"),
                "unread": (notice_counts.get(r["workspace"], 0) if notices_allowed and r["state"] == "connected" else 0) if "notification-sync-v1" in json.loads(r["capabilities"]).get("features", []) else meta.get("unread"),
                "portal_origin": r["origin"],
                "portal_workspace_id": r["workspace"],
                "settings_url": meta.get("settings_url", ""),
                "neural_labs_url": meta.get("neural_labs_url", "") if r["state"] == "connected" else "",
                "state": r["state"],
                "scopes": meta.get("scopes", []),
                "last_sync": r["cursor"],
                "live": live,
                "incoming": incoming,
                "retry_at": health.get("next"),
                "pending": pending,
                "attention": trouble,
                "sync": health.get("state", "pending"),
                "error": health.get("error", ""),
            }
        )
    return rows


def connection_id(store, ident):
    """Resolve a public workspace UUID in the active account; accept old IDs for saved state."""
    if ident is None or ident == LOCAL:
        return ident
    rows = catalog(store)
    matches = [w for w in rows if w['id'] == ident]
    if not matches:
        matches = [w for w in rows if w.get('connection_id') == ident]
    if len(matches) != 1:
        raise Problem('Workspace unavailable', 404)
    return matches[0]['connection_id']


def workspace_id(store, connection):
    if connection is None or connection == LOCAL:
        return LOCAL
    row = store.db.execute('SELECT workspace FROM connections WHERE id=?', (connection,)).fetchone()
    return row['workspace'] if row and row['workspace'] else connection


@contextmanager
def scope(store, ident):
    previous = getattr(store, "workspace", None)
    store.workspace = connection_id(store, ident) if ident is not None else previous
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
