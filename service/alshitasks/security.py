import hashlib
import hmac
import json
import secrets
import shutil
import subprocess
import os
from pathlib import Path
from .store import Problem, uid

SCOPES = {
    "notifications:read",
    "project:read",
    "project:write",
    "logs:read",
    "logs:write",
    "monitor:read",
    "monitor:heartbeat",
}


class Secrets:
    """Linux Secret Service or Windows user-bound DPAPI; never plaintext."""

    def windows_path(self, ident):
        from .paths import private_dir
        from .platform_support import windows_home
        directory = private_dir(private_dir(windows_home()) / "secrets")
        return directory / (hashlib.sha256(ident.encode()).hexdigest() + ".dpapi")

    def run(self, args, value=None):
        if not shutil.which("secret-tool"):
            raise Problem(
                "Linux Secret Service is unavailable. Install libsecret-tools and unlock your login keyring.",
                503,
            )
        p = subprocess.run(["secret-tool", *args], input=value, capture_output=True, text=True, timeout=15)
        if p.returncode:
            raise Problem("Unlock your Linux Secret Service keyring to continue", 503)
        return p.stdout.rstrip("\n")

    def put(self, ident, value):
        if os.name == "nt":
            import win32crypt
            path = self.windows_path(ident)
            temporary = path.with_suffix("." + secrets.token_hex(8) + ".tmp")
            try:
                with temporary.open("wb") as stream:
                    stream.write(win32crypt.CryptProtectData(value.encode(), "Alshival.Ai", None, None, None, 1))
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            return
        self.run(["store", "--label=Alshival.Ai", "application", "alshitasks", "id", ident], value)

    def get(self, ident):
        if os.name == "nt":
            import win32crypt
            try:
                return win32crypt.CryptUnprotectData(self.windows_path(ident).read_bytes(), None, None, None, 1)[1].decode()
            except FileNotFoundError:
                return ""
        return self.run(["lookup", "application", "alshitasks", "id", ident])

    def delete(self, ident):
        if os.name == "nt":
            self.windows_path(ident).unlink(missing_ok=True)
            return
        self.run(["clear", "application", "alshitasks", "id", ident])


def grant_create(store, body):
    scopes = body.get("scopes", ["project:read"])
    if not isinstance(scopes, list) or not scopes or set(scopes) - SCOPES:
        raise Problem("Invalid scopes")
    resource = body.get("resource_id")
    if resource and store.item(resource)["kind"] != "resource":
        raise Problem("Invalid resource")
    if set(scopes) & {"logs:write", "monitor:heartbeat"} and not resource:
        raise Problem("Log ingestion requires a resource-scoped grant")
    ident = uid()
    token = "alt_" + secrets.token_urlsafe(32)
    store.db.execute(
        "INSERT INTO grants VALUES(?,?,?,?,?,NULL)",
        (
            ident,
            str(body.get("name", "Local client"))[:120],
            hashlib.sha256(token.encode()).hexdigest(),
            json.dumps(scopes),
            resource,
        ),
    )
    return dict(id=ident, token=token)


def authorize(store, token, scope, resource=None):
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = store.db.execute("SELECT * FROM grants WHERE digest=? AND revoked_at IS NULL", (digest,)).fetchone()
    if not row or not hmac.compare_digest(row["digest"], digest):
        raise Problem("Authentication required", 401)
    if scope not in json.loads(row["scopes"]):
        raise Problem("Grant does not permit this operation", 403)
    if row["resource_id"] and row["resource_id"] != resource:
        raise Problem("Resource scope mismatch", 403)
    return row
