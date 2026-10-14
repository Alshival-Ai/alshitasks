import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from alshitasks import health, notifications
from alshitasks.api import API
from alshitasks.paths import Paths
from alshitasks.store import Store, Problem, uid
from alshitasks.security import grant_create


class MonitoringNotifications(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.s = Store(Paths(self.temp.name))
        self.api = API(self.s, None)
        with self.s.tx():
            self.resource = self.s.create(
                {"kind": "resource", "data": {"title": "Fixture service"}, "idempotency_key": uid()}
            )["id"]
            self.check = health.save(
                self.s,
                {"resource_id": self.resource, "name": "Availability", "config": {"kind": "heartbeat"}},
            )

    def tearDown(self):
        self.s.db.close()
        self.temp.cleanup()

    def sample(self, outcome, check=None):
        ident = (check or self.check)["id"]
        with self.s.tx():
            self.s.db.execute("UPDATE health_checks SET next_at=0 WHERE id=?", (ident,))
            row = next(r for r in health.claim(self.s) if r["id"] == ident)
            self.assertTrue(health.observe(self.s, row, outcome, "Fixture observation"))

    def feed(self, **args):
        return notifications.rows(self.s, args, True)

    def test_confirmation_incident_recovery_and_restart_no_replay(self):
        self.sample("failed")
        self.assertEqual(self.feed()["items"], [])
        self.sample("failed")
        self.assertEqual(self.feed()["unread_count"], 1)
        self.sample("failed")
        self.assertEqual(len(self.feed()["items"]), 1)
        claimed = notifications.pending(self.s)
        self.assertEqual(len(claimed), 1)
        self.s.db.close()
        self.s = Store(Paths(self.temp.name))
        self.assertEqual(notifications.pending(self.s), [])
        self.sample("healthy")
        self.assertEqual(len(self.feed()["items"]), 1)
        self.sample("healthy")
        self.assertEqual(len(self.feed()["items"]), 2)
        incident = self.s.db.execute("SELECT * FROM health_incidents").fetchone()
        self.assertIsNotNone(incident["closed_at"])

    def test_optional_checks_maintenance_and_expired_worker(self):
        with self.s.tx():
            optional = health.save(
                self.s,
                {
                    "resource_id": self.resource,
                    "name": "Optional",
                    "required": False,
                    "config": {"kind": "heartbeat"},
                },
            )
        self.sample("failed", optional)
        self.sample("failed", optional)
        self.assertEqual(self.feed()["items"], [])
        with self.s.tx():
            check = health.action(
                self.s, dict(id=self.check["id"], revision=1, action="maintenance", minutes=60)
            )
        self.sample("failed")
        self.sample("failed")
        self.assertEqual(self.feed()["items"], [])
        with self.s.tx():
            health.action(self.s, dict(id=check["id"], revision=check["revision"], action="end_maintenance"))
        self.sample("failed")
        self.assertEqual(len(self.feed()["items"]), 1)
        with self.s.tx():
            self.s.db.execute("UPDATE health_checks SET next_at=0")
            row = health.claim(self.s)[0]
            self.assertFalse(health.observe(self.s, row, "healthy", "late", clock=time.time() + 100))
            current = health.get_check(self.s, row["id"])
            health.action(self.s, dict(id=current["id"], revision=current["revision"], action="pause"))
            self.assertFalse(health.observe(self.s, row, "healthy", "stale"))

    def test_agent_read_contract_never_marks_read_and_grants_are_scoped(self):
        self.sample("missing")
        with self.s.tx():
            grant = grant_create(self.s, {"name": "Agent inbox", "scopes": ["notifications:read"]})
            restricted = grant_create(
                self.s, {"name": "Resource", "scopes": ["monitor:read"], "resource_id": self.resource}
            )
        result = self.api.dispatch("/v1/notifications", token=grant["token"])
        self.assertEqual(set(result), {"items", "next_page"})
        self.assertNotIn("source", result["items"][0])
        self.assertFalse(result["items"][0]["read"])
        self.assertEqual(self.feed()["unread_count"], 1)
        with self.assertRaises(Problem):
            self.api.dispatch(
                "/v1/notifications/actions", "POST", {"action": "read_all"}, token=grant["token"]
            )
        with self.assertRaises(Problem):
            self.api.dispatch("/v1/notifications", token=restricted["token"])
        with self.assertRaises(Problem):
            self.api.dispatch("/v1/notifications?limit=51", owner=True)
        notifications.mutate(self.s, {"action": "read", "id": result["items"][0]["id"]})
        self.assertEqual(self.feed()["unread_count"], 0)
        self.assertEqual(notifications.pending(self.s), [])
        notifications.mutate(self.s, {"action": "unread", "id": result["items"][0]["id"]})
        self.assertEqual(notifications.pending(self.s), [])
        notifications.mutate(self.s, {"action": "archive", "id": result["items"][0]["id"]})
        self.assertEqual(self.feed()["items"], [])
        self.assertEqual(len(self.feed(filter="archived")["items"]), 1)

    def portal_fixture(self):
        connection = uid()
        with self.s.tx():
            self.s.db.execute(
                "INSERT INTO connections VALUES(?,?,?,?,?,?,?)",
                (connection, "https://alshival.ai", "7", "", "connected", None, "{}"),
            )
            self.s.set_setting(
                "active_account", {"id": "7", "origin": "https://alshival.ai", "connection": connection}
            )
        self.portal_items = [
            dict(
                id=uid(),
                title="Portal alert",
                body="Details",
                event_type="resource_failed",
                workspace_id=None,
                destination="/account/notifications/",
                created_at="2026-10-11T00:00:00+00:00",
                read=False,
                archived=False,
            )
        ]

        def remote(origin, path, token, body=None):
            if body:
                return {"updated": 1}
            return {
                "items": [] if "filter=archived" in path else self.portal_items,
                "next_page": None,
                "workspaces": [],
            }

        return remote

    def test_portal_initial_suppression_new_alert_and_live_revocation(self):
        remote = self.portal_fixture()
        with (
            patch("alshitasks.portal.Portal.token", return_value="fixture"),
            patch("alshitasks.portal.remote", side_effect=remote),
        ):
            notifications.sync_portal(self.s, True)
            self.assertEqual(len(self.feed()["items"]), 1)
            self.assertEqual(notifications.pending(self.s), [])
            self.portal_items.append(dict(self.portal_items[0], id=uid(), title="New alert"))
            notifications.sync_portal(self.s, True)
            self.assertEqual(len(notifications.pending(self.s)), 1)
            self.portal_items = []
            notifications.sync_portal(self.s, True)
            result = self.api.dispatch("/v1/notifications?agent_read=1", owner=True)
            self.assertEqual(result["items"], [])
        self.sample("missing")
        with (
            patch("alshitasks.portal.Portal.token", return_value="fixture"),
            patch("alshitasks.portal.remote", side_effect=Problem("Offline", 503)),
        ):
            result = self.api.dispatch("/v1/notifications?agent_read=1", owner=True)
            self.assertEqual(len(result["items"]), 1)
            self.assertEqual(result["items"][0]["event_type"], "resource_unknown")

    def test_configuration_and_heartbeat_resource_binding(self):
        with self.s.tx():
            second = self.s.create(
                {"kind": "resource", "data": {"title": "Other"}, "idempotency_key": uid()}
            )["id"]
            grant = grant_create(
                self.s, {"name": "Heartbeat", "scopes": ["monitor:heartbeat"], "resource_id": self.resource}
            )
            self.api.dispatch(
                "/v1/heartbeat",
                "POST",
                {"resource_id": self.resource, "check_id": self.check["id"]},
                token=grant["token"],
            )
            with self.assertRaises(Problem):
                self.api.dispatch(
                    "/v1/heartbeat",
                    "POST",
                    {"resource_id": second, "check_id": self.check["id"]},
                    token=grant["token"],
                )
            with self.assertRaises(Problem):
                health.save(
                    self.s,
                    {
                        "resource_id": self.resource,
                        "name": "Invalid",
                        "config": {"kind": "tcp", "host": "-bad"},
                    },
                )
            self.assertGreater(health.get_check(self.s, self.check["id"])["heartbeat_at"], 0)

    def test_backup_preserves_inbox_but_disables_checks_and_old_toasts(self):
        from alshitasks import files
        from alshitasks.logs import Logs

        self.sample("missing")
        logs = Logs(self.s.paths)
        destination = Path(self.temp.name) / "fixture-backup.zip"
        files.backup(self.s, logs, str(destination))
        paths = self.s.paths
        self.s.db.close()
        logs.db.close()
        files.restore(paths, str(destination))
        self.s = Store(paths)
        self.assertEqual(len(self.feed()["items"]), 1)
        self.assertEqual(notifications.pending(self.s), [])
        self.assertFalse(health.get_check(self.s, self.check["id"])["enabled"])

    def test_old_monitor_migration_keeps_history_without_alert_replay(self):
        with self.s.tx():
            self.s.db.execute("DELETE FROM health_checks")
            self.s.db.execute('DELETE FROM settings WHERE key="named_monitor_migration"')
            from alshitasks.monitoring import configure, record

            configure(self.s, self.resource, {"kind": "heartbeat", "timeout": 240})
            record(self.s, self.resource, "failed", "fixture")
            record(self.s, self.resource, "failed", "fixture")
        self.s.db.close()
        self.s = Store(Paths(self.temp.name))
        self.assertEqual(health.get_check(self.s, self.resource)["name"], "Default check")
        self.assertEqual(len(health.snapshot(self.s)["history"]), 1)
        self.assertEqual(self.feed()["items"], [])

    def test_native_click_uses_notification_id_and_escapes_body(self):
        from unittest.mock import MagicMock
        from alshitasks.notifier import NativeNotifications

        native = NativeNotifications.__new__(NativeNotifications)
        native.GLib = MagicMock()
        native.Gio = MagicMock()
        native.bus = MagicMock()
        native.ids = {}
        native.bus.call_sync.return_value.unpack.return_value = (42,)
        ident = uid()
        native.show({"id": ident, "title": "Fixture alert", "body": "<b>Untrusted text</b>"})
        self.assertIn("&lt;b&gt;", native.GLib.Variant.call_args.args[1][4])
        signal = MagicMock()
        signal.unpack.return_value = (42, "default")
        with patch("alshitasks.notifier.subprocess.Popen") as launch:
            native.action(None, None, None, None, None, signal)
            self.assertEqual(
                launch.call_args.args[0], ["/usr/bin/alshitasks", "desktop", "--notification", ident]
            )

    def test_signout_and_other_account_never_expose_portal_cache(self):
        remote = self.portal_fixture()
        with (
            patch("alshitasks.portal.Portal.token", return_value="fixture"),
            patch("alshitasks.portal.remote", side_effect=remote),
        ):
            notifications.sync_portal(self.s, True)
        with self.s.tx():
            self.s.set_setting("active_account", None)
        self.assertEqual(self.feed()["items"], [])
        with self.s.tx():
            self.s.set_setting(
                "active_account", {"origin": "https://alshival.ai", "id": "8", "connection": uid()}
            )
        self.assertEqual(self.feed()["items"], [])


class IncrementalNotifications(MonitoringNotifications):
    def incremental_fixture(self):
        self.portal_fixture()
        with self.s.tx():
            self.s.db.execute("UPDATE connections SET capabilities=?", ('{"features":["notification-sync-v1"]}',))
        return self.portal_items[0]

    def page(self, items=(), cursor='checkpoint', complete=True, snapshot=False, removed=()):
        return dict(items=list(items), cursor=cursor, complete=complete, snapshot=snapshot,
                    removed=list(removed), reset=False, workspaces=[])

    def sync(self, response, force=False):
        with patch('alshitasks.portal.Portal.token', return_value='fixture'), patch('alshitasks.portal.remote', return_value=response):
            with self.s.tx():
                _, key = notifications.account(self.s)
                state = self.s.setting('notification_portal:' + key, {})
                state['next'] = 0
                self.s.set_setting('notification_portal:' + key, state)
            notifications.sync_portal(self.s, force)

    def test_snapshot_completion_delta_and_removal(self):
        item = self.incremental_fixture()
        self.sync(self.page([item], snapshot=True))
        self.assertEqual(len(self.feed()['items']), 1)
        self.assertEqual(notifications.pending(self.s), [])
        new = dict(item, id=uid(), title='New notification')
        self.sync(self.page([new], cursor='next'))
        self.assertEqual(len(self.feed()['items']), 2)
        self.assertEqual(len(notifications.pending(self.s)), 1)
        self.sync(self.page(removed=[item['id']]))
        self.assertEqual([r['title'] for r in self.feed()['items']], ['New notification'])

    def test_partial_snapshot_and_reset_preserve_existing_rows(self):
        item = self.incremental_fixture()
        self.sync(self.page([item], snapshot=True))
        self.sync({'reset': True})
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM app_notifications WHERE source='portal'").fetchone()[0], 1)
        self.sync(self.page([], snapshot=True, complete=False, cursor='partial'))
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM app_notifications WHERE source='portal'").fetchone()[0], 1)
        self.sync(self.page([], snapshot=True, complete=True))
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM app_notifications WHERE source='portal'").fetchone()[0], 0)

    def test_notice_during_fetch_is_not_lost(self):
        item = self.incremental_fixture()
        self.sync(self.page([item], snapshot=True))
        def during(*args):
            _, key = notifications.account(self.s)
            with self.s.tx():
                self.s.set_setting('notification_notice:' + key, 2)
            return self.page()
        with patch('alshitasks.portal.Portal.token', return_value='fixture'), patch('alshitasks.portal.remote', side_effect=during):
            notifications.sync_portal(self.s, True)
        _, key = notifications.account(self.s)
        self.assertEqual(self.s.setting('notification_portal:' + key)['next'], 0)
