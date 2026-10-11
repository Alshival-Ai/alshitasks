"""Optional user OAuth adapter and revision-based offline cache/outbox."""

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit, urlencode, parse_qs
from .store import Problem, DEFAULT, uid, now
from .security import Secrets
from .monitoring import NoRedirect

MAX_RESPONSE = 8 * 1024**2


def remote(origin, path, token=None, body=None, form=False):
    u = urlsplit(origin)
    if (
        u.scheme != "https"
        or not u.hostname
        or u.username
        or u.password
        or u.path not in ("", "/")
        or u.query
        or u.fragment
    ):
        raise Problem("HTTPS portal origin required")
    if not path.startswith("/") or path.startswith("//"):
        raise Problem("Invalid portal path")
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = (urlencode(body) if form else json.dumps(body)).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    try:
        with urllib.request.build_opener(NoRedirect).open(
            urllib.request.Request(origin + path, data=data, headers=headers), timeout=15
        ) as r:
            data = r.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE:
                raise Problem("Portal response too large", 413)
            return json.loads(data)
    except urllib.error.HTTPError as e:
        raise Problem(
            "Portal access rejected"
            if e.code in (401, 403)
            else "Portal revision conflict"
            if e.code == 409
            else "Portal request failed",
            e.code,
        )
    except (ValueError, OSError):
        raise Problem("Portal unavailable; offline edits are retained", 503)


def local_id(connection, remote_id):
    return str(uuid.uuid5(uuid.UUID(connection), remote_id))


def cached_item_id(store, connection, remote_id):
    row = store.db.execute(
        "SELECT id FROM items WHERE connection=? AND remote_id=?", (connection, remote_id)
    ).fetchone()
    return row["id"] if row else local_id(connection, remote_id)


def merge(base, local, shared):
    """Field-level three-way merge. Arrays and nested objects remain atomic."""
    keys = set(base) | set(local) | set(shared)
    changed_local = {k for k in keys if local.get(k) != base.get(k)}
    changed_shared = {k for k in keys if shared.get(k) != base.get(k)}
    if any(local.get(k) != shared.get(k) for k in changed_local & changed_shared):
        return None
    return {**shared, **{k: local.get(k) for k in changed_local}}


def encode_data(store, connection, data):
    data = {k: v for k, v in data.items() if k not in ("position", "resource_id")}
    for field in ("board_id", "parent_id", "status_id"):
        if data.get(field):
            row = store.db.execute(
                "SELECT remote_id FROM items WHERE id=? AND connection=?", (data[field], connection)
            ).fetchone()
            if row:
                data[field] = row["remote_id"]
            elif field == "status_id":
                data[field] = store.setting("status_remote:" + data[field], data[field])
            else:
                raise Problem("Shared record references a local-only record", 409)
    for field in ("assignee", "reviewer"):
        if data.get(field):
            remote_member = store.setting("member_remote:" + connection + ":" + str(data[field]))
            if not remote_member:
                raise Problem("Choose a member of this shared workspace", 409)
            data[field] = remote_member
    if data.get("visibility") == "private":
        data["visibility"] = "internal"
    return data


def decode(store, connection, record):
    data = dict(DEFAULT, **record["data"])
    for f in ("board_id", "parent_id", "status_id"):
        if data.get(f):
            data[f] = (
                store.setting("status_local:" + connection + ":" + data[f], local_id(connection, data[f]))
                if f == "status_id"
                else cached_item_id(store, connection, data[f])
            )
    for field in ("assignee", "reviewer"):
        if data.get(field):
            data[field] = "member:" + local_id(connection, str(data[field]))
    # Portal default-board records without board_id belong to their cached board.
    if record["kind"] != "board" and not data["board_id"]:
        data["board_id"] = store.setting("cached_board:" + connection)
    existing = store.db.execute(
        "SELECT id FROM items WHERE connection=? AND remote_id=?", (connection, record["id"])
    ).fetchone()
    return dict(
        record,
        id=existing["id"] if existing else local_id(connection, record["id"]),
        data=data,
        author_id=record.get("author_id", "portal"),
    )


