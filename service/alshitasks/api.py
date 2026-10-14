import json
from urllib.parse import urlsplit, parse_qs
from .store import Problem, now, uid
from .security import authorize, grant_create
from . import files, monitoring, ssh
from pathlib import Path
from .platform_support import mcp_command


def team_portal_url():
    try:
        path = Path("/etc/alshitasks/team.json")
        if path.stat().st_size > 16384:
            return ""
        value = json.loads(path.read_text()).get("portal_url", "")
        url = urlsplit(value)
        return (
            value.rstrip("/")
            if url.scheme == "https"
            and url.hostname
            and not url.username
            and not url.password
            and url.path in ("", "/")
            and not url.query
            and not url.fragment
            else ""
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return ""


class API:
    def __init__(self, store, logs):
        self.store = store
        self.logs = logs
        self.stopping = False

    def members(self):
        from .workspaces import LOCAL

        selected = self.store.workspace
        result = (
            ([{"id": "local", "name": "Me"}] + self.store.setting("local_members", []))
            if selected in (None, LOCAL)
            else []
        )
        for row in self.store.db.execute("SELECT id FROM connections"):
            if selected is not None and selected != row["id"]:
                continue
            result.extend(
                {"id": m["id"], "name": m.get("name", "Member")}
                for m in self.store.setting("members:" + row["id"], [])
            )
        return result

    def edge_timestamp(self, ident):
        row = self.store.db.execute(
            "SELECT created_at FROM events WHERE operation='link' AND json_extract(data,'$.id')=?", (ident,)
        ).fetchone()
        return row[0] if row else (self.store.setting("remote_edge:" + ident, {}).get("created_at") or now())

    def dispatch(self, path, method="GET", body=None, owner=False, token="", proof=None):
        from .cloud_logs import ROUTE, dispatch as cloud_dispatch

        if ROUTE.fullmatch(urlsplit(path).path):
            return cloud_dispatch(self, path, method, body, owner, token)
        if path == "/v1/desktop-toasts" and method == "POST" and owner:
            from .notifications import pending
            return [{k: row[k] for k in ("id", "title", "body")} for row in pending(self.store)]
        if path.startswith("/v1/notifications"):
            from .notifications import request

            return request(self.store, path, method, body or {}, owner, token)
        from .workspaces import scope

        q = parse_qs(urlsplit(path).query)
        with scope(self.store, q.get("workspace_id", [None])[-1]):
            return self._dispatch(path, method, body, owner, token, proof)

    def _dispatch(self, path, method="GET", body=None, owner=False, token="", proof=None):
        body = body or {}
        url = urlsplit(path)
        path = url.path
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        s = self.store
        if not isinstance(body, dict):
            raise Problem("JSON object required")
        if path == "/v1/mcp":
            from .mcp_parity import call

            return call(self, body.get("name"), body.get("arguments", {}), owner=owner, token=token)
        resource = body.get("resource_id") or q.get("resource_id")
        if not owner:
            scope = (
                "monitor:heartbeat"
                if path == "/v1/heartbeat"
                else ("logs:write" if method == "POST" else "logs:read")
                if path == "/v1/logs"
                else "monitor:read"
                if path in ("/v1/monitoring", "/v1/monitoring/checks", "/v1/monitoring/actions")
                else "project:read"
                if method == "GET"
                else "project:write"
            )
            grant = authorize(s, token, scope, resource)
            if grant["resource_id"] and path not in ("/v1/logs", "/v1/monitoring", "/v1/heartbeat"):
                raise Problem("Resource grant cannot access project records", 403)
        if path == "/v1/projects/taxonomy/review" and method == "POST":
            from .taxonomy import review

            return review(s, body)
        if path == "/v1/projects/taxonomy":
            from . import taxonomy

            if method == "GET":
                return taxonomy.catalog(s)
            return self.execute_tool("project_taxonomy_write", body, owner=owner, token=token)
        if path == "/v1/workspaces/icon":
            from .workspace_icons import dispatch
            return dispatch(s, method, body, owner)
        if path == "/v1/workspaces":
            from .workspaces import catalog

            return {"workspaces": catalog(s)}
        if path == "/v1/revision" and owner:
            return {"revision": s.db.total_changes}
        if path == "/v1/health":
            package = Path(__file__).resolve().parents[2] / "package.json"
            return {"ok": True, "version": json.loads(package.read_text())["version"]}
        if path.startswith("/v1/projects"):
            suffix = path[len("/v1/projects") :]
            parts = suffix.strip("/").split("/")
            if suffix == "/items":
                if method == "GET":
                    return s.list(
                        q.get("after", ""),
                        q.get("kind"),
                        int(q.get("limit", 100)),
                        type_ids=[v for v in q.get("type_ids", "").split(",") if v],
                        label_ids=[v for v in q.get("label_ids", "").split(",") if v],
                    )
                if method == "POST":
                    return s.create(body)
            if len(parts) >= 2 and parts[0] == "items":
                ident = parts[1]
                s.item(ident)
                if len(parts) == 2:
                    if method == "GET":
                        return s.item(ident)
                    if method == "PATCH":
                        return s.update(ident, body)
                if parts[-1] == "actions" and method == "POST":
                    return s.action(ident, body)
                if parts[-1] == "history":
                    return {
                        "events": [
                            dict(r)
                            for r in s.db.execute(
                                "SELECT sequence,operation,created_at FROM events WHERE item_id=? ORDER BY sequence",
                                (ident,),
                            )
                        ]
                    }
            if suffix == "/boards":
                from .workspaces import visible

                return {
                    "boards": [
                        s.row(r)
                        for r in s.db.execute("SELECT * FROM items WHERE kind='board'")
                        if visible(s, r["connection"]) and not s.setting("unavailable:" + r["id"], False)
                    ]
                }
            if suffix == "/members":
                return {
                    "members": [dict(m, display_name=m.get("name", "Member")) for m in self.members()],
                    "actor": {"id": "local"},
                    "next": None,
                }
            if suffix == "/statuses":
                if method == "GET":
                    return {"statuses": s.statuses(), "can_manage": owner}
                if method == "POST":
                    return s.status_save(body)
            if suffix == "/edges":
                if method == "GET":
                    return {"edges": s.edges()}
                if method == "POST":
                    return s.edge_create(body)
            if len(parts) == 2 and parts[0] == "edges" and method == "DELETE":
                return s.edge_delete(parts[1], body.get("revision"))
            if parts[0] == "keys":
                if not owner:
                    raise Problem("Only the desktop owner manages grants", 403)
                if len(parts) == 2 and method == "DELETE":
                    s.db.execute("UPDATE grants SET revoked_at=? WHERE id=?", (now(), parts[1]))
                    return {"revoked": True}
                if method == "GET":
                    return {
                        "keys": [
                            dict(r)
                            for r in s.db.execute("SELECT id,name,scopes,resource_id,revoked_at FROM grants")
                        ]
                    }
                if method == "POST":
                    return grant_create(s, body)
        if path == "/v1/logs/setup" and method == "GET":
            if not owner:
                raise Problem("Desktop owner authorization required", 403)
            from .workspaces import require_item

            row = require_item(s, resource)
            if s.item(resource)["kind"] != "resource":
                raise Problem("Choose a resource")
            from .cloud_logs import identity

            workspace, public_resource = identity(s, resource)
            return {
                "workspace_id": workspace,
                "resource_id": public_resource,
                "logs_path": f"/v1/workspaces/{workspace}/resources/{public_resource}/logs",
                "otlp_path": f"/v1/workspaces/{workspace}/resources/{public_resource}/otlp/v1/logs",
                "connected": bool(row["connection"]),
                "http_port": s.setting("http_port", None),
                "keys": [
                    dict(r)
                    for r in s.db.execute(
                        "SELECT id,name,revoked_at FROM grants WHERE resource_id=?", (resource,)
                    )
                ],
            }
        if path == "/v1/logs":
            if method == "GET":
                from .workspaces import require_item, visible

                if resource:
                    require_item(s, resource)
                    resources = [resource]
                else:
                    resources = [
                        r["id"]
                        for r in s.db.execute("SELECT id,connection FROM items WHERE kind='resource'")
                        if visible(s, r["connection"]) and not s.setting("unavailable:" + r["id"], False)
                    ]
                return self.logs.query(q, resources=resources)
            if method == "POST":
                if s.item(resource)["kind"] != "resource":
                    raise Problem("Log ingestion requires a resource")
                result = self.logs.ingest(resource, body.get("events"))
                retention = s.setting("retention", {"days": 7, "max_bytes": 1024**3})
                self.logs.retain(**retention)
                monitoring.queue_forward(s, resource, body["events"])
                return result
        if path == "/v1/monitoring" and method == "GET":
            from .health import snapshot

            result = snapshot(s, resource)
            if not owner:
                for check in result["checks"]:
                    check["config"].pop("credential", None)
                    check.pop("lease", None)
            return result
        if path == "/v1/heartbeat" and method == "POST":
            from .health import resource as require_resource

            require_resource(s, resource)
            ident = body.get("check_id") or resource
            row = s.db.execute(
                "SELECT * FROM health_checks WHERE id=? AND resource_id=?", (ident, resource)
            ).fetchone()
            if not row or json.loads(row["config"])["kind"] != "heartbeat":
                raise Problem("Heartbeat check unavailable", 404)
            import time

            s.db.execute("UPDATE health_checks SET heartbeat_at=?,next_at=0 WHERE id=?", (time.time(), ident))
            return {"received": True}
        # All local execution and credential/configuration endpoints are owner-only.
        if not owner:
            raise Problem("Desktop owner authorization required", 403)
        if resource and method != 'GET' and path in ('/v1/ssh', '/v1/ssh/key', '/v1/ssh/open', '/v1/forwarding'):
            row = s.db.execute('SELECT connection FROM items WHERE id=?', (resource,)).fetchone()
            if row and row['connection']:
                if proof is None:
                    raise Problem('Current online resource authorization required', 403)
                proof[0]()

        if path in ("/v1/monitoring", "/v1/monitoring/checks") and method == "POST":
            from . import health

            if path == "/v1/monitoring":
                old = s.db.execute("SELECT revision FROM health_checks WHERE id=?", (resource,)).fetchone()
                body = dict(body, id=resource, name="Default check", revision=old[0] if old else None)
            return health.save(s, body)
        if path == "/v1/monitoring/actions" and method == "POST":
            from .health import action

            return action(s, body)
        if path == "/v1/settings":
            if method == "GET":
                return {
                    "portal_url": team_portal_url(),
                    "local_members": s.setting("local_members", []),
                    "mcp_config": {"mcpServers": {"alshitasks": mcp_command()}},
                    "paused": False,
                    "pause_migration": s.setting("pause_migration"),
                    "notification_toasts": s.setting("notification_toasts", True),
                    "http_port": s.setting("http_port", None),
                    "retention": s.setting("retention", {"days": 7, "max_bytes": 1024**3}),
                    "usage": s.setting("log_usage", {"bytes": 0, "events": 0}),
                    "socket": str(s.paths.socket),
                    "secret_service": "required for connections",
                    "stopping": self.stopping,
                }
            if method == "PATCH":
                if "paused" in body:
                    raise Problem("Global pause has been retired. Disable individual monitoring checks or forwarding destinations, disconnect your portal account, or stop the service.", 410)
                if set(body) - {"paused", "retention", "http_port", "local_members", "notification_toasts"}:
                    raise Problem("Unknown setting")
                if "local_members" in body:
                    members = body["local_members"]
                    if (
                        not isinstance(members, list)
                        or len(members) > 100
                        or any(
                            not isinstance(m, dict)
                            or set(m) != {"id", "name"}
                            or not isinstance(m["id"], str)
                            or not m["id"].startswith("contact:")
                            or not isinstance(m["name"], str)
                            or not 1 <= len(m["name"]) <= 120
                            for m in members
                        )
                        or len({m["id"] for m in members}) != len(members)
                    ):
                        raise Problem("Supply unique local contacts with names")
                if "notification_toasts" in body and type(body["notification_toasts"]) is not bool:
                    raise Problem("Invalid toast preference")
                if "retention" in body:
                    self.logs.validate_retention(**body["retention"])
                if (
                    "http_port" in body
                    and body["http_port"] is not None
                    and (not isinstance(body["http_port"], int) or not 1024 <= body["http_port"] <= 65535)
                ):
                    raise Problem("Use a port in 1024..65535")
                for k, v in body.items():
                    s.set_setting(k, v)
                return {"saved": True, "restart_required": "http_port" in body}
        if path == "/v1/stop" and method == "POST":
            self.stopping = True
            return {"stopping": True}
        if path == "/v1/attachments":
            if method == "GET":
                s.item(q.get("item_id"))
                return {
                    "attachments": [
                        dict(
                            r,
                            sync=s.setting(
                                "file_job:" + r["id"],
                                {"state": "synced"} if s.setting("file_remote:" + r["id"]) else {},
                            ),
                        )
                        for r in s.db.execute(
                            "SELECT * FROM attachments WHERE item_id=?", (q.get("item_id"),)
                        )
                        if not s.setting("file_deleted:" + r["id"], False)
                        or s.setting("file_job:" + r["id"], {}).get("state") == "conflict"
                    ]
                }
            if method == "POST":
                return files.attach(s, body["item_id"], body["name"], body["content"], body.get("replaces"))
        if path.startswith("/v1/attachments/") and path.endswith("/review") and method == "POST":
            return files.resolve_attachment(s, path.split("/")[-2])
        if path.startswith("/v1/attachments/") and method == "DELETE":
            ident = path.rsplit("/", 1)[1]
            row = s.db.execute("SELECT * FROM attachments WHERE id=?", (ident,)).fetchone()
            if not row:
                raise Problem("Attachment unavailable", 404)
            from .workspaces import writable

            writable(s, row["item_id"])
            job = s.setting("file_job:" + ident, {})
            if job and job.get("state") != "sent":
                raise Problem("Wait for the attachment transfer before removing it", 409)
            connection = s.db.execute(
                "SELECT connection FROM items WHERE id=?", (row["item_id"],)
            ).fetchone()[0]
            if connection:
                s.set_setting(
                    "file_job:" + ident,
                    dict(
                        id=ident,
                        upload_id=uid(),
                        item_id=row["item_id"],
                        connection=connection,
                        operation="delete",
                        state="queued",
                    ),
                )
            s.set_setting("file_deleted:" + ident, True)
            return {"deleted": True}
        if path.startswith("/v1/attachments/") and method == "GET":
            import base64

            ident = path.rsplit("/", 1)[1]
            row = s.db.execute("SELECT * FROM attachments WHERE id=?", (ident,)).fetchone()
            if not row or s.setting("file_deleted:" + ident, False):
                raise Problem("Attachment unavailable", 404)
            s.item(row["item_id"])
            return {
                "name": row["name"],
                "content": base64.b64encode((s.paths.attachments / ident).read_bytes()).decode(),
            }
        if path == "/v1/backup" and method == "POST":
            # SQLite backup must be called outside an open write transaction (router handles it).
            return files.backup(s, self.logs, body["path"])
        if path == "/v1/ssh/key" and method == "POST":
            return ssh.import_key(s, resource, body["path"], body, inspected=proof[1] if proof else None)
        if path == "/v1/ssh":
            if method == "POST":
                return ssh.configure(s, resource, body, inspected=proof[1] if proof else None)
            if method == "DELETE":
                return ssh.revoke(s, resource)
            if method == "GET":
                return {"aliases": [dict(r) for r in s.db.execute("SELECT * FROM aliases")]}
        if path == "/v1/ssh/open" and method == "POST":
            return ssh.open_terminal(s, resource)
        if path == "/v1/forwarding":
            if not owner:
                raise Problem("Desktop owner authorization required", 403)
            from .workspaces import require_item, visible

            if resource:
                require_item(s, resource)
            if method == "GET":
                targets = []
                for r in s.db.execute("SELECT id,destination,resource_id,enabled FROM forwarding"):
                    if resource and r["resource_id"] != resource:
                        continue
                    try:
                        require_item(s, r["resource_id"])
                    except Problem:
                        continue
                    targets.append(
                        dict(
                            r,
                            status=s.setting(
                                "forward_status:" + r["id"],
                                {"state": "pending", "message": "Waiting for logs."},
                            ),
                            queued=s.db.execute(
                                "SELECT COUNT(*) FROM forward_queue WHERE target=?", (r["id"],)
                            ).fetchone()[0],
                        )
                    )
                return {"targets": targets, "queued": sum(t["queued"] for t in targets)}
            if method == "POST":
                u = urlsplit(body.get("destination", ""))
                if u.scheme != "https" or not u.hostname or u.username or u.password or u.fragment or u.query:
                    raise Problem("HTTPS destination required")
                if s.item(resource)["kind"] != "resource":
                    raise Problem("Resource required")
                if proof is None:
                    raise Problem("Credential verification required", 409)
                ident = uid()
                s.db.execute(
                    "INSERT INTO forwarding VALUES(?,?,?,?,1)",
                    (ident, body["destination"], resource, body["credential"]),
                )
                return {"id": ident}
            if method in ("DELETE", "PATCH"):
                target = s.db.execute(
                    "SELECT resource_id FROM forwarding WHERE id=?", (body["id"],)
                ).fetchone()
                if not target:
                    raise Problem("Target unavailable", 404)
                require_item(s, target["resource_id"])
                if method == "DELETE":
                    s.db.execute("UPDATE forwarding SET enabled=0 WHERE id=?", (body["id"],))
                    return {"disabled": True}
                if body.get("credential"):
                    if proof is None:
                        raise Problem("Credential verification required", 409)
                    s.db.execute(
                        "UPDATE forwarding SET credential=? WHERE id=?", (body["credential"], body["id"])
                    )
                s.set_setting(
                    "forward_status:" + body["id"], {"state": "pending", "message": "Retry requested."}
                )
                s.db.execute("UPDATE forwarding SET enabled=1 WHERE id=?", (body["id"],))
                s.db.execute("UPDATE forward_queue SET next_at=0 WHERE target=?", (body["id"],))
                return {"retrying": True}
        if path.startswith("/v1/portal"):
            from .portal import Portal

            return Portal(s).dispatch(path, method, body, q)
        if path == "/v1/drafts":
            from .workspaces import catalog

            allowed = {w.get("connection_id", w["id"]) for w in catalog(s)}
            return {
                "drafts": [
                    dict(r)
                    for r in s.db.execute(
                        "SELECT o.id,o.reason,o.connection,json_extract(i.data,'$.title') AS title FROM outbox o JOIN items i ON i.id=o.item_id WHERE o.state='draft'"
                    )
                    if r["connection"] in allowed
                ]
            }
        if path == "/v1/conflicts":
            from .workspaces import catalog
            from .portal import decode

            def review(row):
                result = dict(row)
                base = json.loads(row["base"])
                result["base_data"] = decode(s, row["connection"], base)["data"] if base else {}
                result["remote_data"] = decode(s, row["connection"], json.loads(row["remote"]))["data"]
                current = s.db.execute(
                    "SELECT revision,data FROM items WHERE id=?", (row["item_id"],)
                ).fetchone()
                result["local_data"] = json.loads(current["data"])
                result["local_revision"] = current["revision"]
                return result

            allowed = {w.get("connection_id", w["id"]) for w in catalog(s)}
            return {
                "conflicts": [
                    review(r)
                    for r in s.db.execute("SELECT * FROM conflicts WHERE resolved=0")
                    if r["connection"] in allowed
                ]
            }
        if path == "/v1/conflicts/resolve" and method == "POST":
            from .portal import resolve_conflict

            return resolve_conflict(s, body)
        raise Problem("Endpoint unavailable", 404)

    def tool(self, name, args, owner=False, token=""):
        return self.conform_tool_result(name, self.execute_tool(name, args, owner, token))

    def conform_tool_result(self, name, result):
        from .contract import tools
        import jsonschema

        schema = tools()[name]["outputSchema"]

        def conform(value, schema):
            if isinstance(value, dict) and schema.get("type") == "object":
                props = schema.get("properties", {})
                return {
                    k: conform(v, props.get(k, {}))
                    for k, v in value.items()
                    if schema.get("additionalProperties", True) or k in props
                }
            if isinstance(value, list):
                return [conform(x, schema.get("items", {})) for x in value]
            return value

        result = conform(result, schema)
        jsonschema.validate(result, schema)
        return result

    def status_catalog(self, board=None):
        import hashlib

        rows = [
            {
                k: v
                for k, v in x.items()
                if k
                in (
                    "id",
                    "name",
                    "category",
                    "color",
                    "position",
                    "is_default",
                    "retired",
                    "legacy_state",
                    "revision",
                )
            }
            for x in self.store.statuses()
            if x.get("board_id") == board
        ]
        return {
            "statuses": rows,
            "revision": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
        }

    def execute_tool(self, name, args, owner=False, token=""):
        from .workspaces import scope
        from .contract import validate

        validate(name, args)
        with scope(self.store, args.get("workspace_id")):
            return self._execute_tool(name, args, owner, token)

    def _execute_tool(self, name, args, owner=False, token=""):
        from .contract import tools, validate

        validate(name, args)
        tool = tools()[name]
        if not owner:
            authorize(self.store, token, "project:read" if tool["readOnly"] else "project:write")
        args = dict(args)
        args.pop("workspace_id", None)
        if name == "workspace_list":
            from .workspaces import catalog

            return {"workspaces": catalog(self.store)}
        s = self.store
        if name == "project_list":
            return s.list(**args)
        if name == "project_get":
            return s.item(args["id"])
        if name == "project_create":
            return s.create(args)
        if name == "project_update":
            return s.update(args.pop("id"), args)
        if name == "project_action":
            return s.action(args.pop("id"), args)
        if name == "project_boards":
            return {
                "items": self.dispatch("/v1/projects/boards", owner=owner, token=token)["boards"],
                "multiple_boards": True,
            }
        if name == "project_members":
            members = sorted(
                (m for m in self.members() if m["id"] > args.get("after", "")), key=lambda m: m["id"]
            )
            limit = args.get("limit", 100)
            return {
                "items": members[:limit],
                "next": members[limit - 1]["id"] if len(members) > limit else None,
            }
        if args.get("board_id"):
            s.item(args["board_id"])
        if name == "project_taxonomy":
            from .taxonomy import catalog

            return catalog(s)
        if name == "project_taxonomy_write":
            from .taxonomy import configure

            return configure(s, args)
        if name == "project_statuses":
            return self.status_catalog(args.get("board_id"))
        if name == "project_status_write":

            def apply():
                board = args.get("board_id")
                catalog = self.status_catalog(board)
                if args["revision"] != catalog["revision"]:
                    raise Problem("Status catalog changed", 409)
                operation = args["operation"]
                old = next(
                    (x for x in s.statuses() if x["id"] == args.get("id") and x.get("board_id") == board),
                    None,
                )
                if operation != "create" and operation != "reorder" and not old:
                    raise Problem("Status unavailable", 404)
                if operation == "reorder":
                    rows = [x for x in s.statuses() if x.get("board_id") == board]
                    if set(args.get("order", [])) != {x["id"] for x in rows}:
                        raise Problem("Include every board status exactly once")
                    for index, ident in enumerate(args["order"]):
                        x = next(x for x in rows if x["id"] == ident)
                        s.status_save(
                            {
                                "id": ident,
                                "revision": x["revision"],
                                "data": {k: v for k, v in x.items() if k not in ("id", "revision")}
                                | {"position": index},
                            }
                        )
                else:
                    data = {k: v for k, v in (old or {}).items() if k not in ("id", "revision")}
                    data.update(
                        {
                            k: v
                            for k, v in args.items()
                            if k in ("name", "color", "category", "board_id", "replacement_id")
                        }
                    )
                    if operation == "retire":
                        data["retired"] = True
                    if operation == "default":
                        for x in s.statuses():
                            if x.get("board_id") == board and x["is_default"]:
                                s.status_save(
                                    {
                                        "id": x["id"],
                                        "revision": x["revision"],
                                        "data": {k: v for k, v in x.items() if k not in ("id", "revision")}
                                        | {"is_default": False},
                                    }
                                )
                        data["is_default"] = True
                        old = next(x for x in s.statuses() if x["id"] == args["id"])
                    s.status_save(
                        {
                            "id": args.get("id"),
                            "revision": old["revision"] if old else None,
                            "data": dict(data, board_id=board),
                        }
                    )
                return self.status_catalog(board)

            return s.replay(args["idempotency_key"], args, apply)
        if name == "project_edges":
            return {
                "edges": [
                    dict(
                        x,
                        deleted=False,
                        created_at=self.edge_timestamp(x["id"]),
                    )
                    for x in s.edges()
                ]
            }
        if name == "project_link":
            edge = s.edge_create(args)
            return dict(
                edge,
                deleted=False,
                created_at=self.edge_timestamp(edge["id"]),
            )
        if name == "project_unlink":

            def unlink():
                edge = next((x for x in s.edges() if x["id"] == args["id"]), None)
                if not edge:
                    raise Problem("Relationship unavailable", 404)
                s.edge_delete(args["id"], args["revision"])
                return dict(edge, deleted=True, revision=edge["revision"] + 1, created_at=now())

            return s.replay(args["idempotency_key"], args, unlink)
        if name == "project_comments":
            s.item(args["id"])
            limit = args.get("limit", 100)
            rows = [
                s.row(r)
                for r in s.db.execute(
                    "SELECT * FROM items WHERE kind='comment' AND json_extract(data,'$.parent_id')=? AND id>? ORDER BY id LIMIT ?",
                    (args["id"], args.get("after", ""), limit + 1),
                )
            ]
            return {"items": rows[:limit], "next": rows[limit - 1]["id"] if len(rows) > limit else None}
        if name == "project_history":
            return self.dispatch("/v1/projects/items/" + args["id"] + "/history", owner=owner, token=token)
        raise Problem("Unknown tool")
