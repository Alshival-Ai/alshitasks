from pathlib import Path

root = Path(__file__).resolve().parents[1]
parts = [
    "# Dependency license notices\n\nGenerated from pinned dependencies in package-lock.json. Public Sans OFL notice remains beside the font.\n"
]
for p in sorted((root / "node_modules").rglob("*")):
    if (
        p.is_file()
        and p.name.lower() in ("license", "license.md", "license.txt", "copying")
        and "electron/dist" not in str(p)
    ):
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        parts.append("\n\n## " + str(p.relative_to(root / "node_modules")) + "\n\n" + text)
(root / "docs/DEPENDENCY_NOTICES.md").write_text("".join(parts))
