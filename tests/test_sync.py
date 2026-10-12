import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from alshitasks.paths import Paths
from alshitasks.store import Store, uid, DEFAULT, Problem
from alshitasks.portal import sync_connection, local_id


class SyncAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.s = Store(Paths(self.temp.name))
        self.c = dict(
            id=uid(),
            origin="https://fixture.example",
            account="1",
            workspace=uid(),
            state="connected",
            cursor=None,
            capabilities="{}",
        )
        with self.s.tx():
            self.s.db.execute(
                "INSERT INTO connections VALUES(:id,:origin,:account,:workspace,:state,:cursor,:capabilities)",
                self.c,
            )
        self.board = {
            "id": uid(),
            "kind": "board",
            "revision": 1,
            "data": dict(DEFAULT, title="Shared board"),
        }
        self.record = {
            "id": uid(),
            "kind": "task",
            "revision": 1,
            "data": dict(DEFAULT, title="First", board_id=self.board["id"], visibility="shared"),
        }
        self.records = [self.board, self.record]
        self.receipts = {}

    def tearDown(self):
        self.s.db.close()
        self.temp.cleanup()

    def transport(self, origin, path, token, body=None):
        if body:
            args = body["arguments"]
            key = args["idempotency_key"]
            if key in self.receipts:
                return {"result": copy.deepcopy(self.receipts[key])}
            if body["tool"] == "project_create":
                record = {
                    "id": uid(),
                    "kind": args["kind"],
                    "revision": 1,
                    "data": dict(DEFAULT, **args["data"]),
                }
                self.records.append(record)
            else:
                record = next(r for r in self.records if r["id"] == args["id"])
                if record["revision"] != args["revision"]:
                    raise Problem("Conflict", 409)
                record["data"].update(args["data"])
                record["revision"] += 1
            self.receipts[key] = copy.deepcopy(record)
            return {"result": copy.deepcopy(record)}
        return {"items": copy.deepcopy(self.records), "complete": True, "next": None}

    def sync(self):
        sync_connection(self.s, self.c, self.transport, token="fixture")

    def update(self, record, data):
        with self.s.tx():
            return self.s.update(
                record["id"], {"data": data, "revision": record["revision"], "idempotency_key": uid()}
            )

    def test_portal_number_arrives_without_a_task_data_change(self):
        self.sync()
        ident = local_id(self.c["id"], self.record["id"])
        self.assertIsNone(self.s.item(ident)["number"])
        self.record["number"] = 17
        self.sync()
        self.assertEqual(self.s.item(ident)["number"], 17)
        edited = self.update(self.s.item(ident), {"title": "Offline edit"})
        self.assertEqual(edited["number"], 17)
        self.sync()
        self.assertEqual(self.s.item(ident)["number"], 17)
        self.assertNotIn("number", self.s.item(ident)["data"])

    def test_nonoverlap_merge_delete_and_stable_revision(self):
        self.sync()
        ident = local_id(self.c["id"], self.record["id"])
        a = self.s.item(ident)
        self.sync()
        self.assertEqual(self.s.item(ident)["revision"], a["revision"])
        a = self.update(a, {"title": "Local title"})
        self.record["data"]["body"] = "Remote body"
        self.record["revision"] = 2
        self.sync()
        self.assertEqual(self.record["data"]["title"], "Local title")
        self.assertEqual(self.record["data"]["body"], "Remote body")
        self.sync()
        a = self.s.item(ident)
        self.update(a, {"deleted": True})
        self.sync()
        self.assertTrue(self.record["data"]["deleted"])

    def test_overlapping_conflict_keeps_both_versions(self):
        self.sync()
        local = self.s.item(local_id(self.c["id"], self.record["id"]))
        self.update(local, {"title": "Local"})
        self.record["data"]["title"] = "Remote"
        self.record["revision"] = 2
        self.sync()
        conflict = self.s.db.execute("SELECT * FROM conflicts").fetchone()
        self.assertEqual(json.loads(conflict["local"])["data"]["title"], "Local")
        self.assertEqual(json.loads(conflict["remote"])["data"]["title"], "Remote")
        self.assertEqual(self.s.item(local["id"])["data"]["title"], "Local")

    def test_offline_create_and_replayed_receipt_uses_same_identity(self):
        self.sync()
        board_id = local_id(self.c["id"], self.board["id"])
        with self.s.tx():
            created = self.s.create(
                {
                    "kind": "task",
                    "data": {"title": "Offline new", "board_id": board_id},
                    "idempotency_key": uid(),
                }
            )
        self.sync()
        self.sync()
        row = self.s.db.execute("SELECT * FROM items WHERE id=?", (created["id"],)).fetchone()
        self.assertIsNotNone(row["remote_id"])
        self.assertEqual(len([r for r in self.records if r["data"]["title"] == "Offline new"]), 1)
        self.assertEqual(
            self.s.db.execute("SELECT COUNT(*) FROM items WHERE remote_id=?", (row["remote_id"],)).fetchone()[
                0
            ],
            1,
        )

    def test_sequential_local_edits_do_not_conflict_with_each_other(self):
        self.sync()
        local = self.s.item(local_id(self.c["id"], self.record["id"]))
        local = self.update(local, {"title": "One"})
        self.update(local, {"title": "Two"})
        self.sync()
        self.assertEqual(self.record["data"]["title"], "Two")
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0], 0)

    def test_create_response_lost_snapshot_retry_keeps_one_identity(self):
        self.sync()
        with self.s.tx():
            created = self.s.create(
                {
                    "kind": "task",
                    "data": {"title": "Lost reply", "board_id": local_id(self.c["id"], self.board["id"])},
                    "idempotency_key": uid(),
                }
            )
        lost = [False]

        def unreliable(origin, path, token, body=None):
            result = self.transport(origin, path, token, body)
            if body and not lost[0]:
                lost[0] = True
                raise Problem("Response lost", 503)
            return result

        with self.assertRaises(Problem):
            sync_connection(self.s, self.c, unreliable, token="fixture")
        self.sync()
        self.sync()
        remote = next(r for r in self.records if r["data"]["title"] == "Lost reply")
        rows = self.s.db.execute(
            "SELECT id FROM items WHERE connection=? AND remote_id=?", (self.c["id"], remote["id"])
        ).fetchall()
        self.assertEqual([r["id"] for r in rows], [created["id"]])

    def test_member_mapping_and_remote_edge_timestamp(self):
        from alshitasks.api import API
        from alshitasks.logs import Logs

        member = "3"
        edge_id = uid()

        def snapshot(origin, path, token, body=None):
            return {
                "items": copy.deepcopy(self.records),
                "members": [{"id": member, "name": "Samuel"}],
                "edges": [
                    {
                        "id": edge_id,
                        "source_id": self.board["id"],
                        "target_id": self.record["id"],
                        "kind": "related",
                        "revision": 1,
                        "created_at": "2026-10-10T00:00:00Z",
                    }
                ],
                "complete": True,
                "next": None,
            }

        self.record["data"]["assignee"] = member
        sync_connection(self.s, self.c, snapshot, token="fixture")
        local = self.s.item(local_id(self.c["id"], self.record["id"]))
        self.assertEqual(local["data"]["assignee"], "member:" + local_id(self.c["id"], member))
        logs = Logs(self.s.paths)
        try:
            api = API(self.s, logs)
            self.assertTrue(any(m["name"] == "Samuel" for m in api.members()))
            self.assertEqual(
                api.tool("project_edges", {}, owner=True)["edges"][0]["created_at"], "2026-10-10T00:00:00Z"
            )
        finally:
            logs.db.close()

    def test_workspace_views_share_rotating_credentials_and_disconnect(self):
        from unittest.mock import patch
        from alshitasks.portal import Portal
        from alshitasks.security import Secrets
        import time

        second = dict(self.c, id=uid(), workspace=uid())
        with self.s.tx():
            self.s.db.execute(
                "INSERT INTO connections VALUES(:id,:origin,:account,:workspace,:state,:cursor,:capabilities)",
                second,
            )
            self.s.set_setting("credential:" + second["id"], self.c["id"])
        values = {
            self.c["id"]: json.dumps(
                {
                    "access_token": "old",
                    "refresh_token": "refresh-fixture",
                    "client_id": "fixture",
                    "expires_at": time.time() - 1,
                }
            )
        }
        with (
            patch.object(Secrets, "get", side_effect=lambda key: values[key]),
            patch.object(Secrets, "put", side_effect=lambda key, value: values.update({key: value})),
            patch.object(Secrets, "delete", side_effect=lambda key: values.pop(key)),
            patch(
                "alshitasks.portal.remote",
                return_value={"access_token": "new", "refresh_token": "rotated", "expires_in": 3600},
            ) as transport,
        ):
            adapter = Portal(self.s)
            self.assertEqual(adapter.token(self.c), "new")
            self.assertEqual(adapter.token(second), "new")
            self.assertEqual(transport.call_count, 1)
            with self.s.tx():
                adapter.dispatch("/v1/portal/disconnect", "POST", {"connection": self.c["id"]}, {})
            self.assertIn(self.c["id"], values)
            with self.s.tx():
                adapter.dispatch("/v1/portal/disconnect", "POST", {"connection": second["id"]}, {})
            self.assertFalse(values)

    def test_offline_parent_identity_and_unshared_assignment_draft(self):
        self.sync()
        board = local_id(self.c["id"], self.board["id"])
        with self.s.tx():
            parent = self.s.create(
                {
                    "kind": "task",
                    "data": {"title": "Offline parent", "board_id": board},
                    "idempotency_key": uid(),
                }
            )
            child = self.s.create(
                {
                    "kind": "task",
                    "data": {"title": "Offline child", "parent_id": parent["id"], "board_id": board},
                    "idempotency_key": uid(),
                }
            )
        self.sync()
        self.sync()
        self.assertEqual(self.s.item(child["id"])["data"]["parent_id"], parent["id"])
        self.update(self.s.item(child["id"]), {"assignee": "local"})
        self.sync()
        self.assertTrue(
            self.s.db.execute(
                "SELECT 1 FROM outbox WHERE item_id=? AND state='draft'", (child["id"],)
            ).fetchone()
        )

    def test_native_callback_rejects_wrong_state_and_issuer_before_exchange(self):
        from unittest.mock import patch
        from urllib.parse import parse_qs, urlsplit, urlencode
        import urllib.request
        import urllib.error
        import hashlib
        import base64
        import time
        from alshitasks.portal import Portal
        from alshitasks.security import Secrets

        values = {}
        exchanges = []
        origin = "https://alshival.ai"

        def transport(origin, path, token=None, body=None, form=False):
            if path == "/oauth/register/":
                return {"client_id": "fixture-client"}
            if path == "/oauth/token/":
                exchanges.append(body)
                return {
                    "access_token": "fixture-access",
                    "refresh_token": "fixture-refresh",
                    "expires_in": 3600,
                }
            return {"resource": origin + "/desktop/v1", "account_id": "fixture-account"}

        with (
            patch.object(Secrets, "put", side_effect=lambda key, value: values.update({key: value})),
            patch.object(Secrets, "delete", side_effect=lambda key: values.pop(key, None)),
            patch("alshitasks.portal.remote", side_effect=transport),
        ):
            with self.s.tx():
                connection = Portal(self.s).connect(origin)
            query = parse_qs(urlsplit(connection["url"]).query)
            callback = query["redirect_uri"][0]
            valid = {"state": query["state"][0], "iss": origin, "code": "fixture-code"}
            for field, wrong in (("state", "wrong"), ("iss", "https://wrong.example")):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(
                        callback + "?" + urlencode(dict(valid, **{field: wrong})), timeout=3
                    )
                self.assertEqual(raised.exception.code, 400)
            self.assertFalse(exchanges)
            with urllib.request.urlopen(callback + "?" + urlencode(valid), timeout=3) as response:
                self.assertEqual(response.status, 200)
            verifier = exchanges[0]["code_verifier"]
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            )
            self.assertEqual(challenge, query["code_challenge"][0])
            self.assertEqual(exchanges[0]["resource"], origin + "/desktop/v1")
            self.assertEqual(Portal(self.s).connection(connection["connection"])["state"], "connected")
            time.sleep(0.6)  # Let the short-lived loopback listener close before fixture cleanup.

    def test_edits_after_disconnect_are_visible_drafts(self):
        self.sync()
        with self.s.tx():
            self.s.db.execute("UPDATE connections SET state='disconnected' WHERE id=?", (self.c["id"],))
        local = self.s.item(local_id(self.c["id"], self.record["id"]))
        self.update(local, {"title": "Retained after disconnect"})
        job = self.s.db.execute("SELECT state,reason FROM outbox WHERE item_id=?", (local["id"],)).fetchone()
        self.assertEqual(job["state"], "draft")
        self.assertIn("disconnected", job["reason"])
