#!/usr/bin/env python3
"""Assemble amd64 package from pinned Electron and locally built assets."""

import json
import os
from pathlib import Path
import shutil
import subprocess

# Package content is public application code, readable by every installed user.
os.umask(0o022)
root = Path(__file__).resolve().parents[1]
version = json.loads((root / "package.json").read_text())["version"]
subprocess.run(["node", str(root / "node_modules/electron/install.js")], check=True)
stage = root / "build" / f"alshitasks-{version}-amd64"
if stage.exists():
    raise SystemExit(
        "Build staging directory exists; choose a new version or remove only your verified staging files"
    )
lib = stage / "usr/lib/alshitasks"
lib.mkdir(parents=True)
for name in ("desktop", "service", "contracts", "dist", "docs"):
    shutil.copytree(root / name, lib / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "_vendor"))
# Bundle CPython codecs for supported Ubuntu Python versions, without runtime pip.
for minor in ("3.12", "3.13", "3.14"):
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python-version",
            minor,
            "--python-platform",
            "x86_64-manylinux_2_28",
            "--only-binary",
            ":all:",
            "--target",
            str(lib / "service/_vendor" / minor),
            "-r",
            str(root / "service/log-requirements.txt"),
        ],
        check=True,
    )
for name in ("package.json", "LICENSE", "NOTICE"):
    shutil.copy2(root / name, lib / name)
shutil.copytree(root / "node_modules/electron/dist", lib / "electron")
launcher = stage / "usr/bin/alshitasks"
launcher.parent.mkdir(parents=True)
launcher.write_text("""#!/bin/sh
if [ "$#" -eq 0 ]; then
  exec /usr/lib/alshitasks/electron/electron /usr/lib/alshitasks
fi
if [ "$1" = desktop ]; then
  shift
  exec /usr/lib/alshitasks/electron/electron /usr/lib/alshitasks "$@"
fi
exec /usr/bin/python3 /usr/lib/alshitasks/service/alshitasks-cli.py "$@"
""")
launcher.chmod(0o755)
applications = stage / "usr/share/applications"
applications.mkdir(parents=True)
(applications / "alshitasks.desktop").write_text("""[Desktop Entry]
Type=Application
Name=Alshival.Ai
Comment=Local task boards, resources and monitoring
Exec=alshitasks
Icon=alshitasks
Terminal=false
Categories=Office;ProjectManagement;
StartupWMClass=alshitasks
""")
for source in (root / "desktop/assets").glob("alshival-*x*.png"):
    size = source.stem.split("-")[-1]
    destination = stage / f"usr/share/icons/hicolor/{size}/apps"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination / "alshitasks.png")
control = stage / "DEBIAN"
control.mkdir()
(control / "control").write_text(f"""Package: alshitasks
Version: {version}
Architecture: amd64
Maintainer: Alshival.Ai <support@alshival.ai>
Section: utils
Priority: optional
Depends: python3 (>= 3.12), python3-jsonschema, python3-gi, libsecret-tools, libsecret-1-0, libgtk-3-0t64, libnss3, libasound2t64, libgbm1, libxss1, libnotify-bin, libglib2.0-bin, xdg-utils, pkexec, polkitd
Recommends: iputils-ping, python3-psycopg, python3-pymysql, python3-redis, python3-pymongo
Description: Alshival.Ai standalone Linux desktop application
 Local-first boards, resources, logs, monitoring, SSH aliases and optional portal sync.
""")
policy = stage / "usr/share/polkit-1/actions/ai.alshival.alshitasks.policy"
policy.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(root / "scripts/ai.alshival.alshitasks.policy", policy)
# Chromium needs its setuid helper on hosts that restrict unprivileged user namespaces.
(lib / "electron/chrome-sandbox").chmod(0o4755)
output = root / "build" / f"alshitasks_{version}_amd64.deb"
subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(output)], check=True)
print(output)
