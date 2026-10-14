"""One optional Alshival account; automatic permission-scoped workspace discovery."""

import json
import time
from .store import Problem, uid
from .security import Secrets

ORIGIN = "https://alshival.ai"


def activate(store, ident, capabilities):
    capabilities = dict(capabilities)
    capabilities.pop('navigation', None)
    c = dict(store.db.execute("SELECT * FROM connections WHERE id=?", (ident,)).fetchone())
    account = {
        "id": str(capabilities["account_id"]),
        "origin": c["origin"],
        "name": capabilities.get("account_name", "Alshival.Ai account"),
        "connection": ident,
    }
    # A callback cannot silently replace another signed-in account.
    current = store.setting("active_account")
    if current and (current["id"], current["origin"]) != (account["id"], account["origin"]):
        raise Problem("Sign out before switching accounts", 409)
    store.db.execute('UPDATE connections SET capabilities=? WHERE id=?', (json.dumps(capabilities), ident))
    for row in store.db.execute("SELECT * FROM connections WHERE workspace!=''").fetchall():
        if (row["origin"], row["account"]) == (account["origin"], account["id"]):
            store.set_setting("credential:" + row["id"], ident)
            # Discovery reactivates only workspaces whose permissions still exist.
            store.db.execute("UPDATE connections SET state='disconnected' WHERE id=?", (row["id"],))
        elif row["state"] == "connected":
            store.db.execute("UPDATE connections SET state='disconnected' WHERE id=?", (row["id"],))
    store.set_setting("active_account", account)
    store.set_setting("account_navigation", [])
    store.set_setting("last_account", account)
    store.set_setting("discovery", {"next": 0})
    store.set_setting("publish_offer", True)


def signout(store):
    from .managed import enabled
    if enabled(store):
        raise Problem('Connected through your Ubuntu account', 409)
    store.set_setting("authorization_generation", store.setting("authorization_generation", 0) + 1)
    for pending in store.db.execute("SELECT id FROM connections WHERE state='authorizing'").fetchall():
        store.db.execute("UPDATE connections SET state='disconnected' WHERE id=?", (pending[0],))
        with store.token_lock:
            Secrets().delete(pending[0])
    current = store.setting("active_account")
    if not current:
        return {"signed_out": True}
    credentials = set()
    for row in store.db.execute(
        "SELECT * FROM connections WHERE origin=? AND account=?", (current["origin"], current["id"])
    ).fetchall():
        credentials.add(store.setting("credential:" + row["id"], row["id"]))
        store.db.execute("UPDATE connections SET state='disconnected' WHERE id=?", (row["id"],))
        store.db.execute(
            "UPDATE outbox SET state='draft',reason='Signed out; retained locally' WHERE connection=? AND state='queued'",
            (row["id"],),
        )
    for credential in credentials:
        with store.token_lock:
            Secrets().delete(credential)
    store.set_setting("active_account", None)
    store.set_setting("account_navigation", [])
    return {"signed_out": True, "local_copies_retained": True}


