"""Checksummed attachment downloads; a partial transfer never replaces a file."""

import base64
import hashlib
import os
import json
from .store import Problem, uid
from .portal import Portal


def download_attachments(store, connection, attachments, transport, token):
    from .portal import cached_item_id, Portal

    for meta in attachments:
        if not isinstance(meta["size"], int) or not 0 <= meta["size"] <= 20 * 1024**2:
            raise Problem("Attachment exceeds local bounds", 413)
        with store.lock:
            if Portal(store).connection(connection["id"])["state"] != "connected":
                return
            ident = store.setting("attachment_local:" + connection["id"] + ":" + meta["id"], uid())
            pending = store.setting("file_job:" + ident, {})
            if pending and pending.get("state") != "sent":
                continue
            if meta.get("deleted"):
                with store.tx():
                    store.set_setting("file_deleted:" + ident, True)
                    store.set_setting("file_remote:" + ident, meta)
                continue
            if meta.get("scan_status", "clean") != "clean":
                continue
            existing = store.db.execute("SELECT sha256 FROM attachments WHERE id=?", (ident,)).fetchone()
            if existing and existing[0] == meta["sha256"]:
                with store.tx():
                    store.set_setting("file_remote:" + ident, meta)
                    store.set_setting("file_deleted:" + ident, False)
                    store.db.execute("UPDATE attachments SET name=? WHERE id=?", (meta["name"], ident))
                continue
            item_id = cached_item_id(store, connection["id"], meta["item_id"])
            if not store.db.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
                continue
        path = store.paths.attachments / (ident + ".partial")
        with store.tx():
            store.set_setting("attachment_local:" + connection["id"] + ":" + meta["id"], ident)
            if store.setting("download_sha:" + ident) != meta["sha256"]:
                path.unlink(missing_ok=True)
            store.set_setting("download_sha:" + ident, meta["sha256"])
        digest = hashlib.sha256()
        offset = path.stat().st_size if path.exists() else 0
        if offset > meta["size"] or (offset != meta["size"] and offset % (256 * 1024)):
            path.unlink(missing_ok=True)
            offset = 0
        if offset:
            with path.open("rb") as prior:
                while chunk := prior.read(256 * 1024):
                    digest.update(chunk)
        try:
            with path.open("ab") as target:
                os.chmod(path, 0o600)
                while offset < meta["size"]:
                    page = transport(
                        connection["origin"],
                        "/desktop/v1/workspaces/"
                        + connection["workspace"]
                        + (
                            "/files/"
                            if "graph-files-v2" in json.loads(connection["capabilities"]).get("features", [])
                            else "/attachments/"
                        )
                        + meta["id"]
                        + "?offset="
                        + str(offset),
                        token,
                    )
                    chunk = base64.b64decode(page["content"], validate=True)
                    if (
                        page["offset"] != offset
                        or page["sha256"] != meta["sha256"]
                        or not chunk
                        or len(chunk) > 256 * 1024
                        or offset + len(chunk) > meta["size"]
                    ):
                        raise Problem("Attachment download changed", 409)
                    target.write(chunk)
                    target.flush()
                    digest.update(chunk)
                    offset += len(chunk)
            if digest.hexdigest() != meta["sha256"]:
                path.unlink(missing_ok=True)
                raise Problem("Attachment checksum mismatch", 409)
            with store.tx():
                if Portal(store).connection(connection["id"])["state"] != "connected":
                    return
                pending = store.setting("file_job:" + ident, {})
                if pending and pending.get("state") != "sent":
                    return
                path.replace(store.paths.attachments / ident)
                store.db.execute(
                    "INSERT OR REPLACE INTO attachments VALUES(?,?,?,?,?)",
                    (ident, item_id, meta["name"], meta["size"], meta["sha256"]),
                )
                store.set_setting("attachment_local:" + connection["id"] + ":" + meta["id"], ident)
                store.set_setting("file_remote:" + ident, meta)
                store.set_setting("file_deleted:" + ident, False)
        except (OSError, Problem):
            # The partial file and its checksum binding survive network failures.
            raise


