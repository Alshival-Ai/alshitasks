"""Offline workspace task types and labels, with revisioned portal mutations."""

import hashlib
import json
import uuid

from .store import Problem, uid
from .workspaces import LOCAL

ICONS = ("task", "bug", "story", "improvement", "research", "design", "support", "maintenance")
DEFAULTS = (("Task", "task"), ("Bug", "bug"), ("Story", "story"), ("Improvement", "improvement"))


def migrate(store):
    store.db.execute(
        "CREATE TABLE IF NOT EXISTS task_taxonomy (workspace TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(workspace,kind,id))"
    )


def workspace(store, data=None, item_id=None):
    if item_id:
        row = store.db.execute("SELECT connection FROM items WHERE id=?", (item_id,)).fetchone()
        if row:
            return row[0] or LOCAL
    if data and data.get("board_id"):
        row = store.db.execute("SELECT connection FROM items WHERE id=?", (data["board_id"],)).fetchone()
        if row:
            return row[0] or LOCAL
    return store.workspace or LOCAL


def seed(store, selected):
    if (
        selected != LOCAL
        or store.db.execute("SELECT 1 FROM task_taxonomy WHERE workspace=?", (selected,)).fetchone()
    ):
        return
    for position, (name, icon) in enumerate(DEFAULTS):
        row = dict(
            id=str(uuid.uuid5(uuid.UUID(LOCAL), "task-type:" + icon)),
            name=name,
            icon=icon,
            position=position,
            is_default=position == 0,
            archived=False,
            revision=1,
        )
        put(store, selected, "type", row)
    default = str(uuid.uuid5(uuid.UUID(LOCAL), "task-type:task"))
    store.db.execute(
        "UPDATE items SET data=json_set(data,'$.type_id',?,'$.label_ids',json('[]')) WHERE kind='task' AND connection IS NULL AND json_extract(data,'$.type_id') IS NULL",
        (default,),
    )


def put(store, selected, kind, row):
    store.db.execute(
        "INSERT OR REPLACE INTO task_taxonomy VALUES(?,?,?,?)", (selected, kind, row["id"], json.dumps(row))
    )


def catalog(store, selected=None):
    selected = selected or workspace(store)
    seed(store, selected)
    rows = list(store.db.execute("SELECT kind,data FROM task_taxonomy WHERE workspace=?", (selected,)))
    data = {
        key: [json.loads(r["data"]) for r in rows if r["kind"] == kind]
        for key, kind in [("types", "type"), ("labels", "label")]
    }
    data["types"].sort(key=lambda row: (row["position"], row["id"]))
    data["labels"].sort(key=lambda row: (row["name"].casefold(), row["id"]))
    data["revision"] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    data["can_manage"] = selected == LOCAL or store.setting("workspace:" + selected, {}).get(
        "can_manage_taxonomy", False
    )
    data["review"] = [
        dict(r)
        for r in store.db.execute(
            "SELECT id,reason FROM outbox WHERE connection=? AND state IN ('draft','conflict') AND json_extract(payload,'$.operation')='project_taxonomy_write'",
            (selected,),
        )
    ]
    if data["review"]:
        data["remote"] = store.setting("taxonomy_latest:" + selected)
    return data


def validate(store, kind, data, item_id=None):
    if kind != "task":
        if data.get("type_id") or data.get("label_ids"):
            raise Problem("Types and labels apply to tasks only")
        return
    selected = workspace(store, data, item_id)
    values = catalog(store, selected)
    old_row = (
        store.db.execute("SELECT data FROM items WHERE id=?", (item_id,)).fetchone() if item_id else None
    )
    old = json.loads(old_row[0]) if old_row else {}
    if not data.get("type_id"):
        default = next((r for r in values["types"] if r["is_default"] and not r["archived"]), None)
        if default:
            data["type_id"] = default["id"]
    chosen = next((r for r in values["types"] if r["id"] == data.get("type_id")), None)
    if data.get("type_id") and (not chosen or chosen["archived"] and old.get("type_id") != chosen["id"]):
        raise Problem("Choose an active type in this workspace", 409)
    ids = data.get("label_ids", [])
    if (
        not isinstance(ids, list)
        or len(ids) > 100
        or any(not isinstance(i, str) for i in ids)
        or len(ids) != len(set(ids))
    ):
        raise Problem("Choose up to 100 distinct labels")
    labels = {r["id"]: r for r in values["labels"]}
    if any(i not in labels or labels[i]["archived"] and i not in old.get("label_ids", []) for i in ids):
        raise Problem("Choose active labels in this workspace", 409)
    data["label_ids"] = sorted(ids)


def receive(store, selected, types, labels, revision):
    previous = store.setting("taxonomy_latest:" + selected, {})
    latest = {"revision": revision}
    for key, incoming in [("types", types), ("labels", labels)]:
        merged = {row["id"]: row for row in previous.get(key, [])}
        merged.update({row["id"]: row for row in incoming})
        latest[key] = list(merged.values())
    store.set_setting("taxonomy_latest:" + selected, latest)
    pending = store.db.execute(
        "SELECT 1 FROM outbox WHERE connection=? AND state IN ('queued','draft','conflict') AND json_extract(payload,'$.operation')='project_taxonomy_write'",
        (selected,),
    ).fetchone()
    if pending:
        return
    for kind, rows in [("type", latest["types"]), ("label", latest["labels"])]:
        for row in rows:
            put(store, selected, kind, row)
    if revision:
        store.set_setting("taxonomy_remote_revision:" + selected, revision)


