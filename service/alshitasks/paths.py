"""Private per-user XDG layout. Never share runtime or state across Unix users."""

import os
import stat
from pathlib import Path
from .platform_support import WINDOWS, owned, protect, windows_home


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    s = path.lstat()
    if not stat.S_ISDIR(s.st_mode) or path.is_symlink() or (WINDOWS and path.is_junction()) or not owned(path):
        raise RuntimeError("Application directory must be owned by the current user")
    protect(path, directory=True)
    return path


class Paths:
    def __init__(self, root=None):
        # Explicit fixture/standalone roots must never adopt host enrollment.
        self.managed = root is None
        self.home = Path(root) / "home" if root else Path.home()
        if root:
            self.data = private_dir(Path(root) / "data")
            self.config = private_dir(Path(root) / "config")
            self.runtime = private_dir(Path(root) / "runtime")
        elif WINDOWS:
            base = private_dir(windows_home())
            self.data = private_dir(base / "data")
            self.config = private_dir(base / "config")
            self.runtime = private_dir(base / "runtime")
        else:
            home = Path.home()
            self.data = private_dir(
                Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "alshitasks"
            )
            self.config = private_dir(
                Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "alshitasks"
            )
            runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
            if not runtime.is_dir() or runtime.stat().st_uid != os.getuid():
                raise RuntimeError("A private XDG_RUNTIME_DIR is required")
            self.runtime = private_dir(runtime / "alshitasks")
        self.attachments = private_dir(self.data / "attachments")
        self.socket = self.runtime / "service.sock"
        self.endpoint = self.runtime / "service.json"