def upload_attachments(store, connection, transport, token):
    """Durable upload sessions resume chunks and keep a stable completion identity."""
    import json
    import time

    if "graph-files-v2" not in json.loads(connection["capabilities"]).get("features", []):
        return
    with store.lock:
        jobs = [
            (r["key"], json.loads(r["value"]))
            for r in store.db.execute("SELECT key,value FROM settings WHERE key LIKE 'file_job:%'")
        ]
    for key, job in jobs:
        if (
            job.get("connection") != connection["id"]
            or job.get("state") in ("sent", "conflict")
            or job.get("next", 0) > time.time()
        ):
            continue
        try:
            with store.lock:
                row = store.db.execute(
                    "SELECT remote_id FROM items WHERE id=? AND connection=?",
                    (job["item_id"], connection["id"]),
                ).fetchone()
                if not row or not row["remote_id"]:
                    continue
                if Portal(store).connection(connection["id"])["state"] != "connected":
                    return
                meta = store.setting("file_remote:" + job["id"])
            prefix = "/desktop/v1/workspaces/" + connection["workspace"]
            if job.get("operation") == "delete":
                if meta:
                    transport(
                        connection["origin"],
                        prefix + "/files/" + meta["id"],
                        token,
                        body={"operation": "delete", "revision": meta["revision"]},
                    )
                result = None
            else:
                path = store.paths.attachments / job["id"]
                data = path.read_bytes()
                if len(data) != job["size"] or hashlib.sha256(data).hexdigest() != job["sha256"]:
                    raise Problem("Local attachment changed during upload", 409)
                endpoint = prefix + "/file-uploads/" + job["upload_id"]
                begin = {
                    "operation": "begin",
                    "item_id": row["remote_id"],
                    "name": job["name"],
                    "size": job["size"],
                    "sha256": job["sha256"],
                    "attachment_id": meta["id"] if meta else job["upload_id"],
                    "revision": meta["revision"] if meta else 0,
                }
                # Persist the full initial request; lost replies must not change its base revision.
                with store.tx():
                    if not job.get("request"):
                        job["request"] = begin
                        store.set_setting(key, job)
                response = transport(connection["origin"], endpoint, token, body=job["request"])
                result = response.get("result")
                if result and (result.get("expired") or result.get("cancelled")):
                    with store.tx():
                        job["upload_id"] = uid()
                        job.pop("request", None)
                        store.set_setting(key, job)
                    raise Problem("Upload session expired; restarting transfer", 503)
                if not result:
                    received = set(response["offsets"])
                    for offset in range(0, len(data), 256 * 1024):
                        if offset not in received:
                            with store.lock:
                                if Portal(store).connection(connection["id"])["state"] != "connected":
                                    return
                            transport(
                                connection["origin"],
                                endpoint,
                                token,
                                body={
                                    "operation": "chunk",
                                    "offset": offset,
                                    "content": base64.b64encode(data[offset : offset + 256 * 1024]).decode(),
                                },
                            )
                    result = transport(connection["origin"], endpoint, token, body={"operation": "commit"})[
                        "result"
                    ]
            with store.tx():
                current = store.setting(key)
                if current.get("upload_id") != job["upload_id"]:
                    continue
                job["state"] = "sent"
                store.set_setting(key, job)
                if result:
                    store.set_setting("file_remote:" + job["id"], result)
                    store.set_setting("attachment_local:" + connection["id"] + ":" + result["id"], job["id"])
        except Exception as error:
            with store.tx():
                current = store.setting(key)
                if current.get("upload_id") != job["upload_id"]:
                    continue
                job.update(
                    state="conflict" if isinstance(error, Problem) and error.status == 409 else "attention",
                    error=str(error) if isinstance(error, Problem) else "Transfer interrupted",
                    next=time.time() + 30,
                )
                store.set_setting(key, job)
