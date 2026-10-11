"""Explicit local-board publication with durable jobs and stable local identities."""

import base64
import hashlib
import io
import json
import time
import zipfile
from .store import Problem, uid, DEFAULT, now
from .workspaces import LOCAL

CHUNK = 256 * 1024


def jobs(store):
    return [
        json.loads(r[0])
        for r in store.db.execute("SELECT value FROM settings WHERE key LIKE 'publication:%'")
    ]


def start(store, body):
    from .portal import Portal

    account = store.setting("active_account")
    if not account:
        raise Problem("Sign in before publishing")
    connection = Portal(store).connection(body["workspace_id"])
    if (
        connection["state"] != "connected"
        or connection["account"] != account["id"]
        or connection["origin"] != account["origin"]
    ):
        raise Problem("Choose a connected workspace")
    caps = json.loads(connection["capabilities"])
    if "board-import-v1" not in caps.get("features", []):
        raise Problem("This portal release does not yet support publishing local boards", 409)
    scopes = store.setting("workspace:" + connection["id"], {}).get("scopes", [])
    if not set(scopes) & {"board:write", "project:write"}:
        raise Problem("Board management permission is required", 403)
    board_id = body["board_id"]
    synthetic = board_id == LOCAL
    if store.setting("publishing:" + board_id):
        raise Problem("Publication already in progress", 409)
    if synthetic:
        board = {
            "id": uid(),
            "kind": "board",
            "revision": 1,
            "data": dict(DEFAULT, title="Local workspace"),
            "author_id": "local",
            "created_at": now(),
            "updated_at": now(),
        }
    else:
        row = store.db.execute(
            "SELECT * FROM items WHERE id=? AND connection IS NULL AND kind='board'", (board_id,)
        ).fetchone()
        if not row:
            raise Problem("Choose a local board", 404)
        board = store.row(row)
    records = [board]
    for row in store.db.execute("SELECT * FROM items WHERE connection IS NULL AND kind!='board'"):
        item = store.row(row)
        if item["data"].get("board_id") == (None if synthetic else board_id):
            item["data"]["board_id"] = board["id"]
            records.append(item)
    ids = {r["id"] for r in records}
    edges = [
        dict(r)
        for r in store.db.execute("SELECT * FROM edges WHERE deleted=0")
        if r["source_id"] in ids or r["target_id"] in ids
    ]
    if any(e["source_id"] not in ids or e["target_id"] not in ids for e in edges):
        raise Problem("Resolve cross-board relationships before publishing")
    statuses = [x for x in store.statuses() if x.get("board_id") == (None if synthetic else board_id)]
    members = body.get("members", {})
    allowed = {str(m["id"]) for m in store.setting("members:" + connection["id"], [])}
    mapped = {}
    for item in records:
        for field in ("assignee", "reviewer"):
            value = item["data"].get(field)
            if value:
                target = members.get(str(value))
                if target is not None and target not in allowed:
                    raise Problem("Choose an eligible workspace member")
                if str(value) not in members:
                    raise Problem("Map every local assignee and reviewer before publishing")
                mapped[str(value)] = (
                    store.setting("member_remote:" + connection["id"] + ":" + str(target)) if target else None
                )
    attachments = [dict(r) for r in store.db.execute("SELECT * FROM attachments") if r["item_id"] in ids]
    if sum(a["size"] for a in attachments) > 100 * 1024**2:
        raise Problem("Board attachments exceed the 100 MiB publication limit", 413)
    ident = uid()
    manifest = {
        "format": "alshitasks-board-v1",
        "items": records,
        "statuses": statuses,
        "edges": edges,
        "attachments": attachments,
        "members": mapped,
    }
    job = {
        "id": ident,
        "workspace_id": connection["id"],
        "source_board": board_id,
        "board_id": board["id"],
        "synthetic": synthetic,
        "manifest": manifest,
        "state": "queued",
        "created_at": now(),
        "next": 0,
    }
    store.set_setting("publication:" + ident, job)
    store.set_setting("publishing:" + board_id, ident)
    store.set_setting("publish_offer", False)
    return {"id": ident, "message": "Board publication queued"}


def archive_bytes(store, manifest):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:

        def write(name, data):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)

        write("manifest.json", json.dumps(manifest, sort_keys=True).encode())
        for meta in manifest["attachments"]:
            content = (store.paths.attachments / meta["id"]).read_bytes()
            if len(content) != meta["size"] or hashlib.sha256(content).hexdigest() != meta["sha256"]:
                raise Problem("Attachment changed; publication paused", 409)
            write("attachments/" + meta["id"], content)
    data = output.getvalue()
    if len(data) > 128 * 1024**2:
        raise Problem("Publication archive exceeds 128 MiB", 413)
    return data


