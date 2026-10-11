import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from alshitasks.api import API
from alshitasks.logs import Logs
from alshitasks.paths import Paths
from alshitasks.security import grant_create
from alshitasks.store import Store, Problem, uid
from alshitasks.workspaces import LOCAL


class LocalLogs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Paths(self.temp.name))
        self.logs = Logs(self.store.paths)
        self.api = API(self.store, self.logs)
        with self.store.tx():
            self.resources = [
                self.store.create({"kind": "resource", "data": {"title": "Local"}, "idempotency_key": uid()})[
                    "id"
                ]
                for _ in range(2)
            ]
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def tearDown(self):
        self.logs.db.close()
        self.store.db.close()
        self.temp.cleanup()

    def ingest(self, resource, ident):
        with self.store.tx():
            return self.api.dispatch(
                "/v1/logs",
                "POST",
                {
                    "resource_id": resource,
                    "events": [
                        {
                            "id": ident,
                            "timestamp": self.timestamp,
                            "severity": "info",
                            "message": "worker started",
                        }
                    ],
                },
                owner=True,
            )

    def test_concurrent_ingestion_pagination_and_restart(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: self.ingest(self.resources[i % 2], str(i)), range(40)))
        query = {"limit": 7, "text": "worker", "severity": "info"}
        ids = []
        while True:
            result = self.logs.query(query)
            ids.extend((e["resource"], e["id"]) for e in result["events"])
            if not result["next_cursor"]:
                break
            query["cursor"] = result["next_cursor"]
        self.assertEqual(len(ids), 40)
        self.assertEqual(len(set(ids)), 40)
        self.logs.db.close()
        self.logs = Logs(self.store.paths)
        self.assertEqual(len(self.logs.query({})["events"]), 40)

    def test_cursor_and_filter_validation(self):
        for i in range(3):
            self.ingest(self.resources[0], str(i))
        cursor = self.logs.query({"limit": 1})["next_cursor"]
        for q in (
            {"cursor": cursor, "severity": "error"},
            {"cursor": "bad"},
            {"since": "2026-01-01"},
            {"severity": "oops"},
            {"limit": 101},
            {"unknown": "x"},
        ):
            with self.subTest(q=q), self.assertRaises(Problem):
                self.logs.query(q)

    def test_workspace_filter_and_resource_grant_cannot_be_bypassed(self):
        for resource in self.resources:
            self.ingest(resource, "same-event")
        with self.store.tx():
            self.store.db.execute("UPDATE items SET connection=? WHERE id=?", (uid(), self.resources[1]))
            grant = grant_create(self.store, {"resource_id": self.resources[0], "scopes": ["logs:read"]})
            result = self.api.dispatch("/v1/logs?workspace_id=" + LOCAL, owner=True)
            self.assertEqual([e["resource"] for e in result["events"]], [self.resources[0]])
            with self.assertRaises(Problem):
                self.api.dispatch(
                    "/v1/logs?workspace_id=" + LOCAL + "&resource_id=" + self.resources[1], owner=True
                )
            with self.assertRaises(Problem):
                self.api.dispatch("/v1/logs?resource_id=" + self.resources[1], token=grant["token"])
            with self.assertRaises(Problem):
                self.api.dispatch("/v1/logs/setup?resource_id=" + self.resources[0], token=grant["token"])
            self.store.db.execute("UPDATE grants SET revoked_at='now' WHERE id=?", (grant["id"],))
            with self.assertRaises(Problem):
                self.api.dispatch("/v1/logs?resource_id=" + self.resources[0], token=grant["token"])

    def test_no_cloud_calls_and_disk_failure_never_acknowledged(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("Must stay offline")):
            self.assertEqual(self.ingest(self.resources[0], "offline")["accepted"], 1)
        self.logs.db.execute("PRAGMA query_only=ON")
        with self.assertRaises(sqlite3.OperationalError):
            self.ingest(self.resources[0], "not-written")
        self.assertEqual([r["id"] for r in self.logs.query({})["events"]], ["offline"])

    def test_resource_setup_has_no_secret_and_no_connection_requirement(self):
        result = self.api.dispatch("/v1/logs/setup?resource_id=" + self.resources[0], owner=True)
        self.assertFalse(result["connected"])
        self.assertIsNone(result["http_port"])
        self.assertNotIn("token", json.dumps(result))
