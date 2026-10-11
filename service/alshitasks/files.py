import base64
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from .store import Problem, uid, now

MAX_ATTACHMENT = 20 * 1024 * 1024
MAX_RESTORE = 64 * 1024**3


def attach(store, item_id, name, content):
    store.item(item_id)
    if not isinstance(name, str) or not name or len(name) > 255:
        raise Problem("Invalid attachment name")
    try:
        data = base64.b64decode(content, validate=True)
    except Exception:
        raise Problem("Invalid attachment encoding")
    if len(data) > MAX_ATTACHMENT:
        raise Problem("Attachment exceeds 20 MiB", 413)
    ident = uid()
    p = store.paths.attachments / ident
    with p.open("xb") as f:
        f.write(data)
    p.chmod(0o600)
    store.db.execute(
        "INSERT INTO attachments VALUES(?,?,?,?,?)",
        (ident, item_id, Path(name).name, len(data), hashlib.sha256(data).hexdigest()),
    )
    return dict(id=ident, name=Path(name).name, size=len(data))


def backup(store, logs, destination):
    p = Path(destination).expanduser()
    if p.exists():
        raise Problem("Destination exists; choose a new backup name", 409)
    with tempfile.TemporaryDirectory(dir=store.paths.data) as d:
        d = Path(d)
        for name, db in [("projects.sqlite", store.db), ("logs.sqlite", logs.db)]:
            target = sqlite3.connect(d / name)
            db.backup(target)
            target.close()
        manifest = {"format": "alshitasks-backup-v1", "created_at": now(), "files": {}}
        attachments = [
            store.paths.attachments / x["id"] for x in store.db.execute("SELECT id FROM attachments")
        ]
        sources = [(d / "projects.sqlite", "projects.sqlite"), (d / "logs.sqlite", "logs.sqlite")] + [
            (x, "attachments/" + x.name) for x in attachments
        ]
        with p.open("xb") as f:
            os.chmod(p, 0o600)
            with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
                for source, name in sources:
                    with source.open("rb") as stream:
                        manifest["files"][name] = hashlib.file_digest(stream, "sha256").hexdigest()
                    z.write(source, name)
                z.writestr("manifest.json", json.dumps(manifest))
    return dict(path=str(p), credentials_included=False)


def validate_backup(path):
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > 100000 or sum(i.file_size for i in infos) > MAX_RESTORE:
            raise Problem("Backup exceeds restore bounds", 413)
        if len({i.filename for i in infos}) != len(infos):
            raise Problem("Duplicate backup paths")
        manifest = json.loads(z.read("manifest.json"))
        if manifest.get("format") != "alshitasks-backup-v1":
            raise Problem("Unsupported backup")
        if set(z.namelist()) != set(manifest["files"]) | {"manifest.json"}:
            raise Problem("Unexpected backup files")
        if not {"projects.sqlite", "logs.sqlite"} <= set(manifest["files"]):
            raise Problem("Incomplete backup")
        for name, digest in manifest["files"].items():
            if name not in ("projects.sqlite", "logs.sqlite"):
                parts = Path(name).parts
                if (
                    len(parts) != 2
                    or parts[0] != "attachments"
                    or not all(c in "abcdef0123456789-" for c in parts[1])
                ):
                    raise Problem("Invalid backup path")
            with z.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise Problem("Backup integrity failed")
        return manifest


def restore(paths, path):
    """Offline only; stage and verify first, preserve current state for recovery."""
    data = validate_backup(path)
    with zipfile.ZipFile(path) as archive:
        if shutil.disk_usage(paths.data).free < sum(i.file_size for i in archive.infolist()) + 64 * 1024**2:
            raise Problem("Insufficient free space to stage the backup safely", 507)
    recovery = paths.data / ("before-restore-" + uid())
    recovery.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(dir=paths.data) as d:
        d = Path(d)
        with zipfile.ZipFile(path) as archive:
            for name in data["files"]:
                p = d / name
                p.parent.mkdir(exist_ok=True)
                with archive.open(name) as source, p.open("xb") as target:
                    shutil.copyfileobj(source, target, 1024**2)
                p.chmod(0o600)
        for name in ("projects.sqlite", "logs.sqlite"):
            db = sqlite3.connect(d / name)
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise Problem("Invalid SQLite backup")
            if name == "projects.sqlite" and db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise Problem("Unsupported schema")
            if name == "projects.sqlite":
                # Copies retain content, but credentials must be reauthorized and local paths recreated.
                db.execute("DELETE FROM grants")
                db.execute("DELETE FROM aliases")
                db.execute("DELETE FROM forwarding")
                db.execute("DELETE FROM forward_queue")
                db.execute("UPDATE connections SET state='disconnected'")
                db.execute("DELETE FROM monitors")
                db.commit()
            db.close()
        for name in (
            "projects.sqlite",
            "projects.sqlite-wal",
            "projects.sqlite-shm",
            "logs.sqlite",
            "logs.sqlite-wal",
            "logs.sqlite-shm",
            "attachments",
        ):
            source = paths.data / name
            if source.exists():
                shutil.move(source, recovery / name)
        shutil.move(d / "projects.sqlite", paths.data / "projects.sqlite")
        shutil.move(d / "logs.sqlite", paths.data / "logs.sqlite")
        if (d / "attachments").exists():
            shutil.move(d / "attachments", paths.attachments)
        else:
            paths.attachments.mkdir(mode=0o700)
    managed = paths.home / ".ssh/alshitasks.conf"
    if managed.exists() and not managed.is_symlink():
        content = managed.read_text()
        if content.startswith("# Managed by Alshival.Ai."):
            shutil.copy2(managed, recovery / "alshitasks.conf")
            managed.write_text("# Managed by Alshival.Ai. Restored aliases require setup again.\n")
            managed.chmod(0o600)
    return dict(recovery=str(recovery))