def finish(store, job, result):
    connection = job["workspace_id"]
    manifest = job["manifest"]
    # No local data is rebound until the server confirms its atomic commit.
    for original in manifest["items"]:
        ident = original["id"]
        remote = result["items"][ident]
        if job["synthetic"] and original["kind"] == "board":
            store.db.execute(
                "INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?,?,?,?)",
                (ident, "board", 1, json.dumps(original["data"]), "local", now(), now(), None, None),
            )
        data = dict(original["data"])
        for field in ("assignee", "reviewer"):
            member = manifest["members"].get(str(data.get(field)))
            if data.get(field):
                from .portal import local_id

                data[field] = "member:" + local_id(connection, str(member)) if member else None
        store.db.execute(
            "UPDATE items SET connection=?,remote_id=?,data=?,revision=revision+1 WHERE id=?",
            (connection, remote["id"], json.dumps(data), ident),
        )
        store.set_setting("remote_base:" + ident, remote)
    for local, remote in result["statuses"].items():
        store.set_setting("status_remote:" + local, remote)
        store.set_setting("status_local:" + connection + ":" + remote, local)
        if job["synthetic"]:
            # Default local columns belong to the remaining local workspace: clone for the published board.
            from .portal import local_id

            new = local_id(connection, remote)
            old = next(s for s in manifest["statuses"] if s["id"] == local)
            data = {k: v for k, v in old.items() if k not in ("id", "revision")}
            data["board_id"] = job["board_id"]
            store.db.execute("INSERT INTO statuses VALUES(?,?,?)", (new, 1, json.dumps(data)))
            store.set_setting("status_remote:" + new, remote)
            store.set_setting("status_local:" + connection + ":" + remote, new)
            for row in store.db.execute(
                "SELECT id,data FROM items WHERE connection=?", (connection,)
            ).fetchall():
                value = json.loads(row["data"])
                if value.get("status_id") == local:
                    value["status_id"] = new
                    store.db.execute("UPDATE items SET data=? WHERE id=?", (json.dumps(value), row["id"]))
    for local, remote in result["edges"].items():
        store.set_setting("remote_edge:" + local, remote)
        store.set_setting("edge_local:" + connection + ":" + remote["id"], local)
    for local, remote in result.get("attachments", {}).items():
        store.set_setting("attachment_local:" + connection + ":" + remote, local)
    store.set_setting("publishing:" + job["source_board"], None)
    job.update(state="complete", error="", completed_at=now())
    job.pop("manifest", None)
    store.set_setting("publication:" + job["id"], job)
    store.db.execute("UPDATE connections SET cursor=NULL WHERE id=?", (connection,))


def resume_all(store):
    from .portal import Portal, remote

    with store.lock:
        pending = [
            j
            for j in jobs(store)
            if j["state"] in ("queued", "cancelling") and j.get("next", 0) <= time.time()
        ]
    for job in pending:
        try:
            with store.lock:
                c = Portal(store).connection(job["workspace_id"])
                account = store.setting("active_account")
                if not account or c["state"] != "connected" or c["account"] != account["id"]:
                    continue
            token = Portal(store).token(c)
            path = "/desktop/v1/workspaces/" + c["workspace"] + "/imports/" + job["id"]
            if job["state"] == "cancelling":
                response = remote(c["origin"], path, token, body={"operation": "cancel"})
                with store.tx():
                    if response.get("result"):
                        finish(store, job, response["result"])
                    else:
                        job.update(state="cancelled", error="")
                        job.pop("manifest", None)
                        store.set_setting("publication:" + job["id"], job)
                        store.set_setting("publishing:" + job["source_board"], None)
                continue
            archive = archive_bytes(store, job["manifest"])
            state = remote(
                c["origin"],
                path,
                token,
                body={
                    "operation": "begin",
                    "size": len(archive),
                    "sha256": hashlib.sha256(archive).hexdigest(),
                },
            )
            result = state.get("result")
            if not result:
                received = set(state["received"])
                for position, offset in enumerate(range(0, len(archive), CHUNK)):
                    if position in received:
                        continue
                    with store.lock:
                        if (
                            not store.setting("active_account")
                            or Portal(store).connection(c["id"])["state"] != "connected"
                        ):
                            return
                    remote(
                        c["origin"],
                        path,
                        token,
                        body={
                            "operation": "chunk",
                            "position": position,
                            "content": base64.b64encode(archive[offset : offset + CHUNK]).decode(),
                        },
                    )
                with store.lock:
                    current = store.setting("active_account")
                    if current != account or Portal(store).connection(c["id"])["state"] != "connected":
                        return
                result = remote(c["origin"], path, token, body={"operation": "commit"})["result"]
            with store.tx():
                finish(store, job, result)
        except (Problem, OSError) as exc:
            with store.tx():
                retry = isinstance(exc, OSError) or exc.status >= 500 or exc.status == 429
                job.update(
                    state=("cancelling" if job["state"] == "cancelling" else "queued")
                    if retry
                    else "rejected",
                    error=str(exc),
                    next=time.time() + 30,
                )
                store.set_setting("publication:" + job["id"], job)


def dispatch(store, method, body):
    if method == "GET":
        account = store.setting("active_account") or store.setting("last_account")
        visible = []
        for j in jobs(store):
            c = store.db.execute(
                "SELECT account FROM connections WHERE id=?", (j["workspace_id"],)
            ).fetchone()
            if account and c and c[0] == account["id"]:
                visible.append({k: v for k, v in j.items() if k != "manifest"})
        return {"publications": visible}
    if body.get("retry") or body.get("cancel"):
        job = store.setting("publication:" + (body.get("retry") or body["cancel"]))
        if not job:
            raise Problem("Publication unavailable", 404)
        if job["state"] in ("complete", "cancelled"):
            raise Problem("Publication already finished", 409)
        job.update(state="cancelling" if body.get("cancel") else "queued", next=0, error="")
        store.set_setting("publication:" + job["id"], job)
        return {
            "message": "Publication cancellation queued" if body.get("cancel") else "Publication retry queued"
        }
    return start(store, body)
