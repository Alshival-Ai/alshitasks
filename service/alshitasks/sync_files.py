"""Checksummed attachment downloads; a partial transfer never replaces a file."""

import base64
import hashlib
import os
from .store import Problem, uid


def download_attachments(store, connection, attachments, transport, token):
    from .portal import cached_item_id, Portal

    for meta in attachments:
        if not isinstance(meta["size"], int) or not 0 <= meta["size"] <= 20 * 1024**2:
            raise Problem("Attachment exceeds local bounds", 413)
        with store.lock:
            if Portal(store).connection(connection["id"])["state"] != "connected":
                return
            ident = store.setting("attachment_local:" + connection["id"] + ":" + meta["id"], uid())
            existing = store.db.execute("SELECT sha256 FROM attachments WHERE id=?", (ident,)).fetchone()
            if existing and existing[0] == meta["sha256"]:
                continue
            item_id = cached_item_id(store, connection["id"], meta["item_id"])
            if not store.db.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
                continue
        path = store.paths.attachments / (ident + ".partial")
        digest = hashlib.sha256()
        offset = 0
        try:
            with path.open("wb") as target:
                os.chmod(path, 0o600)
                while offset < meta["size"]:
                    page = transport(
                        connection["origin"],
                        "/desktop/v1/workspaces/"
                        + connection["workspace"]
                        + "/attachments/"
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
                    digest.update(chunk)
                    offset += len(chunk)
            if digest.hexdigest() != meta["sha256"]:
                raise Problem("Attachment checksum mismatch", 409)
            with store.tx():
                if Portal(store).connection(connection["id"])["state"] != "connected":
                    return
                path.replace(store.paths.attachments / ident)
                store.db.execute(
                    "INSERT OR REPLACE INTO attachments VALUES(?,?,?,?,?)",
                    (ident, item_id, meta["name"], meta["size"], meta["sha256"]),
                )
                store.set_setting("attachment_local:" + connection["id"] + ":" + meta["id"], ident)
        finally:
            path.unlink(missing_ok=True)