def configure(store, args):
    def apply():
        selected = workspace(store)
        before = catalog(store, selected)
        if args.get("revision") != before["revision"]:
            raise Problem("Types or labels changed; review the latest settings", 409)
        connection = (
            None
            if selected == LOCAL
            else store.db.execute("SELECT * FROM connections WHERE id=?", (selected,)).fetchone()
        )
        if connection:
            if not before["can_manage"]:
                raise Problem("Workspace management permission is required", 403)
            features = json.loads(connection["capabilities"]).get("features", [])
            if "task-taxonomy-v1" not in features:
                raise Problem("This portal does not support shared types and labels yet", 409)
        kind, action = args.get("kind"), args.get("operation")
        if kind not in ("type", "label"):
            raise Problem("Choose types or labels")
        rows = before["types" if kind == "type" else "labels"]
        changes = []
        row = next((r for r in rows if r["id"] == args.get("id")), None)
        if action == "reorder" and kind == "type":
            order = args.get("order", [])
            if len(order) != len(set(order)) or set(order) != {r["id"] for r in rows if not r["archived"]}:
                raise Problem("Provide every active type exactly once")
            changes = [
                dict(r, position=order.index(r["id"]), revision=r["revision"] + 1)
                for r in rows
                if not r["archived"]
            ]
        else:
            if action == "create":
                row = dict(id=uid(), name="", archived=False, revision=0)
                if kind == "type":
                    row.update(icon="task", position=len(rows), is_default=False)
            if not row:
                raise Problem("Value unavailable in this workspace", 404)
            if action in ("create", "update"):
                name = args.get("name", row["name"])
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
                    raise Problem("Enter a name of 1–60 characters")
                if any(
                    r["id"] != row["id"] and r["name"].casefold() == name.strip().casefold() for r in rows
                ):
                    raise Problem("This name already exists, including archived values")
                row["name"] = name.strip()
                if kind == "type":
                    row["icon"] = args.get("icon", row["icon"])
                    if row["icon"] not in ICONS:
                        raise Problem("Choose a supported icon")
            elif action == "archive":
                if row.get("is_default"):
                    raise Problem("Choose another default type first")
                row["archived"] = True
            elif action == "default" and kind == "type" and not row["archived"]:
                changes.extend(
                    dict(r, is_default=False, revision=r["revision"] + 1)
                    for r in rows
                    if r["is_default"] and r["id"] != row["id"]
                )
                row["is_default"] = True
            else:
                raise Problem("Unsupported settings action")
            row["revision"] += 1
            changes.append(row)
        for row in changes:
            put(store, selected, kind, row)
        if connection:
            arguments = dict(args, revision=store.setting("taxonomy_remote_revision:" + selected))
            if action == "create":
                arguments["id"] = changes[0]["id"]
            store.db.execute(
                "INSERT INTO outbox(id,connection,item_id,base,payload) VALUES(?,?,?,?,?)",
                (
                    args["idempotency_key"],
                    selected,
                    "taxonomy:" + selected,
                    json.dumps(before),
                    json.dumps(dict(operation="project_taxonomy_write", arguments=arguments)),
                ),
            )
            store.sync_wake.set()
        return catalog(store, selected)

    return store.replay(args.get("idempotency_key"), args, apply)


def review(store, body):
    selected = workspace(store)
    latest = store.setting("taxonomy_latest:" + selected)
    if not latest or latest["revision"] != body.get("revision"):
        raise Problem("Portal settings changed; reload before resolving", 409)
    choice = body.get("choice")
    if choice not in ("local", "remote"):
        raise Problem("Choose your changes or portal settings")
    if choice == "remote":
        store.db.execute(
            "UPDATE outbox SET state='discarded' WHERE connection=? AND state IN ('queued','draft','conflict') AND json_extract(payload,'$.operation')='project_taxonomy_write'",
            (selected,),
        )
        store.db.execute("DELETE FROM task_taxonomy WHERE workspace=?", (selected,))
        receive(store, selected, latest["types"], latest["labels"], latest["revision"])
    else:
        for row in store.db.execute(
            "SELECT id,payload FROM outbox WHERE connection=? AND state IN ('draft','conflict') AND json_extract(payload,'$.operation')='project_taxonomy_write' ORDER BY rowid",
            (selected,),
        ).fetchall():
            payload = json.loads(row["payload"])
            payload["arguments"]["revision"] = latest["revision"]
            # Changed payload needs a new receipt identity.
            new_id = uid()
            payload["arguments"]["idempotency_key"] = new_id
            store.db.execute(
                "UPDATE outbox SET id=?,payload=?,state='queued',reason='' WHERE id=?",
                (new_id, json.dumps(payload), row["id"]),
            )
        store.sync_wake.set()
    return catalog(store, selected)
