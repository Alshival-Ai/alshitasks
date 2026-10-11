"""Allocate a monotonic release version without committing generated version bumps."""
import json
import os
from pathlib import Path
import re
import sys


def select(requested, releases, sha):
    def version(value):
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
            raise ValueError("Expected stable semantic version: " + value)
        return tuple(map(int, value.split(".")))
    wanted = version(requested)
    stable = [r for r in releases if not r.get("draft") and not r.get("prerelease")]
    if any(r.get("target_commitish") == sha for r in stable):
        return None
    used = [version(r["tag_name"].removeprefix("v")) for r in releases
            if re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+", r["tag_name"])]
    highest = max(used, default=(-1, -1, -1))
    chosen = wanted if wanted > highest else (highest[0], highest[1], highest[2] + 1)
    return ".".join(map(str, chosen))


def main():
    releases = [r for page in json.loads(Path(sys.argv[1]).read_text()) for r in page]
    package = json.loads(Path("package.json").read_text())
    chosen = select(package["version"], releases, os.environ["GITHUB_SHA"])
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write("publish=" + str(chosen is not None).lower() + "\n")
        if chosen is None:
            return
        output.write("value=" + chosen + "\n")
    for name in ("package.json", "package-lock.json"):
        path = Path(name)
        data = json.loads(path.read_text())
        data["version"] = chosen
        if "packages" in data:
            data["packages"][""]["version"] = chosen
        path.write_text(json.dumps(data, indent=2) + "\n")
    print("Release version: " + chosen)


if __name__ == "__main__":
    main()