def discover(store, transport=None):
    from .managed import reconcile
    reconcile(store, transport)
    from .portal import Portal, remote

    transport = transport or remote
    with store.lock:
        account = store.setting("active_account")
        schedule = store.setting("discovery", {})
        if not account or schedule.get("next", 0) > time.time():
            return
        c = Portal(store).connection(account["connection"])
    try:
        token = Portal(store).token(c)
        capabilities = transport(c["origin"], "/desktop/v1/capabilities", token)
        if (
            capabilities.get("resource") != c["origin"] + "/desktop/v1"
            or str(capabilities.get("account_id")) != c["account"]
        ):
            raise Problem("Account capability identity changed; sign in again", 401)
        capabilities = dict(capabilities)
        capabilities.pop('navigation', None)
        encoded = json.dumps(capabilities)
        rows = transport(c["origin"], "/desktop/v1/workspaces", token)["workspaces"]
        if not isinstance(rows, list) or any(not all(k in w for k in ("id", "name", "scopes")) for w in rows):
            raise Problem("Incomplete workspace discovery", 502)
        with store.tx():
            if store.setting("active_account") != account:
                return
            store.db.execute(
                "UPDATE connections SET capabilities=? WHERE origin=? AND account=?",
                (encoded, c["origin"], c["account"]),
            )
            store.set_setting("account_navigation", [])
            seen = set()
            for w in rows:
                if "project:read" not in w["scopes"]:
                    continue
                existing = store.db.execute(
                    "SELECT id FROM connections WHERE origin=? AND account=? AND workspace=? ORDER BY rowid LIMIT 1",
                    (c["origin"], c["account"], w["id"]),
                ).fetchone()
                ident = existing[0] if existing else uid()
                if not existing:
                    store.db.execute(
                        "INSERT INTO connections VALUES(?,?,?,?,?,?,?)",
                        (ident, c["origin"], c["account"], w["id"], "connected", None, encoded),
                    )
                else:
                    store.db.execute("UPDATE connections SET state='connected' WHERE id=?", (ident,))
                store.set_setting("credential:" + ident, account["connection"])
                from .workspace_art import appearance
                from .workspace_icons import receive
                receive(store, ident, w)
                store.set_setting("workspace:" + ident, dict(w, **appearance(w['id'], w.get('icon'), w.get('icon_color'))))
                seen.add(ident)
            for r in store.db.execute(
                "SELECT id FROM connections WHERE origin=? AND account=? AND workspace!=''",
                (c["origin"], c["account"]),
            ).fetchall():
                if r[0] not in seen:
                    store.db.execute("UPDATE connections SET state='revoked' WHERE id=?", (r[0],))
                    store.db.execute(
                        "UPDATE outbox SET state='draft',reason='Workspace access removed' WHERE connection=? AND state='queued'",
                        (r[0],),
                    )
            store.set_setting("discovery", {"next": time.time() + 30, "state": "connected"})
    except Problem as e:
        with store.tx():
            if store.setting("active_account") != account:
                return
            attempt = schedule.get("attempt", 0) + 1
            store.set_setting(
                "discovery",
                {
                    "next": time.time() + min(300, 2 ** min(attempt, 8)),
                    "attempt": attempt,
                    "state": "sign-in-required" if e.status in (401, 403) else "offline",
                    "error": str(e),
                },
            )


def dispatch(store, path, method, body):
    from .managed import enabled
    if path == "/v1/portal/account" and method == "GET":
        return {
            "managed": enabled(store),
            "navigation": [],  # Compatibility with older desktop clients; administration is portal-only.
            "account": store.setting("active_account"),
            "status": store.setting("discovery", {}),
            "offer_publish": store.setting("publish_offer", False),
        }
    if path == "/v1/portal/signout" and method == "POST":
        return signout(store)
    if path == "/v1/portal/retry" and method == "POST":
        store.set_setting('managed_next', 0)
        store.set_setting("discovery", {"next": 0})
        for r in store.db.execute("SELECT id FROM connections"):
            store.set_setting("sync:" + r[0], {"next": 0})
        store.db.execute("UPDATE connections SET cursor=NULL WHERE state='connected'")
        store.sync_wake.set()
        return {"message": "Sync scheduled"}
    if path == "/v1/portal/publish-offer" and method == "POST":
        store.set_setting("publish_offer", False)
        return {"saved": True}
    raise Problem("Account endpoint unavailable", 404)


def adopt_legacy(store):
    """Preserve existing account/workspace identities during the UI upgrade."""
    if store.setting("accounts_initialized"):
        return
    rows = [
        dict(r)
        for r in store.db.execute(
            "SELECT * FROM connections WHERE origin=? AND account!='' ORDER BY rowid DESC", (ORIGIN,)
        )
    ]
    if rows and not store.setting("last_account"):
        first = rows[0]
        root = first["id"]
        if first["workspace"]:
            root = uid()
            store.db.execute(
                "INSERT INTO connections VALUES(?,?,?,?,?,?,?)",
                (root, ORIGIN, first["account"], "", first["state"], None, first["capabilities"]),
            )
            store.set_setting("credential:" + root, store.setting("credential:" + first["id"], first["id"]))
        account = {
            "id": first["account"],
            "origin": ORIGIN,
            "name": "Alshival.Ai account",
            "connection": root,
        }
        store.set_setting("last_account", account)
        if (
            len({r["account"] for r in rows if r["state"] == "connected"}) == 1
            and first["state"] == "connected"
        ):
            store.set_setting("active_account", account)
        else:
            store.db.execute("UPDATE connections SET state='disconnected' WHERE origin=?", (ORIGIN,))
    store.set_setting("accounts_initialized", True)
