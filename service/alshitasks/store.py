"""SQLite graph storage, revisions, tombstones, transactional replay and outbox."""

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager

KINDS = {"task", "board", "deliverable", "note", "resource", "comment"}
DEFAULT = dict(
    title="",
    body="",
    state="todo",
    status_id=None,
    type_id=None,
    label_ids=[],
    visibility="private",
    priority="normal",
    acceptance="",
    assignee=None,
    reviewer=None,
    starts_on=None,
    due_on=None,
    parent_id=None,
    board_id=None,
    resource_id=None,
    archived=False,
    deleted=False,
    color="yellow",
    resource=None,
    checklist=[],
    details={},
)


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Store:
    def __init__(self, paths):
        self.paths = paths
        self.lock = threading.RLock()
        self._context = threading.local()
        self.workspace = None
        self.sync_lock = threading.Lock()
        self.token_lock = threading.RLock()
        self.sync_wake = threading.Event()
        self.db = sqlite3.connect(paths.data / "projects.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        try:
            self.migrate()
            self.migrate_task_numbers()
            from . import team
            team.migrate(self)
            from . import taxonomy
            taxonomy.migrate(self)
            from . import health, notifications

            health.migrate(self)
            notifications.migrate(self)
            from .accounts import adopt_legacy

            with self.tx():
                adopt_legacy(self)
                if not self.setting('pause_retired'):
                    if self.setting('paused', False):
                        self.db.execute("UPDATE health_checks SET enabled=0,revision=revision+1,lease=NULL,lease_until=0 WHERE enabled=1")
                        self.db.execute("UPDATE forwarding SET enabled=0 WHERE enabled=1")
                        self.set_setting('pause_migration', 'Previously paused monitoring checks and forwarding destinations remain disabled. Re-enable them individually in resource settings. Connected portal accounts synchronize automatically.')
                    self.set_setting('paused', False)
                    self.set_setting('pause_retired', True)
                from .graph import migrate

                migrate(self)
                taxonomy.seed(self, taxonomy.LOCAL)
                from .workspace_icons import local_selection
                self.set_setting('local_workspace_icon', local_selection(self))
                if self.setting('account_navigation'):
                    self.set_setting('account_navigation', [])
                for row in self.db.execute('SELECT id,capabilities FROM connections WHERE capabilities LIKE ?', ('%"navigation"%',)).fetchall():
                    capabilities = json.loads(row['capabilities'])
                    if isinstance(capabilities, dict) and 'navigation' in capabilities:
                        capabilities.pop('navigation')
                        self.db.execute('UPDATE connections SET capabilities=? WHERE id=?', (json.dumps(capabilities), row['id']))
        except BaseException:
            self.db.close()
            raise
        os.chmod(paths.data / "projects.sqlite", 0o600)

    @property
    def workspace(self):
        return getattr(self._context, "workspace", None)

    @workspace.setter
    def workspace(self, value):
        self._context.workspace = value

    def migrate(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            raise Problem("Database is newer than this application; refusing downgrade", 409)
        if version == 0:
            self.db.executescript("""BEGIN IMMEDIATE;
                CREATE TABLE items(id TEXT PRIMARY KEY, kind TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL, author_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, connection TEXT, remote_id TEXT);
                CREATE TABLE edges(id TEXT PRIMARY KEY, source_id TEXT REFERENCES items(id), target_id TEXT REFERENCES items(id), kind TEXT, revision INTEGER, deleted INTEGER DEFAULT 0);
                CREATE TABLE statuses(id TEXT PRIMARY KEY, revision INTEGER, data TEXT);
                CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, operation TEXT, created_at TEXT, data TEXT);
                CREATE TABLE replay(key TEXT PRIMARY KEY, digest TEXT, response TEXT);
                CREATE TABLE grants(id TEXT PRIMARY KEY, name TEXT, digest TEXT UNIQUE, scopes TEXT, resource_id TEXT, revoked_at TEXT);
                CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE attachments(id TEXT PRIMARY KEY, item_id TEXT REFERENCES items(id), name TEXT, size INTEGER, sha256 TEXT);
                CREATE TABLE connections(id TEXT PRIMARY KEY, origin TEXT, account TEXT, workspace TEXT, state TEXT, cursor TEXT, capabilities TEXT);
                CREATE TABLE outbox(id TEXT PRIMARY KEY, connection TEXT, item_id TEXT, base TEXT, payload TEXT, state TEXT DEFAULT 'queued', reason TEXT DEFAULT '');
                CREATE TABLE conflicts(id TEXT PRIMARY KEY, connection TEXT, item_id TEXT, base TEXT, local TEXT, remote TEXT, resolved INTEGER DEFAULT 0);
                CREATE TABLE monitors(resource_id TEXT PRIMARY KEY REFERENCES items(id), config TEXT, health TEXT DEFAULT 'unknown', candidate TEXT, streak INTEGER DEFAULT 0, last_at TEXT, next_at REAL DEFAULT 0, detail TEXT);
                CREATE TABLE incidents(id TEXT PRIMARY KEY, resource_id TEXT, health TEXT, created_at TEXT, detail TEXT);
                CREATE TABLE forwarding(id TEXT PRIMARY KEY, destination TEXT, resource_id TEXT, credential TEXT, enabled INTEGER DEFAULT 1);
                CREATE TABLE forward_queue(id TEXT, target TEXT, data TEXT, bytes INTEGER, attempts INTEGER DEFAULT 0, next_at REAL DEFAULT 0, PRIMARY KEY(id,target));
                CREATE TABLE aliases(resource_id TEXT PRIMARY KEY, alias TEXT UNIQUE, host TEXT, username TEXT, port INTEGER, identity_file TEXT, agent INTEGER);
                """)
            try:
                for state, name, color in [
                    ("todo", "To do", "#777777"),
                    ("doing", "In progress", "#7357db"),
                    ("waiting", "Waiting", "#bd8200"),
                    ("review", "In review", "#2479b7"),
                    ("done", "Done", "#24865d"),
                ]:
                    self.status_save(
                        {
                            "data": dict(
                                name=name,
                                color=color,
                                category=state,
                                legacy_state=state,
                                position=len(self.statuses()),
                                is_default=state == "todo",
                                retired=False,
                                replacement_id=None,
                                board_id=None,
                            )
                        }
                    )
                self.db.execute("PRAGMA user_version=1")
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    @contextmanager
    def tx(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def setting(self, key, default=None):
        # check_same_thread=False permits sharing; it does not serialize the
        # execute/fetch pair or protect the driver's prepared statement cache.
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def migrate_task_numbers(self):
        # Separate immutable metadata: numbers are never editable task fields.
        # Keep allocations after deletion so a task number is never reused.
        with self.tx():
            self.db.execute("CREATE TABLE IF NOT EXISTS local_task_numbers(number INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL UNIQUE)")
            self.db.execute("INSERT INTO local_task_numbers(item_id) SELECT id FROM items WHERE kind='task' AND connection IS NULL AND id NOT IN (SELECT item_id FROM local_task_numbers) ORDER BY created_at,id")
            self.db.execute("""CREATE TRIGGER IF NOT EXISTS allocate_task_number AFTER INSERT ON items
                WHEN NEW.kind='task' AND NEW.connection IS NULL BEGIN
                INSERT OR IGNORE INTO local_task_numbers(item_id) VALUES(NEW.id); END""")

    def row(self, row):
        if row is None:
            raise Problem("Record not found", 404)
        result = {
            k: (json.loads(row[k]) if k == "data" else row[k])
            for k in ("id", "kind", "revision", "data", "author_id", "created_at", "updated_at")
        }
        recorded = self.db.execute("SELECT a.actor FROM event_actors a JOIN events e ON e.sequence=a.sequence WHERE e.item_id=? AND e.operation='create' ORDER BY e.sequence LIMIT 1", (row['id'],)).fetchone()
        if recorded:
            result['actor'] = json.loads(recorded[0])
        elif row['connection']:
            remote = self.setting('latest_remote:' + row['id']) or self.setting('remote_base:' + row['id']) or {}
            if remote.get('actor'):
                result['actor'] = remote['actor']
        if row["kind"] == "task":
            if row["connection"]:
                remote = self.setting("latest_remote:" + row["id"]) or {}
                base = self.setting("remote_base:" + row["id"]) or {}
                result["number"] = remote.get("number", base.get("number"))
            else:
                allocated = self.db.execute("SELECT number FROM local_task_numbers WHERE item_id=?", (row["id"],)).fetchone()
                result["number"] = allocated[0] if allocated else None
        return result

    def item(self, item_id):
        from .workspaces import require_item

        require_item(self, item_id)
        return self.row(self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())

    def list(self, after="", kind=None, limit=100, board=None, type_ids=None, label_ids=None, canonical_ids=False, assignee=None, reviewer=None):
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise Problem("limit must be 1..100")
        from .workspaces import LOCAL

        workspace = self.workspace
        type_ids, label_ids = type_ids or [], label_ids or []
        if any(not isinstance(values, list) or len(values) > 100 or any(not isinstance(v, str) for v in values) for values in (type_ids, label_ids)):
            raise Problem("Invalid type or label filters")
        sort_id = "COALESCE(remote_id,id)" if canonical_ids else "id"
        rows = self.db.execute(
            f"SELECT * FROM items WHERE {sort_id}>? AND (? IS NULL OR kind=?) "
            "AND (? IS NULL OR (?=? AND connection IS NULL) OR connection=?) "
            "AND NOT EXISTS (SELECT 1 FROM settings WHERE key='unavailable:' || items.id AND value='true') "
            "AND (? IS NULL OR json_extract(data,'$.board_id')=?) "
            "AND (?=0 OR (kind='task' AND json_extract(data,'$.type_id') IN (SELECT value FROM json_each(?)))) "
            "AND (?=0 OR (kind='task' AND EXISTS (SELECT 1 FROM json_each(json_extract(data,'$.label_ids')) labels WHERE labels.value IN (SELECT value FROM json_each(?))))) "
            "AND (? IS NULL OR json_extract(data,'$.assignee')=?) "
            "AND (? IS NULL OR json_extract(data,'$.reviewer')=?) "
            f"ORDER BY {sort_id} LIMIT ?",
            (after, kind, kind, workspace, workspace, LOCAL, workspace, board, board, len(type_ids), json.dumps(type_ids), len(label_ids), json.dumps(label_ids), assignee, assignee, reviewer, reviewer, limit + 1),
        ).fetchall()
        return dict(
            items=[self.row(r) for r in rows[:limit]],
            next=(rows[limit - 1]["remote_id"] or rows[limit - 1]["id"]) if canonical_ids and len(rows) > limit else rows[limit - 1]["id"] if len(rows) > limit else None,
        )

    def validate(self, kind, data, item_id=None):
        if kind not in KINDS or not isinstance(data, dict) or set(data) - set(DEFAULT):
            raise Problem("Invalid record kind or fields")
        for name in ("title", "body", "state", "visibility", "priority", "acceptance", "color"):
            if name in data and (
                not isinstance(data[name], str)
                or len(data[name]) > (100000 if name in ("body", "acceptance") else 500)
            ):
                raise Problem(f"Invalid {name}")
        for name in ("deleted", "archived"):
            if name in data and not isinstance(data[name], bool):
                raise Problem(f"Invalid {name}")
        if "checklist" in data:
            if (
                not isinstance(data["checklist"], list)
                or len(data["checklist"]) > 200
                or any(
                    not isinstance(x, dict)
                    or not isinstance(x.get("id"), str)
                    or not isinstance(x.get("text"), str)
                    or not isinstance(x.get("done"), bool)
                    for x in data["checklist"]
                )
            ):
                raise Problem("Invalid checklist")
        if "details" in data and (
            not isinstance(data["details"], dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in data["details"].items())
        ):
            raise Problem("Invalid details")
        if item_id and self.db.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
            from .workspaces import writable

            writable(self, item_id)
        if data.get("board_id"):
            from .workspaces import writable

            writable(self, data["board_id"])
            b = self.item(data["board_id"])
            if b["kind"] != "board" or b["data"]["deleted"]:
                raise Problem("Board unavailable")
        if data.get("parent_id"):
            p = self.item(data["parent_id"])
            if p["id"] == item_id or p["data"]["deleted"]:
                raise Problem("Invalid parent")
            if kind == "comment":
                data["board_id"] = p["data"].get("board_id")
        from .taxonomy import validate as validate_taxonomy
        validate_taxonomy(self, kind, data, item_id)
        if data.get("status_id"):
            status = next((s for s in self.statuses() if s["id"] == data["status_id"]), None)
            if not status or status["retired"] or status.get("board_id") != data.get("board_id"):
                raise Problem("Status unavailable for this board")
            data["state"] = status["legacy_state"]
        for f in ("starts_on", "due_on"):
            if data.get(f):
                try:
                    datetime.strptime(data[f], "%Y-%m-%d")
                except (TypeError, ValueError):
                    raise Problem("Invalid date")
        if data.get("starts_on") and data.get("due_on") and data["starts_on"] > data["due_on"]:
            raise Problem("Due date precedes start date")
        if data.get("resource") is not None and not isinstance(data["resource"], dict):
            raise Problem("Invalid resource metadata")

    def replay(self, key, payload, operation):
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise Problem("idempotency_key required")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if self.workspace:
            key = self.workspace + ":" + key
        from .team import actor
        if actor().get("credential_id"):
            key = actor()["id"] + ":" + key
        row = self.db.execute("SELECT * FROM replay WHERE key=?", (key,)).fetchone()
        if row:
            if row["digest"] != digest:
                raise Problem("Idempotency key reused for a different request", 409)
            return json.loads(row["response"])
        value = operation()
        self.db.execute("INSERT INTO replay VALUES(?,?,?)", (key, digest, json.dumps(value)))
        return value

    def retain_disconnected_drafts(self, connection):
        if connection:
            row = self.db.execute("SELECT state FROM connections WHERE id=?", (connection,)).fetchone()
            if not row or row["state"] not in ("connected", "syncing"):
                self.db.execute(
                    "UPDATE outbox SET state='draft',reason='Connection is disconnected; retained locally' WHERE connection=? AND state='queued'",
                    (connection,),
                )

    def event(self, item_id, operation, data):
        self.sync_wake.set()
        row = self.db.execute("SELECT connection FROM items WHERE id=?", (item_id,)).fetchone()
        self.retain_disconnected_drafts(row["connection"] if row else None)
        self.db.execute(
            "INSERT INTO events(item_id,operation,created_at,data) VALUES(?,?,?,?)",
            (item_id, operation, now(), json.dumps(data)),
        )

        from .team import actor
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='event_actors'").fetchone():
            self.db.execute("INSERT INTO event_actors VALUES(last_insert_rowid(),?)", (json.dumps(actor()),))

    def create(self, body):
        def apply():
            item_id = body.get("id") or uid()
            try:
                uuid.UUID(item_id)
            except (ValueError, TypeError):
                raise Problem("Invalid record identity")
            data = dict(DEFAULT, **body.get("data", {}))
            kind = body.get("kind")
            from .workspaces import LOCAL

            if (
                not data.get("board_id")
                and self.workspace in (None, LOCAL)
                and self.setting("publishing:" + LOCAL)
            ):
                raise Problem("Local workspace publication is in progress", 409)
            from .team import actor, validate_assignment
            validate_assignment(self, data)
            if actor().get('workspace_id') and data.get('reviewer') and data.get('state') == 'done':
                raise Problem('Create reviewed work before requesting approval', 403)
            self.validate(kind, data, item_id)
            stamp = now()
            try:
                self.db.execute(
                    "INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)",
                    (item_id, kind, 1, json.dumps(data), actor()["id"], stamp, stamp, None, None),
                )
            except sqlite3.IntegrityError:
                raise Problem("Identity already exists", 409)
            if kind == "board":
                for s in [x for x in self.statuses() if x.get("board_id") is None]:
                    self.status_save(
                        {
                            "data": {k: v for k, v in s.items() if k not in ("id", "revision")}
                            | {"board_id": item_id}
                        }
                    )
            parent = data.get("board_id") or data.get("parent_id")
            connection = (
                self.db.execute("SELECT connection FROM items WHERE id=?", (parent,)).fetchone()
                if parent
                else None
            )
            from .workspaces import LOCAL

            if not connection and self.workspace and self.workspace != LOCAL:
                if kind != "board":
                    raise Problem("Choose a board in this workspace")
                connection = {"connection": self.workspace}
            if connection and connection["connection"]:
                self.db.execute(
                    "UPDATE items SET connection=? WHERE id=?", (connection["connection"], item_id)
                )
                self.db.execute(
                    "INSERT INTO outbox(id,connection,item_id,base,payload) VALUES(?,?,?,?,?)",
                    (
                        body["idempotency_key"],
                        connection["connection"],
                        item_id,
                        json.dumps(None),
                        json.dumps(self.item(item_id)),
                    ),
                )
            from .graph import synchronize_mentions

            synchronize_mentions(self, self.item(item_id))
            self.event(item_id, "create", data)
            return self.item(item_id)

        return self.replay(body.get("idempotency_key"), body, apply)

    def update(self, item_id, body):
        def apply():
            old = self.item(item_id)
            if body.get("revision") != old["revision"]:
                raise Problem("Record changed; reload or resolve the conflict", 409)
            patch = body.get("data", {})
            data = dict(old["data"], **patch)
            if patch.get("state") and "status_id" not in patch and patch["state"] != old["data"].get("state"):
                status = next(
                    (
                        x
                        for x in self.statuses()
                        if x.get("board_id") == data.get("board_id")
                        and not x["retired"]
                        and x["legacy_state"] == patch["state"]
                    ),
                    None,
                )
                data["status_id"] = status["id"] if status else None
            from .team import validate_assignment, validate_review
            validate_assignment(self, data, old["data"])
            validate_review(old["data"], data)
            self.validate(old["kind"], data, item_id)
            if old["data"].get("deleted") and not (body.get("restoring") or patch == {"deleted": False}):
                raise Problem("Restore the record before editing", 409)
            self.db.execute(
                "UPDATE items SET data=?,revision=revision+1,updated_at=? WHERE id=?",
                (json.dumps(data), now(), item_id),
            )
            row = self.db.execute("SELECT connection FROM items WHERE id=?", (item_id,)).fetchone()
            if row["connection"]:
                self.db.execute(
                    "INSERT INTO outbox(id,connection,item_id,base,payload) VALUES(?,?,?,?,?)",
                    (
                        body["idempotency_key"],
                        row["connection"],
                        item_id,
                        json.dumps(old),
                        json.dumps(self.item(item_id)),
                    ),
                )
            from .graph import synchronize_mentions

            synchronize_mentions(self, self.item(item_id))
            self.event(item_id, "update", data)
            return self.item(item_id)

        return self.replay(body.get("idempotency_key"), dict(body, item_id=item_id), apply)

    def action(self, item_id, body):
        action = body.get("action")
        flags = {
            "trash": dict(deleted=True),
            "restore": dict(deleted=False),
            "archive": dict(archived=True),
            "unarchive": dict(archived=False),
            "delete": dict(deleted=True),
            "approve": dict(state="done"),
            "changes": dict(state="doing"),
            "reopen": dict(state="todo"),
        }
        if action not in flags:
            raise Problem("Unknown action")
        return self.update(item_id, dict(body, data=flags[action], restoring=action == "restore"))

    def edges(self):
        from .workspaces import visible

        return [
            dict(x)
            for x in self.db.execute(
                "SELECT e.id,source_id,target_id,e.kind,e.revision FROM edges e JOIN items i ON i.id=e.source_id WHERE e.deleted=0"
            )
            if not self.setting("unavailable:" + x["source_id"], False)
            and not self.setting("unavailable:" + x["target_id"], False)
            and visible(
                self,
                self.db.execute("SELECT connection FROM items WHERE id=?", (x["source_id"],)).fetchone()[0],
            )
        ]

    def edge_create(self, body, *, explicit=True):
        def apply():
            from .workspaces import writable

            writable(self, body.get("source_id"))
            writable(self, body.get("target_id"))
            source = self.item(body.get("source_id"))
            target = self.item(body.get("target_id"))
            kind = body.get("kind")
            if kind not in ("related", "depends_on") or source["id"] == target["id"]:
                raise Problem("Invalid relationship")
            if source["kind"] not in {"task", "note", "resource"} or target["kind"] not in {
                "task",
                "note",
                "resource",
            }:
                raise Problem("Choose a task, note or resource")
            connections = [
                self.db.execute("SELECT connection FROM items WHERE id=?", (x["id"],)).fetchone()[0]
                for x in (source, target)
            ]
            if connections[0] != connections[1]:
                raise Problem("Relationships must stay in one workspace")
            if kind == "depends_on" and (source["kind"] != "task" or target["kind"] != "task"):
                raise Problem("Dependencies require two tasks")
            if kind == "related" and source["id"] > target["id"]:
                source, target = target, source
            if source["data"]["deleted"] or target["data"]["deleted"]:
                raise Problem("Deleted record")
            if kind == "depends_on":
                adjacency = {}
                for e in self.edges():
                    if e["kind"] == "depends_on":
                        adjacency.setdefault(e["source_id"], []).append(e["target_id"])
                todo = [target["id"]]
                seen = set()
                while todo:
                    x = todo.pop()
                    if x == source["id"]:
                        raise Problem("Dependencies cannot form a cycle", 409)
                    if x not in seen:
                        seen.add(x)
                        todo.extend(adjacency.get(x, []))
            existing = self.db.execute(
                "SELECT * FROM edges WHERE ((source_id=? AND target_id=?) OR (?='related' AND source_id=? AND target_id=?)) AND kind=? AND deleted=0",
                (source["id"], target["id"], kind, target["id"], source["id"], kind),
            ).fetchone()
            if "expected_revision" in body and body["expected_revision"] != (
                existing["revision"] if existing else 0
            ):
                raise Problem("Graph changed", 409)
            if existing:
                if explicit:
                    self.set_setting("explicit_edge:" + existing["id"], True)
                return dict(existing)
            edge = dict(id=uid(), source_id=source["id"], target_id=target["id"], kind=kind, revision=1)
            self.db.execute(
                "INSERT INTO edges(id,source_id,target_id,kind,revision) VALUES(:id,:source_id,:target_id,:kind,:revision)",
                edge,
            )
            self.set_setting("explicit_edge:" + edge["id"], explicit)
            connection = self.db.execute(
                "SELECT connection FROM items WHERE id=?", (source["id"],)
            ).fetchone()[0]
            if connection and explicit:
                self.db.execute(
                    "INSERT INTO outbox(id,connection,item_id,base,payload) VALUES(?,?,?,?,?)",
                    (
                        body["idempotency_key"],
                        connection,
                        source["id"],
                        "null",
                        json.dumps({"operation": "project_link", "edge": edge}),
                    ),
                )
            self.event(source["id"], "link", edge)
            return edge

        return self.replay(body.get("idempotency_key"), body, apply)

    def edge_delete(self, edge_id, revision):
        row = self.db.execute("SELECT * FROM edges WHERE id=? AND deleted=0", (edge_id,)).fetchone()
        if not row:
            raise Problem("Relationship unavailable", 404)
        from .workspaces import writable

        writable(self, row["source_id"])
        writable(self, row["target_id"])
        self.item(row["source_id"])
        self.item(row["target_id"])
        if row["revision"] != revision:
            raise Problem("Relationship changed", 409)
        from .graph import mentioned

        for note_id, resource_id in (
            (row["source_id"], row["target_id"]),
            (row["target_id"], row["source_id"]),
        ):
            note = self.item(note_id)
            if note["kind"] == "note" and resource_id in mentioned(note["data"]["body"]):
                raise Problem("Remove the resource mention before removing its relationship")
        self.db.execute("UPDATE edges SET deleted=1,revision=revision+1 WHERE id=?", (edge_id,))
        connection = self.db.execute(
            "SELECT connection FROM items WHERE id=?", (row["source_id"],)
        ).fetchone()[0]
        if connection:
            self.db.execute(
                "INSERT INTO outbox(id,connection,item_id,base,payload) VALUES(?,?,?,?,?)",
                (
                    uid(),
                    connection,
                    row["source_id"],
                    "null",
                    json.dumps({"operation": "project_unlink", "edge": dict(row)}),
                ),
            )
        self.event(row["source_id"], "unlink", dict(row))
        return None

    def statuses(self):
        from .workspaces import visible

        def allowed(data):
            board = json.loads(data).get("board_id")
            row = (
                self.db.execute("SELECT connection FROM items WHERE id=?", (board,)).fetchone()
                if board
                else None
            )
            return visible(self, row[0] if row else None)

        return [
            dict(json.loads(r["data"]), id=r["id"], revision=r["revision"])
            for r in self.db.execute("SELECT * FROM statuses")
            if allowed(r["data"])
        ]

    def status_save(self, body):
        from .workspaces import LOCAL

        data = body.get("data", {})
        if self.workspace not in (None, LOCAL) and not data.get("board_id"):
            raise Problem("Choose a board in this workspace")
        ident = body.get("id") or uid()
        if not isinstance(data, dict) or not data.get("name") or len(data["name"]) > 120:
            raise Problem("Status name required")
        old = self.db.execute("SELECT * FROM statuses WHERE id=?", (ident,)).fetchone()
        if old and not any(x["id"] == ident for x in self.statuses()):
            raise Problem("Status unavailable in this workspace", 404)
        for board in {
            data.get("board_id"),
            json.loads(old["data"]).get("board_id") if old else data.get("board_id"),
        }:
            if self.setting("publishing:" + (board or LOCAL)):
                raise Problem("Board publication is in progress", 409)
        if old and old["revision"] != body.get("revision"):
            raise Problem("Status changed", 409)
        data = dict(
            dict(
                name="",
                color="#777777",
                category="todo",
                legacy_state="todo",
                position=0,
                is_default=False,
                retired=False,
                replacement_id=None,
                board_id=None,
            ),
            **data,
        )
        data["legacy_state"] = data["legacy_state"] or data["category"]
        if data["category"] in ("waiting", "review"):
            data["category"] = "doing"
        if data["category"] not in ("todo", "doing", "done"):
            raise Problem("Invalid status reporting category")
        if data.get("board_id") and self.item(data["board_id"])["kind"] != "board":
            raise Problem("Invalid status board")
        if data["retired"]:
            replacement = next(
                (
                    s
                    for s in self.statuses()
                    if s["id"] == data["replacement_id"]
                    and not s["retired"]
                    and s.get("board_id") == data.get("board_id")
                ),
                None,
            )
            if not replacement:
                raise Problem("Choose a replacement status before retiring")
            for row in self.db.execute("SELECT * FROM items").fetchall():
                item = self.row(row)
                if item["data"].get("status_id") == ident:
                    item["data"].update(status_id=replacement["id"], state=replacement["legacy_state"])
                    self.db.execute(
                        "UPDATE items SET data=?,revision=revision+1 WHERE id=?",
                        (json.dumps(item["data"]), item["id"]),
                    )
        rev = old["revision"] + 1 if old else 1
        self.db.execute("INSERT OR REPLACE INTO statuses VALUES(?,?,?)", (ident, rev, json.dumps(data)))
        board = data.get("board_id")
        connection = (
            self.db.execute("SELECT connection FROM items WHERE id=?", (board,)).fetchone() if board else None
        )
        if connection and connection["connection"]:
            connection = connection["connection"]
            remote_id = self.setting("status_remote:" + ident)
            previous = json.loads(old["data"]) if old else {}
            operation = (
                "retire"
                if data["retired"]
                else "create"
                if not remote_id
                else "default"
                if data["is_default"] and not previous.get("is_default")
                else "reorder"
                if data["position"] != previous.get("position")
                else "update"
            )
            args = {
                "operation": operation,
                "revision": self.setting(
                    "status_catalog:" + connection + ":" + board,
                    self.setting("status_catalog:" + connection, "unknown"),
                ),
                "name": data["name"],
                "color": data["color"],
                "category": data["category"],
            }
            board_remote = self.db.execute("SELECT remote_id FROM items WHERE id=?", (board,)).fetchone()[0]
            if board_remote:
                args["board_id"] = board_remote
            if operation == "reorder":
                args["order"] = [
                    self.setting("status_remote:" + status["id"])
                    for status in sorted(
                        (x for x in self.statuses() if x.get("board_id") == board and not x["retired"]),
                        key=lambda x: x["position"],
                    )
                ]
            elif remote_id:
                args["id"] = remote_id
            if data.get("replacement_id"):
                args["replacement_id"] = self.setting("status_remote:" + data["replacement_id"])
            request_id = uid()
            args["idempotency_key"] = request_id
            self.db.execute(
                "INSERT INTO outbox(id,connection,item_id,base,payload) VALUES(?,?,?,?,?)",
                (
                    request_id,
                    connection,
                    board,
                    "null",
                    json.dumps({"operation": "project_status_write", "status": ident, "arguments": args}),
                ),
            )

            self.retain_disconnected_drafts(connection)

        return dict(data, id=ident, revision=rev)