class Portal:
    def __init__(self, store):
        self.store = store

    def connection(self, ident):
        r = self.store.db.execute("SELECT * FROM connections WHERE id=?", (ident,)).fetchone()
        if not r:
            raise Problem("Connection not found", 404)
        return dict(r)

    def token(self, c):
        # Rotation is serialized across all workspace views of the same account credential.
        with self.store.lock:
            return self._token(c)

    def _token(self, c):
        if c["state"] not in ("connected", "syncing"):
            raise Problem("Connection is disconnected; sign in again", 401)
        credential = self.store.setting("credential:" + c["id"], c["id"])
        value = json.loads(Secrets().get(credential))
        if value.get("expires_at", 0) < time.time() + 60:
            if not value.get("refresh_token"):
                raise Problem("Sign in again to refresh authorization", 401)
            refreshed = remote(
                c["origin"],
                "/oauth/token/",
                body={
                    "grant_type": "refresh_token",
                    "refresh_token": value["refresh_token"],
                    "client_id": value["client_id"],
                    "resource": c["origin"] + "/desktop/v1",
                },
                form=True,
            )
            value.update(refreshed, expires_at=time.time() + refreshed.get("expires_in", 3600))
            Secrets().put(credential, json.dumps(value))
        return value["access_token"]

    def connect(self, origin):
        from .accounts import ORIGIN

        if origin != ORIGIN:
            raise Problem("Sign-in is available only through Alshival.Ai")
        if self.store.setting("active_account"):
            raise Problem("Sign out before signing into another account", 409)
        if self.store.db.execute("SELECT 1 FROM connections WHERE state='authorizing'").fetchone():
            raise Problem("Sign-in is already open in your browser", 409)
        generation = self.store.setting("authorization_generation", 0)
        ident = uid()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        # Verify keyring access before initiating browser consent.
        Secrets().put(ident, json.dumps({"pending": True}))
        store = self.store
        finished = threading.Event()

        class Callback(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                q = parse_qs(urlsplit(self.path).query)
                if (
                    urlsplit(self.path).path != "/callback"
                    or q.get("state") != [state]
                    or q.get("iss") != [origin]
                ):
                    self.send_error(400, "Invalid OAuth callback")
                    return
                try:
                    if time.time() > deadline:
                        raise Problem("Login expired")
                    with finish_lock:
                        if completed[0]:
                            raise Problem("Callback already used")
                        completed[0] = True
                    tokens = remote(
                        origin,
                        "/oauth/token/",
                        body={
                            "grant_type": "authorization_code",
                            "client_id": client["client_id"],
                            "code": q["code"][0],
                            "code_verifier": verifier,
                            "redirect_uri": redirect,
                            "resource": origin + "/desktop/v1",
                        },
                        form=True,
                    )
                    capabilities = remote(origin, "/desktop/v1/capabilities", tokens["access_token"])
                    if capabilities.get("resource") != origin + "/desktop/v1":
                        raise Problem("Desktop audience mismatch")
                    Secrets().put(
                        ident,
                        json.dumps(
                            dict(
                                tokens,
                                client_id=client["client_id"],
                                expires_at=time.time() + tokens.get("expires_in", 3600),
                            )
                        ),
                    )
                    with store.tx():
                        if store.setting("authorization_generation", 0) != generation:
                            raise Problem("Sign-in was cancelled", 409)
                        store.db.execute(
                            "UPDATE connections SET account=?,state='connected',capabilities=? WHERE id=?",
                            (str(capabilities["account_id"]), json.dumps(capabilities), ident),
                        )
                        from .accounts import activate

                        activate(store, ident, capabilities)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(
                        b"Signed in. Return to Alshival.Ai; your workspaces will sync automatically."
                    )
                except Exception:
                    with store.tx():
                        store.db.execute(
                            "UPDATE connections SET state='authorization-failed' WHERE id=?", (ident,)
                        )
                    Secrets().delete(ident)
                    self.send_error(400, "Unable to finish sign-in; retry from Alshival.Ai")
                finally:
                    finished.set()

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Callback)
        redirect = f"http://127.0.0.1:{server.server_port}/callback"
        deadline = time.time() + 300
        completed = [False]
        finish_lock = threading.Lock()
        try:
            client = remote(
                origin,
                "/oauth/register/",
                body={
                    "client_name": "Alshival.Ai",
                    "application_type": "native",
                    "redirect_uris": [redirect],
                    "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                },
            )
            self.store.db.execute(
                "INSERT INTO connections VALUES(?,?,?,?,?,?,?)",
                (ident, origin, "", "", "authorizing", None, "{}"),
            )
        except Exception:
            server.server_close()
            Secrets().delete(ident)
            raise
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def expire():
            if not finished.wait(300):
                try:
                    with store.tx():
                        store.db.execute(
                            "UPDATE connections SET state='authorization-expired' WHERE id=? AND state='authorizing'",
                            (ident,),
                        )
                    Secrets().delete(ident)
                except Exception:
                    pass
            server.shutdown()
            server.server_close()

        threading.Thread(target=expire, daemon=True).start()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return {
            "connection": ident,
            "url": origin
            + "/oauth/authorize/?"
            + urlencode(
                {
                    "client_id": client["client_id"],
                    "redirect_uri": redirect,
                    "response_type": "code",
                    "scope": "project:read tasks:write tasks:manage board:write project:write project:comment notes:write resources:read resources:write logs:read offline_access",
                    "resource": origin + "/desktop/v1",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": state,
                }
            ),
        }

    def dispatch(self, path, method, body, q):
        if path == "/v1/portal/publish":
            from .publishing import dispatch

            return dispatch(self.store, method, body)
        if path in (
            "/v1/portal/account",
            "/v1/portal/signout",
            "/v1/portal/retry",
            "/v1/portal/publish-offer",
        ):
            from .accounts import dispatch

            return dispatch(self.store, path, method, body)
        if path == "/v1/portal/connections":
            return {
                "connections": [
                    dict(r)
                    for r in self.store.db.execute(
                        "SELECT id,origin,account,workspace,state,cursor FROM connections"
                    )
                ]
            }
        if path == "/v1/portal/connect" and method == "POST":
            from .accounts import ORIGIN

            return self.connect(ORIGIN)
        if path in ("/v1/portal/logs", "/v1/portal/monitoring") and method == "GET":
            resource = q.get("resource_id")
            row = self.store.db.execute(
                "SELECT connection,remote_id FROM items WHERE id=? AND kind='resource'", (resource,)
            ).fetchone()
            if not row or not row["connection"]:
                raise Problem("Choose a connected workspace resource")
            c = self.connection(row["connection"])
            token = self.token(c)
            if path.endswith("/logs"):
                filters = {
                    k: v for k, v in q.items() if k in ("since", "until", "severity", "text", "limit") and v
                }
                filters.update(resource_id=row["remote_id"], limit=min(100, max(1, int(q.get("limit", 100)))))
                return remote(
                    c["origin"],
                    "/desktop/v1/workspaces/" + c["workspace"] + "/logs?" + urlencode(filters),
                    token,
                )
            result = remote(c["origin"], "/desktop/v1/workspaces/" + c["workspace"] + "/monitoring", token)
            return {"resources": [r for r in result["resources"] if r["id"] == row["remote_id"]]}
        c = self.connection(body.get("connection") or q.get("connection"))
        if path == "/v1/portal/disconnect" and method == "POST":
            self.store.db.execute("UPDATE connections SET state='disconnected' WHERE id=?", (c["id"],))
            self.store.db.execute(
                "UPDATE outbox SET state='draft',reason='Disconnected' WHERE connection=? AND state='queued'",
                (c["id"],),
            )
            credential = self.store.setting("credential:" + c["id"], c["id"])
            active = [
                dict(r)
                for r in self.store.db.execute(
                    "SELECT * FROM connections WHERE state IN ('connected','syncing')"
                )
            ]
            if not any(self.store.setting("credential:" + r["id"], r["id"]) == credential for r in active):
                Secrets().delete(credential)
            return {"disconnected": True, "local_copies_retained": True}
        token = self.token(c)
        if path == "/v1/portal/workspaces":
            return remote(c["origin"], "/desktop/v1/workspaces", token)
        if path == "/v1/portal/select" and method == "POST":
            available = remote(c["origin"], "/desktop/v1/workspaces", token)["workspaces"]
            if body["workspace"] not in [w["id"] for w in available]:
                raise Problem("Workspace permission unavailable", 403)
            # Separate cache connection per selected workspace; keep the original identity/account.
            if c["workspace"] and c["workspace"] != body["workspace"]:
                new = uid()
                self.store.set_setting(
                    "credential:" + new, self.store.setting("credential:" + c["id"], c["id"])
                )
                self.store.db.execute(
                    "INSERT INTO connections VALUES(?,?,?,?,?,?,?)",
                    (new, c["origin"], c["account"], body["workspace"], "connected", None, c["capabilities"]),
                )
                return {"selected": new}
            self.store.db.execute(
                "UPDATE connections SET workspace=?,cursor=NULL WHERE id=?", (body["workspace"], c["id"])
            )
            return {"selected": True}
        raise Problem("Portal endpoint unavailable", 404)


def sync_connection(store, c, transport=remote, token=None):
    adapter = Portal(store)
    token = token or adapter.token(c)
    prefix = "/desktop/v1/workspaces/" + c["workspace"]
    records = []
    seen = set()
    cursor = ""
    statuses = []
    edges = []
    status_revision = None
    members = []
    attachments = []
    status_revisions = {}
    default_board = None
    while True:
        if cursor in seen:
            raise Problem("Snapshot cursor repeated", 409)
        seen.add(cursor)
        page = transport(
            c["origin"], prefix + "/snapshot" + ("?" + urlencode({"cursor": cursor}) if cursor else ""), token
        )
        records.extend(page["items"])
        attachments.extend(page.get("attachments", []))
        status_revisions.update(page.get("status_revisions", {}))
        default_board = page.get("default_board_id", default_board)
        statuses.extend(page.get("statuses", []))
        edges.extend(page.get("edges", []))
        members.extend(page.get("members", []))
        status_revision = page.get("status_revision", status_revision)
        if len(records) > 100000:
            raise Problem("Workspace cache limit exceeded", 413)
        cursor = page.get("next")
        if not cursor:
            if not page.get("complete"):
                raise Problem("Incomplete snapshot", 409)
            break
    # Nothing is committed until every page succeeds. Missing records never delete cached content.
    remote_by_id = {r["id"]: r for r in records}
    with store.tx():
        if adapter.connection(c["id"])["state"] != "connected":
            return
        if members:
            cached_members = []
            for member in members:
                mapped = "member:" + local_id(c["id"], str(member["id"]))
                store.set_setting("member_remote:" + c["id"] + ":" + mapped, member["id"])
                cached_members.append(dict(member, id=mapped))
            store.set_setting("members:" + c["id"], cached_members)
        boards = [r for r in records if r["kind"] == "board"]
        if boards:
            store.set_setting(
                "cached_board:" + c["id"], cached_item_id(store, c["id"], default_board or boards[0]["id"])
            )
        if status_revision:
            store.set_setting("status_catalog:" + c["id"], status_revision)
        for board_id, revision in status_revisions.items():
            store.set_setting(
                "status_catalog:" + c["id"] + ":" + cached_item_id(store, c["id"], board_id), revision
            )
        for status in statuses:
            ident = store.setting(
                "status_local:" + c["id"] + ":" + status["id"], local_id(c["id"], status["id"])
            )
            data = {k: v for k, v in status.items() if k not in ("id", "revision")}
            data["board_id"] = (
                cached_item_id(store, c["id"], status["board_id"])
                if status.get("board_id")
                else store.setting("cached_board:" + c["id"])
            )
            if data.get("replacement_id"):
                remote_replacement = data["replacement_id"]
                data["replacement_id"] = store.setting(
                    "status_local:" + c["id"] + ":" + remote_replacement,
                    local_id(c["id"], remote_replacement),
                )
            queued = store.db.execute(
                "SELECT 1 FROM outbox WHERE connection=? AND state IN ('queued','draft','conflict') AND json_extract(payload,'$.status')=?",
                (c["id"], ident),
            ).fetchone()
            if not queued:
                store.db.execute(
                    "INSERT OR REPLACE INTO statuses VALUES(?,?,?)",
                    (ident, status.get("revision", 1), json.dumps(data)),
                )
            store.set_setting("status_remote:" + ident, status["id"])
            store.set_setting("status_local:" + c["id"] + ":" + status["id"], ident)
        for record in records:
            existing_map = store.db.execute(
                "SELECT id FROM items WHERE connection=? AND remote_id=?", (c["id"], record["id"])
            ).fetchone()
            ident = existing_map["id"] if existing_map else local_id(c["id"], record["id"])
            pending = store.db.execute(
                "SELECT 1 FROM outbox WHERE item_id=? AND state IN ('queued','conflict','draft')", (ident,)
            ).fetchone()
            if pending:
                continue
            decoded = decode(store, c["id"], record)
            existing = store.db.execute("SELECT revision,data FROM items WHERE id=?", (ident,)).fetchone()
            if existing and json.loads(existing["data"]) == decoded["data"]:
                continue
            rev = existing["revision"] + 1 if existing else 1
            store.db.execute(
                "INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,revision=excluded.revision,updated_at=excluded.updated_at",
                (
                    ident,
                    record["kind"],
                    rev,
                    json.dumps(decoded["data"]),
                    decoded["author_id"],
                    record.get("created_at", now()),
                    record.get("updated_at", now()),
                    c["id"],
                    record["id"],
                ),
            )
            store.set_setting("remote_base:" + ident, record)
        for edge in edges:
            source = cached_item_id(store, c["id"], edge["source_id"])
            target = cached_item_id(store, c["id"], edge["target_id"])
            if (
                not store.db.execute("SELECT 1 FROM items WHERE id=?", (source,)).fetchone()
                or not store.db.execute("SELECT 1 FROM items WHERE id=?", (target,)).fetchone()
            ):
                continue
            edge_id = store.setting("edge_local:" + c["id"] + ":" + edge["id"], local_id(c["id"], edge["id"]))
            queued = store.db.execute(
                "SELECT 1 FROM outbox WHERE connection=? AND state IN ('queued','conflict','draft') AND json_extract(payload,'$.edge.id')=?",
                (c["id"], edge_id),
            ).fetchone()
            if not queued:
                store.db.execute(
                    "INSERT OR REPLACE INTO edges VALUES(?,?,?,?,?,?)",
                    (
                        edge_id,
                        source,
                        target,
                        edge["kind"],
                        edge.get("revision", 1),
                        int(edge.get("deleted", False)),
                    ),
                )
            store.set_setting("remote_edge:" + edge_id, edge)
        pending = [
            dict(r)
            for r in store.db.execute(
                "SELECT * FROM outbox WHERE connection=? AND state='queued' ORDER BY rowid", (c["id"],)
            )
        ]
    from .sync_files import download_attachments

    download_attachments(store, c, attachments, transport, token)
    for job in pending:
        with store.lock:
            if adapter.connection(c["id"])["state"] != "connected":
                return
        payload = json.loads(job["payload"])
        if payload.get("operation") == "project_status_write":
            try:
                result = transport(
                    c["origin"],
                    prefix + "/mutations",
                    token,
                    body={"tool": "project_status_write", "arguments": payload["arguments"]},
                )["result"]
                with store.tx():
                    store.db.execute("UPDATE outbox SET state='sent' WHERE id=?", (job["id"],))
                    store.set_setting("status_catalog:" + c["id"], result["revision"])
                    remote_board = payload["arguments"].get("board_id")
                    if remote_board:
                        store.set_setting(
                            "status_catalog:" + c["id"] + ":" + cached_item_id(store, c["id"], remote_board),
                            result["revision"],
                        )
                    args = payload["arguments"]
                    matches = [
                        x
                        for x in result["statuses"]
                        if x["id"] == args.get("id") or not args.get("id") and x["name"] == args["name"]
                    ]
                    if len(matches) == 1:
                        store.set_setting("status_remote:" + payload["status"], matches[0]["id"])
                        store.set_setting(
                            "status_local:" + c["id"] + ":" + matches[0]["id"], payload["status"]
                        )
            except Problem as e:
                if e.status in (401, 403, 409, 400):
                    with store.tx():
                        store.db.execute(
                            "UPDATE outbox SET state='draft',reason='Shared status rejected; review revision or permissions' WHERE id=?",
                            (job["id"],),
                        )
                else:
                    raise
            continue
        if payload.get("operation") in ("project_link", "project_unlink"):
            edge = payload["edge"]
            operation = payload["operation"]
            with store.lock:
                source = store.db.execute(
                    "SELECT remote_id FROM items WHERE id=?", (edge["source_id"],)
                ).fetchone()[0]
                target = store.db.execute(
                    "SELECT remote_id FROM items WHERE id=?", (edge["target_id"],)
                ).fetchone()[0]
                remote_edge = store.setting("remote_edge:" + edge["id"])
            if not source or not target:
                continue
            args = {"idempotency_key": job["id"]}
            if operation == "project_link":
                args.update(
                    source_id=source,
                    target_id=target,
                    kind=edge["kind"],
                    expected_revision=remote_edge["revision"] if remote_edge else 0,
                )
            else:
                if not remote_edge:
                    with store.tx():
                        store.db.execute("UPDATE outbox SET state='sent' WHERE id=?", (job["id"],))
                    continue
                args.update(id=remote_edge["id"], revision=remote_edge["revision"])
            try:
                result = transport(
                    c["origin"], prefix + "/mutations", token, body={"tool": operation, "arguments": args}
                )["result"]
                with store.tx():
                    store.db.execute("UPDATE outbox SET state='sent' WHERE id=?", (job["id"],))
                    store.set_setting("remote_edge:" + edge["id"], result)
                    store.set_setting("edge_local:" + c["id"] + ":" + result["id"], edge["id"])
            except Problem as e:
                if e.status in (401, 403, 409, 400):
                    with store.tx():
                        store.db.execute(
                            "UPDATE outbox SET state='draft',reason='Shared relationship rejected; review permissions or revision' WHERE id=?",
                            (job["id"],),
                        )
                else:
                    raise
            continue
        with store.lock:
            base = json.loads(job["base"])
            local = json.loads(job["payload"])
            remote_id = store.db.execute(
                "SELECT remote_id FROM items WHERE id=?", (job["item_id"],)
            ).fetchone()[0]
            shared = remote_by_id.get(remote_id)
            try:
                if base is not None:
                    base["data"] = encode_data(store, c["id"], base["data"])
                local_data = encode_data(store, c["id"], local["data"])
            except Problem:
                with store.tx():
                    store.db.execute(
                        "UPDATE outbox SET state='draft',reason='Shared edit contains a local-only reference or unavailable member' WHERE id=?",
                        (job["id"],),
                    )
                continue
        if remote_id is None and base is None:
            try:
                result = transport(
                    c["origin"],
                    prefix + "/mutations",
                    token,
                    body={
                        "tool": "project_create",
                        "arguments": {
                            "kind": local["kind"],
                            "idempotency_key": job["id"],
                            "data": local_data,
                        },
                    },
                )["result"]
                with store.tx():
                    duplicate = store.db.execute(
                        "SELECT id FROM items WHERE connection=? AND remote_id=? AND id!=?",
                        (c["id"], result["id"], job["item_id"]),
                    ).fetchone()
                    if duplicate:
                        old = duplicate["id"]
                        # A response lost after remote commit may have been imported by the next snapshot.
                        # Keep the original offline identity, including references from the imported graph.
                        pending = store.db.execute(
                            "SELECT 1 FROM outbox WHERE item_id=? AND state IN ('queued','draft','conflict')",
                            (old,),
                        ).fetchone()
                        if pending:
                            raise Problem("Imported copy has local edits; retain both drafts for review", 409)
                        for row in store.db.execute("SELECT id,data FROM items").fetchall():
                            data = json.loads(row["data"])
                            changed = False
                            for field in ("board_id", "parent_id", "resource_id"):
                                if data.get(field) == old:
                                    data[field] = job["item_id"]
                                    changed = True
                            if changed:
                                store.db.execute(
                                    "UPDATE items SET data=? WHERE id=?", (json.dumps(data), row["id"])
                                )
                        store.db.execute(
                            "UPDATE edges SET source_id=? WHERE source_id=?", (job["item_id"], old)
                        )
                        store.db.execute(
                            "UPDATE edges SET target_id=? WHERE target_id=?", (job["item_id"], old)
                        )
                        store.db.execute("DELETE FROM items WHERE id=?", (old,))
                    store.db.execute(
                        "UPDATE items SET remote_id=? WHERE id=?", (result["id"], job["item_id"])
                    )
                    store.db.execute("UPDATE outbox SET state='sent' WHERE id=?", (job["id"],))
                    store.set_setting("remote_base:" + job["item_id"], result)
                    remote_by_id[result["id"]] = result
            except Problem as e:
                if e.status in (401, 403, 400):
                    with store.tx():
                        store.db.execute(
                            "UPDATE outbox SET state='draft',reason='Create rejected; review shared fields or permissions' WHERE id=?",
                            (job["id"],),
                        )
                else:
                    raise
            continue
        if shared is None:
            # Never interpret a missing snapshot item as deletion or permission to recreate.
            with store.tx():
                store.db.execute(
                    "UPDATE outbox SET state='draft',reason='Shared record unavailable' WHERE id=?",
                    (job["id"],),
                )
            continue
        merged = merge(base.get("data", {}) if base else {}, local_data, shared["data"])
        if merged is None:
            with store.tx():
                store.db.execute(
                    "INSERT INTO conflicts VALUES(?,?,?,?,?,?,0)",
                    (uid(), c["id"], job["item_id"], json.dumps(base), json.dumps(local), json.dumps(shared)),
                )
                store.db.execute(
                    "UPDATE outbox SET state='conflict',reason='Overlapping edit' WHERE id=?", (job["id"],)
                )
            continue
        changed = {k: v for k, v in merged.items() if v != shared["data"].get(k)}
        try:
            if changed:
                result = transport(
                    c["origin"],
                    prefix + "/mutations",
                    token,
                    body={
                        "tool": "project_update",
                        "arguments": {
                            "id": remote_id,
                            "revision": shared["revision"],
                            "idempotency_key": job["id"],
                            "data": changed,
                        },
                    },
                )["result"]
            else:
                result = shared
            with store.tx():
                store.db.execute("UPDATE outbox SET state='sent' WHERE id=?", (job["id"],))
                store.set_setting("remote_base:" + job["item_id"], result)
                remote_by_id[remote_id] = result
        except Problem as e:
            if e.status in (401, 403):
                with store.tx():
                    store.db.execute(
                        "UPDATE outbox SET state='draft',reason='Authorization removed' WHERE id=?",
                        (job["id"],),
                    )
            elif e.status == 409:
                continue
            else:
                raise
    with store.tx():
        store.db.execute("UPDATE connections SET cursor=? WHERE id=?", (now(), c["id"]))


def sync_all(store):
    if not store.sync_lock.acquire(blocking=False):
        return
    try:
        from .accounts import discover

        discover(store)
        from .publishing import resume_all

        resume_all(store)
        with store.lock:
            connections = [
                dict(r)
                for r in store.db.execute(
                    "SELECT * FROM connections WHERE state='connected' AND workspace!=''"
                )
            ]
        for c in connections:
            with store.lock:
                schedule = store.setting("sync:" + c["id"], {})
                pending = store.db.execute(
                    "SELECT 1 FROM outbox WHERE connection=? AND state='queued'", (c["id"],)
                ).fetchone()
            if schedule.get("next", 0) > time.time():
                continue
            if c["cursor"] and not pending:
                from datetime import datetime

                if time.time() - datetime.fromisoformat(c["cursor"]).timestamp() < 30:
                    continue
            try:
                with store.tx():
                    store.set_setting("sync:" + c["id"], {"state": "syncing"})
                sync_connection(store, c)
                with store.tx():
                    store.set_setting("sync:" + c["id"], {"state": "up-to-date"})
            except Problem as e:
                with store.tx():
                    attempt = schedule.get("attempt", 0) + 1
                    store.set_setting(
                        "sync:" + c["id"],
                        {
                            "state": "attention" if e.status in (400, 401, 403, 409) else "offline",
                            "error": str(e),
                            "attempt": attempt,
                            "next": time.time() + min(300, 2 ** min(attempt, 8)),
                        },
                    )
                    if e.status in (401, 403):
                        store.db.execute("UPDATE connections SET state='revoked' WHERE id=?", (c["id"],))
                        store.db.execute(
                            "UPDATE outbox SET state='draft',reason='Authorization removed' WHERE connection=? AND state='queued'",
                            (c["id"],),
                        )
    finally:
        store.sync_lock.release()


def resolve_conflict(store, body):
    c = store.db.execute("SELECT * FROM conflicts WHERE id=? AND resolved=0", (body["id"],)).fetchone()
    if not c:
        raise Problem("Conflict unavailable", 404)
    if body.get("choice") not in ("local", "remote"):
        raise Problem("Choose local or remote")
    if body["choice"] == "remote":
        shared = decode(store, c["connection"], json.loads(c["remote"]))
        store.db.execute(
            "UPDATE items SET data=?,revision=revision+1 WHERE id=?",
            (json.dumps(shared["data"]), c["item_id"]),
        )
        store.db.execute(
            "UPDATE outbox SET state='discarded' WHERE item_id=? AND state='conflict'", (c["item_id"],)
        )
    else:
        # Preserve local as a draft. Explicit edit after reconnect is needed to submit it.
        store.db.execute(
            "UPDATE outbox SET state='draft',reason='Kept local after conflict' WHERE item_id=? AND state='conflict'",
            (c["item_id"],),
        )
    store.db.execute("UPDATE conflicts SET resolved=1 WHERE id=?", (c["id"],))
    return {"resolved": True}
