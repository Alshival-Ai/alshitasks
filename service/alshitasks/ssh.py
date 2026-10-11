import os
import re
import subprocess
from pathlib import Path
from .store import Problem


def configure(store, resource, body, home=None):
    if store.item(resource)["kind"] != "resource":
        raise Problem("SSH requires a resource")
    home = Path(home or store.paths.home)
    directory = home / ".ssh"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    alias = body.get("alias", "")
    if not re.fullmatch(r"alt-[A-Za-z0-9_-]{1,64}", alias):
        raise Problem("Use an alias beginning alt- followed by letters, digits or dashes")
    host = body.get("host", "")
    user = body.get("username", "")
    port = body.get("port", 22)
    if (
        not re.fullmatch(r"[A-Za-z0-9.:-]{1,255}", host)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", user)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
    ):
        raise Problem("Invalid SSH host, username or port")
    identity = body.get("identity_file")
    if identity:
        p = Path(identity).expanduser()
        if not p.is_file() or p.stat().st_uid != os.getuid() or any(x in str(p) for x in ("\n", "\r", '"')):
            raise Problem("Choose an existing key owned by you")
        identity = str(p.resolve())
    else:
        if not body.get("agent"):
            raise Problem("Choose a local key or SSH agent")
    managed = directory / "alshitasks.conf"
    config = directory / "config"
    if managed.is_symlink() or config.is_symlink():
        raise Problem("Refusing symlinked SSH configuration")
    # Evaluate OpenSSH configuration to catch explicit aliases, wildcards and Includes.
    resolved = subprocess.run(
        ["ssh", "-F", str(config) if config.exists() else "/dev/null", "-G", alias],
        capture_output=True,
        text=True,
        timeout=5,
    )
    existing = store.db.execute("SELECT * FROM aliases WHERE alias=?", (alias,)).fetchone()
    if existing and existing["resource_id"] != resource:
        raise Problem("Alias already assigned", 409)
    if not existing and any(
        line.startswith("hostname ") and line.split(" ", 1)[1].strip() != alias
        for line in resolved.stdout.splitlines()
    ):
        raise Problem("Alias collides with existing SSH configuration", 409)
    current = config.read_text() if config.exists() else ""
    # Explicit Host names collide even when they keep the default hostname.
    for line in current.splitlines():
        if line.strip().lower().startswith("host ") and alias in line.split()[1:]:
            raise Problem("Alias collides with existing configuration", 409)
    if managed.is_symlink() or config.is_symlink():
        raise Problem("Refusing symlinked SSH configuration")
    store.db.execute(
        "INSERT OR REPLACE INTO aliases VALUES(?,?,?,?,?,?,?)",
        (resource, alias, host, user, port, identity, int(bool(body.get("agent")))),
    )
    render(store, managed)
    include = 'Include "' + str(managed) + '"'
    if include not in current:
        temporary = config.with_name("config.alshitasks-next")
        with temporary.open("x") as f:
            f.write(include + "\n" + current)
        temporary.chmod(0o600)
        temporary.replace(config)
    return {"alias": alias}


def render(store, path=None):
    path = path or store.paths.home / ".ssh/alshitasks.conf"
    content = "# Managed by Alshival.Ai. Private keys and paths never sync.\n"
    for r in store.db.execute("SELECT * FROM aliases ORDER BY alias"):
        content += f"Host {r['alias']}\n  HostName {r['host']}\n  User {r['username']}\n  Port {r['port']}\n  StrictHostKeyChecking ask\n"
        if r["identity_file"]:
            content += '  IdentitiesOnly yes\n  IdentityFile "' + r["identity_file"] + '"\n'
        content += "\n"
    temporary = path.with_name("alshitasks.conf.next")
    if temporary.exists():
        raise Problem("SSH configuration write already pending", 409)
    with temporary.open("x") as f:
        f.write(content)
    temporary.chmod(0o600)
    temporary.replace(path)


def revoke(store, resource):
    store.db.execute("DELETE FROM aliases WHERE resource_id=?", (resource,))
    render(store)
    return {"revoked": True}


def open_terminal(store, resource):
    row = store.db.execute("SELECT alias FROM aliases WHERE resource_id=?", (resource,)).fetchone()
    if not row:
        raise Problem("SSH alias unavailable", 404)
    import shutil

    if not shutil.which("x-terminal-emulator"):
        raise Problem("Set a default terminal emulator first", 503)
    subprocess.Popen(["x-terminal-emulator", "-e", "ssh", row["alias"]], start_new_session=True)
    return {"opened": True}
