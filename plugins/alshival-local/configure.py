"""Build a machine-specific local plugin without reading credentials or app data."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

SOURCE = Path(__file__).resolve().parent


def connection(platform, app_directory=None, read_only=False):
    args = ["mcp"] + (["--read-only"] if read_only else [])
    if platform == "win32":
        root = Path(app_directory) if app_directory else Path(os.environ["LOCALAPPDATA"]) / "Programs/Alshival.Ai"
        runtime = root / "resources/python/python.exe"
        cli = root / "resources/app/service/alshitasks-cli.py"
        if not runtime.is_file() or not cli.is_file():
            raise ValueError("Alshival installation not found; specify --app-directory.")
        return {"type": "stdio", "command": str(runtime.resolve()), "args": [str(cli.resolve()), *args]}
    if platform.startswith("linux"):
        command = shutil.which("alshitasks")
        if not command:
            raise ValueError("Install the Alshival .deb first; alshitasks must be on PATH.")
        return {"type": "stdio", "command": str(Path(command).absolute()), "args": args}
    raise ValueError("Alshival Local currently supports Windows and Linux; use the portal plugin on other platforms.")


def build(output, server):
    output = Path(output).expanduser().resolve()
    if output == SOURCE or SOURCE in output.parents:
        raise ValueError("Choose an output directory outside the plugin source.")
    # Never merge with or overwrite an installed plugin, marketplace, or user files.
    output.mkdir(parents=True, exist_ok=False)
    target = output / "plugins/alshival-local"
    target.mkdir(parents=True)
    for name in ("skills", "assets"):
        shutil.copytree(SOURCE / name, target / name)
    manifest = json.loads((SOURCE / "plugin.json").read_text(encoding="utf-8"))
    write_json(target / "plugin.json", manifest)
    config = {"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json", "mcpServers": {"alshival-local": server}}
    write_json(target / "mcp.json", config)
    write_json(target / ".mcp.json", config)
    compatibility = {key: manifest[key] for key in ("name", "version", "description", "author", "homepage", "license")}
    compatibility.update(skills="./skills/", mcpServers="./.mcp.json", interface=manifest["extensions"]["com.openai"]["interface"])
    compatibility["extensions"] = {"com.openai": {key: manifest["extensions"]["com.openai"][key] for key in ("onboardingSkill", "requires_local_executor")}}
    write_json(target / ".codex-plugin/plugin.json", compatibility)
    write_json(output / ".agents/plugins/marketplace.json", {
        "name": "alshival-local-device",
        "interface": {"displayName": "Alshival on this computer"},
        "plugins": [{"name": "alshival-local", "source": {"source": "local", "path": "./plugins/alshival-local"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]
    })
    archive = output / "alshival-local.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                bundle.write(path, Path("alshival-local") / path.relative_to(target))
    return archive


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-directory", help="Custom Windows Alshival installation directory")
    parser.add_argument("--read-only", action="store_true", help="Deny all MCP writes")
    parser.add_argument("--output", default=str(Path.home() / "Alshival-Local-Plugin"), help="New directory to create; existing directories are never overwritten")
    args = parser.parse_args()
    try:
        server = connection(sys.platform, args.app_directory, args.read_only)
        archive = build(args.output, server)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Setup failed: {error}\n")
    print(f"Created: {archive}")
    print("Access: " + ("read-only" if args.read_only else "local owner (read and write)"))
    print("Keep the desktop app running. Import the ZIP through your desktop host's local plugin flow.")
    print(f'Or register this Codex marketplace: codex plugin marketplace add "{archive.parent}"')
    print("Then install Alshival Local in the desktop plugin UI and run Setup.")
    print("This package contains device-specific paths. Regenerate it on each computer.")


if __name__ == "__main__":
    main()
