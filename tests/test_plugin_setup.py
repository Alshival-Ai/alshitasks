import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SOURCE = Path(__file__).resolve().parents[1] / "plugins/alshival-local/configure.py"
spec = importlib.util.spec_from_file_location("plugin_setup", SOURCE)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class PluginSetup(unittest.TestCase):
    def test_windows_paths_with_spaces_and_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / "Custom App"
            for name in ("resources/python/python.exe", "resources/app/service/alshitasks-cli.py"):
                path = app / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            server = setup.connection("win32", app, True)
            self.assertEqual(server["command"], str((app / "resources/python/python.exe").resolve()))
            self.assertEqual(server["args"][1:], ["mcp", "--read-only"])
            self.assertTrue(Path(server["args"][0]).is_file())

    def test_linux_preserves_executable_launcher_and_access(self):
        with patch.object(setup.shutil, "which", return_value="/usr/bin/alshitasks"):
            server = setup.connection("linux")
        self.assertEqual(server["args"], ["mcp"])
        self.assertEqual(Path(server["command"]).name, "alshitasks")

    def test_missing_app_and_unsupported_platform_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                setup.connection("win32", folder)
        with patch.object(setup.shutil, "which", return_value=None):
            with self.assertRaises(ValueError):
                setup.connection("linux")
        with self.assertRaises(ValueError):
            setup.connection("darwin")

    def test_archive_marketplace_and_compatibility(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "generated"
            server = {"type": "stdio", "command": "alshitasks", "args": ["mcp", "--read-only"]}
            archive = setup.build(output, server)
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                self.assertTrue(all(name.startswith("alshival-local/") for name in names))
                self.assertFalse(any(".app.json" in name or "configure.py" in name or "__pycache__" in name for name in names))
                config = json.loads(bundle.read("alshival-local/mcp.json"))
                self.assertEqual(config["mcpServers"]["alshival-local"], server)
                self.assertEqual(bundle.read("alshival-local/mcp.json"), bundle.read("alshival-local/.mcp.json"))
                manifest = json.loads(bundle.read("alshival-local/plugin.json"))
                compat = json.loads(bundle.read("alshival-local/.codex-plugin/plugin.json"))
                self.assertEqual(manifest["version"], compat["version"])
                self.assertEqual(manifest["extensions"]["com.openai"]["interface"], compat["interface"])
                for path in ("skills/setup/SKILL.md", "assets/icon.png"):
                    self.assertIn("alshival-local/" + path, names)
            marketplace = json.loads((output / ".agents/plugins/marketplace.json").read_text())
            self.assertTrue((output / marketplace["plugins"][0]["source"]["path"] / "plugin.json").is_file())
            with self.assertRaises(FileExistsError):
                setup.build(output, server)
            self.assertTrue(archive.is_file())

    def test_source_cannot_be_overwritten(self):
        with self.assertRaises(ValueError):
            setup.build(setup.SOURCE, {})
        with self.assertRaises(ValueError):
            setup.build(setup.SOURCE / "generated", {})


if __name__ == "__main__":
    unittest.main()
