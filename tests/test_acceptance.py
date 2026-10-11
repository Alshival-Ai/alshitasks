import base64
from datetime import datetime, timezone, timedelta
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from alshitasks.paths import Paths
from alshitasks.store import Store, Problem, uid, DEFAULT
from alshitasks.logs import Logs
from alshitasks.api import API
from alshitasks.security import Secrets, grant_create, authorize
from alshitasks import files, monitoring, portal, ssh


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = Paths(self.temp.name)
        self.store = Store(self.paths)
        self.logs = Logs(self.paths)
        self.api = API(self.store, self.logs)

    def tearDown(self):
        self.store.db.close()
        self.logs.db.close()
        self.temp.cleanup()

    def create(self, kind="task", **data):
        with self.store.tx():
            return self.store.create(
                {"kind": kind, "data": {"title": "Test", **data}, "idempotency_key": uid()}
            )

    def update(self, item, **data):
        with self.store.tx():
            return self.store.update(
                item["id"], {"revision": item["revision"], "data": data, "idempotency_key": uid()}
            )

    def test_offline_first_launch_and_restart(self):
        self.assertEqual(self.store.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        x = self.create()
        self.store.db.close()
        self.store = Store(self.paths)
        self.assertEqual(self.store.item(x["id"])["data"]["title"], "Test")
        self.assertEqual(self.paths.runtime.stat().st_mode & 0o777, 0o700)

    def test_idempotent_retry_and_mismatched_payload(self):
        b = {"kind": "task", "data": {"title": "A"}, "idempotency_key": uid()}
        with self.store.tx():
            x = self.store.create(b)
            self.assertEqual(x, self.store.create(b))
        with self.assertRaises(Problem), self.store.tx():
            self.store.create(dict(b, data={"title": "B"}))

    def test_revision_conflict_and_rollback(self):
        x = self.create()
        self.update(x, title="Other")
        with self.assertRaises(Problem):
            self.update(x, title="Old")
        self.assertEqual(self.store.item(x["id"])["data"]["title"], "Other")

    def test_boards_isolated_and_dependency_cycles(self):
        b = self.create("board")
        a = self.create(board_id=b["id"])
        c = self.create(board_id=b["id"])
        other = self.create()
        with self.store.tx():
            self.store.edge_create(
                {"source_id": a["id"], "target_id": c["id"], "kind": "depends_on", "idempotency_key": uid()}
            )
        for source, target in [(c, a), (a, other)]:
            with self.assertRaises(Problem), self.store.tx():
                self.store.edge_create(
                    {
                        "source_id": source["id"],
                        "target_id": target["id"],
                        "kind": "depends_on",
                        "idempotency_key": uid(),
                    }
                )

    def test_tombstone_restore_keeps_history(self):
        x = self.create()
        with self.store.tx():
            d = self.store.action(
                x["id"], {"action": "trash", "revision": x["revision"], "idempotency_key": uid()}
            )
        self.assertTrue(d["data"]["deleted"])
        with self.store.tx():
            r = self.store.action(
                d["id"], {"action": "restore", "revision": d["revision"], "idempotency_key": uid()}
            )
        self.assertFalse(r["data"]["deleted"])
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM events WHERE item_id=?", (x["id"],)).fetchone()[0], 3
        )

    def test_backup_restore_data_and_attachments(self):
        x = self.create()
        with self.store.tx():
            a = files.attach(self.store, x["id"], "space name.txt", base64.b64encode(b"hello").decode())
        backup = self.paths.data / "export.zip"
        files.backup(self.store, self.logs, backup)
        self.update(x, title="New")
        self.store.db.close()
        self.logs.db.close()
        result = files.restore(self.paths, backup)
        self.store = Store(self.paths)
        self.logs = Logs(self.paths)
        self.assertEqual(self.store.item(x["id"])["data"]["title"], "Test")
        self.assertEqual((self.paths.attachments / a["id"]).read_bytes(), b"hello")
        self.assertTrue(Path(result["recovery"]).is_dir())

    def test_corrupt_backup_does_not_replace_state(self):
        import zipfile

        x = self.create()
        p = self.paths.data / "bad.zip"
        files.backup(self.store, self.logs, p)
        with zipfile.ZipFile(p, "a") as z:
            z.writestr("../escape", b"bad")
        with self.assertRaises(Problem):
            files.restore(self.paths, p)
        self.assertEqual(self.store.item(x["id"])["data"]["title"], "Test")

    def test_future_schema_refuses_downgrade(self):
        with self.store.db:
            self.store.db.execute("PRAGMA user_version=999")
        with self.assertRaises(Problem):
            Store(self.paths)

    def test_scoped_grant_and_revoke(self):
        r = self.create("resource")
        other = self.create("resource")
        with self.store.tx():
            g = grant_create(self.store, {"scopes": ["logs:write"], "resource_id": r["id"]})
        self.assertEqual(authorize(self.store, g["token"], "logs:write", r["id"])["resource_id"], r["id"])
        for scope, resource in [("project:write", r["id"]), ("logs:write", other["id"])]:
            with self.assertRaises(Problem):
                authorize(self.store, g["token"], scope, resource)
        with self.store.tx():
            self.store.db.execute("UPDATE grants SET revoked_at=?", ("revoked",))
        with self.assertRaises(Problem):
            authorize(self.store, g["token"], "logs:write", r["id"])

    def test_secret_service_no_plaintext_fallback(self):
        with patch("shutil.which", return_value=None), self.assertRaises(Problem):
            Secrets().put("test", "never-on-disk")
        self.assertFalse(any(p.name == "credentials" for p in self.paths.data.rglob("*")))

    def event(self, ident="one", **kw):
        return {
            "id": ident,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": "info",
            "message": "Hello",
            "attributes": {},
            **kw,
        }

    def test_logs_dedupe_filter_and_batch_atomic_validation(self):
        self.assertEqual(self.logs.ingest("r", [self.event()])["accepted"], 1)
        self.assertEqual(self.logs.ingest("r", [self.event()])["duplicates"], 1)
        self.assertEqual(len(self.logs.query({"text": "Hello"})["events"]), 1)
        with self.assertRaises(Problem):
            self.logs.ingest("r", [self.event("two"), self.event("bad", severity="invalid")])
        self.assertEqual(len(self.logs.query({})["events"]), 1)

    def test_log_retention(self):
        self.logs.ingest(
            "r",
            [
                self.event("old", timestamp=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat()),
                self.event("new"),
            ],
        )
        self.logs.retain()
        self.assertEqual([x["id"] for x in self.logs.query({})["events"]], ["new"])

    def test_monitor_two_results_and_missing_collector(self):
        r = self.create("resource")
        with self.store.tx():
            monitoring.configure(self.store, r["id"], {"kind": "heartbeat"})
            monitoring.record(self.store, r["id"], "missing", "No collector")
        self.assertEqual(self.store.db.execute("SELECT health FROM monitors").fetchone()[0], "unknown")
        with self.store.tx():
            monitoring.record(self.store, r["id"], "missing", "No collector")
        self.assertEqual(self.store.db.execute("SELECT health FROM monitors").fetchone()[0], "missing")
        with self.store.tx():
            monitoring.record(self.store, r["id"], "healthy", "OK")
            monitoring.record(self.store, r["id"], "healthy", "OK")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], 2)

    def test_forwarding_queue_bounded_and_outage_retry(self):
        r = self.create("resource")
        with self.store.tx():
            self.store.db.execute(
                "INSERT INTO forwarding VALUES(?,?,?,?,1)",
                ("target", "https://example.com/logs", r["id"], "key"),
            )
            monitoring.queue_forward(self.store, r["id"], [self.event()] * 2)
            with patch.object(Secrets, "get", side_effect=Problem("Locked")):
                monitoring.forward_once(self.store)
        row = self.store.db.execute("SELECT * FROM forward_queue").fetchone()
        self.assertEqual(row["attempts"], 1)
        self.assertLess(row["bytes"], 16 * 1024**2)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM forward_queue").fetchone()[0], 1)

    def test_three_way_merge_and_conflict(self):
        self.assertEqual(portal.merge({"a": 1, "b": 1}, {"a": 2, "b": 1}, {"a": 1, "b": 2}), {"a": 2, "b": 2})
        self.assertIsNone(portal.merge({"a": 1}, {"a": 2}, {"a": 3}))

    def connection(self):
        c = {
            "id": uid(),
            "origin": "https://portal.example",
            "account": "one",
            "workspace": uid(),
            "state": "connected",
            "cursor": None,
            "capabilities": "{}",
        }
        with self.store.tx():
            self.store.db.execute(
                "INSERT INTO connections VALUES(:id,:origin,:account,:workspace,:state,:cursor,:capabilities)",
                c,
            )
        return c

    def record(self, ident=None, kind="task", **data):
        return {
            "id": ident or uid(),
            "kind": kind,
            "revision": 1,
            "data": dict(DEFAULT, **{"title": "Shared", **data}),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def test_snapshot_interruption_and_missing_records_no_delete(self):
        c = self.connection()
        record = self.record()
        calls = [0]

        def interrupted(*a, **kw):
            calls[0] += 1
            if calls[0] > 1:
                raise Problem("offline", 503)
            return {"items": [record], "next": "page2", "complete": False}

        with self.assertRaises(Problem):
            portal.sync_connection(self.store, c, interrupted, token="fixture")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)
        portal.sync_connection(
            self.store,
            c,
            lambda *a, **kw: {"items": [record], "next": None, "complete": True},
            token="fixture",
        )
        portal.sync_connection(
            self.store, c, lambda *a, **kw: {"items": [], "next": None, "complete": True}, token="fixture"
        )
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)

    def test_account_cache_identity_isolation(self):
        a = self.connection()
        b = self.connection()
        r = self.record()
        for c in (a, b):
            portal.sync_connection(
                self.store,
                c,
                lambda *a, **kw: {"items": [r], "next": None, "complete": True},
                token="fixture",
            )
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM items").fetchone()[0], 2)

    def test_offline_edit_merge_permission_removed_and_conflict(self):
        c = self.connection()
        r = self.record()
        shared = [r]
        sent = []

        def transport(origin, path, token, body=None, **kw):
            if body:
                sent.append(body)
                raise Problem("Forbidden", 403)
            return {"items": shared, "next": None, "complete": True}

        portal.sync_connection(self.store, c, transport, token="fixture")
        local = self.store.item(portal.local_id(c["id"], r["id"]))
        self.update(local, title="Offline edit")
        portal.sync_connection(self.store, c, transport, token="fixture")
        self.assertEqual(self.store.db.execute("SELECT state FROM outbox").fetchone()[0], "draft")
        self.assertEqual(self.store.item(local["id"])["data"]["title"], "Offline edit")

    def test_standalone_boards_not_enqueued(self):
        self.connection()
        b = self.create("board")
        self.create(board_id=b["id"])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 0)

    def test_ssh_collision_preserves_existing_config(self):
        r = self.create("resource")
        home = Path(self.temp.name) / "home"
        (home / ".ssh").mkdir(parents=True)
        config = home / ".ssh/config"
        config.write_text("Host alt-used\n  HostName example.com\n")
        with patch(
            "subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="hostname example.com\n")
        ):
            with self.assertRaises(Problem), self.store.tx():
                ssh.configure(
                    self.store,
                    r["id"],
                    {"alias": "alt-used", "host": "example.org", "username": "me", "agent": True},
                    home,
                )
        self.assertEqual(config.read_text(), "Host alt-used\n  HostName example.com\n")

    def test_mcp_schema_rejects_unexpected_fields(self):
        from alshitasks.contract import validate

        with self.assertRaises(Problem):
            validate(
                "project_create", {"kind": "task", "data": {}, "idempotency_key": uid(), "execute": "bad"}
            )

    def test_log_quota_accounts_for_sqlite_pages(self):
        for batch in range(8):
            self.logs.ingest("r", [self.event(str(batch * 50 + n), message="x" * 12000) for n in range(50)])
        self.logs.retain(max_bytes=1024 * 1024)
        self.assertLessEqual(self.logs.path.stat().st_size, 1024 * 1024)

    def test_ssh_key_path_spaces_and_revoke_preserves_unrelated(self):
        import subprocess

        resource = self.create("resource")
        home = self.paths.home
        directory = home / ".ssh"
        directory.mkdir(parents=True)
        config = directory / "config"
        original = "Host unrelated\n  HostName untouched.example\n"
        config.write_text(original)
        key = home / "key with spaces"
        key.write_text("fixture key content")
        with self.store.tx():
            ssh.configure(
                self.store,
                resource["id"],
                {"alias": "alt-fixture", "host": "example.org", "username": "me", "identity_file": str(key)},
                home,
            )
        resolved = subprocess.run(
            ["ssh", "-F", str(config), "-G", "alt-fixture"], text=True, capture_output=True, check=True
        ).stdout
        self.assertIn("hostname example.org", resolved)
        self.assertIn("identityfile " + str(key), resolved)
        self.assertIn("stricthostkeychecking ask", resolved)
        with self.store.tx():
            ssh.revoke(self.store, resource["id"])
        self.assertTrue(config.read_text().endswith(original))
        self.assertNotIn("Host alt-fixture", (directory / "alshitasks.conf").read_text())

    def test_initial_migration_failure_rolls_back_schema(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as d:
            paths = Paths(d)
            with patch.object(Store, "status_save", side_effect=Problem("Injected initialization failure")):
                with self.assertRaises(Problem):
                    Store(paths)
            db = sqlite3.connect(paths.data / "projects.sqlite")
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0], 0
            )
            db.close()
            recovered = Store(paths)
            self.assertEqual(len(recovered.statuses()), 5)
            recovered.db.close()


if __name__ == "__main__":
    unittest.main()
