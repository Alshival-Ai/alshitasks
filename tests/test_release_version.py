import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("release_version", Path(__file__).resolve().parents[1] / "scripts/release-version.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseVersionTests(unittest.TestCase):
    def test_existing_version_increments_and_rerun_skips(self):
        releases = [{"tag_name": "v0.1.15", "target_commitish": "old"}]
        self.assertEqual(module.select("0.1.15", releases, "new"), "0.1.16")
        self.assertIsNone(module.select("0.1.15", releases, "old"))

    def test_manual_bump_and_out_of_order_versions(self):
        releases = [{"tag_name": "v0.1.18"}, {"tag_name": "v0.1.15"}]
        self.assertEqual(module.select("0.1.15", releases, "new"), "0.1.19")
        self.assertEqual(module.select("0.2.0", releases, "new"), "0.2.0")

    def test_draft_reserves_version_but_does_not_skip_commit(self):
        self.assertEqual(module.select("0.1.15", [{"tag_name": "v0.1.15", "draft": True, "target_commitish": "new"}], "new"), "0.1.16")
        self.assertEqual(module.select("0.1.15", [], "new"), "0.1.15")
