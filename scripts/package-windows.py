"""Build a per-user Windows installer with a private, pinned Python runtime."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

root = Path(__file__).resolve().parents[1]
build = root / "build"
build.mkdir(exist_ok=True)
version = "3.13.9"
archive = build / f"python-{version}-embed-amd64.zip"
if not archive.exists():
    urllib.request.urlretrieve(f"https://www.python.org/ftp/python/{version}/{archive.name}", archive)
if hashlib.sha256(archive.read_bytes()).hexdigest() != "91d828c2da3a029b41699e918674a0cb379c02cf20dab9c501306885f837402a":
    raise SystemExit("Python runtime checksum mismatch")
runtime = build / "windows-python"
runtime.mkdir(exist_ok=True)
with zipfile.ZipFile(archive) as bundle:
    bundle.extractall(runtime)
(runtime / "python313._pth").write_text("python313.zip\n.\nLib/site-packages\n../app/service\nimport site\n", encoding="utf-8")
subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "--only-binary=:all:",
                "--python-version", "3.13", "--platform", "win_amd64", "--target", str(runtime / "Lib/site-packages"),
                "-r", str(root / "service/windows-requirements.txt")], check=True)
subprocess.run([str(runtime / "python.exe"), "-c", "import win32crypt, win32security, jsonschema, pydantic_core"], check=True)
subprocess.run(["node", str(root / "node_modules/electron-builder/cli.js"), "--win", "nsis", "--x64", "--publish", "never"], cwd=root, check=True)
