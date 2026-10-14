import base64
import gzip
import hashlib
import http.client
import http.server
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from alshitasks.api import API
from alshitasks.logs import Logs
from alshitasks.paths import Paths
from alshitasks.store import Store, uid
from alshitasks.security import grant_create
from alshitasks.server import Handler
from alshitasks.workspaces import LOCAL
from alshitasks import monitoring
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest


class CloudLogs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = Paths(self.temp.name)
        self.s = Store(self.paths)
        self.logs = Logs(self.paths)
        self.api = API(self.s, self.logs)
        with self.s.tx():
            self.resource = self.s.create(
                {"kind": "resource", "data": {"title": "app"}, "idempotency_key": uid()}
            )["id"]
            self.grant = grant_create(
                self.s, {"resource_id": self.resource, "scopes": ["logs:write", "logs:read"]}
            )
        self.path = f"/v1/workspaces/{LOCAL}/resources/{self.resource}/logs"
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.api = self.api
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.logs.db.close()
        self.s.db.close()
        self.temp.cleanup()

    def request(self, method="POST", body=None, path=None, headers=None, chunked=False):
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        h = {
            "Authorization": "Bearer " + self.grant["token"],
            "Content-Type": "application/json",
            **(headers or {}),
        }
        raw = json.dumps(body).encode() if isinstance(body, dict) else body
        c.request(
            method,
            path or self.path,
            (iter([raw[:4], raw[4:]]) if chunked else raw),
            h,
            encode_chunked=chunked,
        )
        r = c.getresponse()
        data = r.read()
        result = (
            r.status,
            json.loads(data) if r.getheader("Content-Type") == "application/json" else data,
            dict(r.getheaders()),
        )
        c.close()
        return result

    def test_actual_seven_column_migration_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = Paths(directory)
            old = sqlite3.connect(paths.data / "logs.sqlite")
            old.execute(
                "CREATE TABLE logs(id TEXT,resource TEXT,timestamp TEXT,severity TEXT,message TEXT,attributes TEXT,bytes INTEGER,PRIMARY KEY(id,resource))"
            )
            old.execute(
                "INSERT INTO logs VALUES(?,?,?,?,?,?,?)",
                ("old", self.resource, "2026-10-12T00:00:00+00:00", "info", "preserved", "{}", 99),
            )
            old.commit()
            old.close()
            logs = Logs(paths)
            self.assertEqual(logs.query({})["events"][0]["message"], "preserved")
            backup = sqlite3.connect(paths.data / "logs.pre-cloud-v1.sqlite")
            self.assertEqual(len(backup.execute("PRAGMA table_info(logs)").fetchall()), 7)
            self.assertEqual(backup.execute("SELECT message FROM logs").fetchone()[0], "preserved")
            backup.close()
            logs.db.close()

    def test_storage_failure_and_concurrent_acknowledgments(self):
        from concurrent.futures import ThreadPoolExecutor

        self.logs.db.execute("PRAGMA query_only=ON")
        status, payload, headers = self.request(body={"events": [{"message": "failed"}]})
        self.assertEqual(status, 503)
        self.assertEqual(headers["Retry-After"], "5")
        self.logs.db.execute("PRAGMA query_only=OFF")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(lambda i: self.request(body={"events": [{"message": str(i)}]})[0], range(12))
            )
        self.assertEqual(results, [200] * 12)
        self.assertEqual(len(self.request("GET")[1]["items"]), 12)

    def test_forwarding_sends_and_removes_acknowledged_batch(self):
        from unittest.mock import MagicMock

        target = uid()
        with self.s.tx():
            self.s.db.execute(
                "INSERT INTO forwarding(id,resource_id,destination,credential,enabled) VALUES(?,?,?,?,1)",
                (target, self.resource, "https://api.alshival.ai" + self.path, "fixture"),
            )
            monitoring.queue_forward(
                self.s, self.resource, [{"id": uid(), "message": "sent", "severity": "info"}]
            )
        secrets = MagicMock()
        secrets.get.return_value = "fixture-token"
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = response
            monitoring.forward_once(self.s, secrets)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(json.loads(request.data)["events"][0]["severity"], "INFO")
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM forward_queue").fetchone()[0], 0)
        self.assertEqual(self.s.setting("forward_status:" + target)["state"], "connected")

    def test_json_gzip_chunked_fields_queries_and_cursor(self):
        events = [
            {
                "message": "Straße WORKER",
                "severity": "TRACE",
                "source": "test",
                "trace_id": "AB" * 16,
                "span_id": "CD" * 8,
            },
            {"message": "next", "severity": "fatal"},
        ]
        status, d, h = self.request(
            body=gzip.compress(json.dumps({"events": events}).encode()),
            headers={"Content-Encoding": "gzip"},
            chunked=True,
        )
        self.assertEqual(status, 200)
        self.assertEqual(d["accepted"], 2)
        self.assertEqual(d["request_id"], h["X-Request-ID"])
        status, d, _ = self.request("GET", path=self.path + "?q=worker&trace_id=" + "ab" * 16)
        self.assertEqual(status, 200)
        self.assertEqual(d["items"][0]["source"], "test")
        self.assertEqual(d["items"][0]["severity"], "TRACE")
        self.assertEqual(d["items"][0]["span_id"], "cd" * 8)
        _, d, _ = self.request("GET", path=self.path + "?limit=1")
        cursor = d["next_cursor"]
        first = d["items"][0]["id"]
        self.assertTrue(cursor)
        self.request(body={"events": [{"message": "new event"}]})
        _, d, _ = self.request("GET", path=self.path + "?limit=1&cursor=" + cursor)
        self.assertEqual(len(d["items"]), 1)
        self.assertNotEqual(d["items"][0]["id"], first)
        self.assertNotEqual(d["items"][0]["message"], "new event")
        self.assertEqual(self.request("GET", path=self.path + "?severity=INFO&cursor=" + cursor)[0], 400)
        self.assertEqual(self.request("GET", path=self.path + "?limit=201")[0], 400)

    def test_otlp_json_and_protobuf(self):
        proto = ExportLogsServiceRequest()
        r = proto.resource_logs.add()
        r.resource.attributes.add(key="service.name").value.string_value = "worker"
        log = r.scope_logs.add().log_records.add()
        log.body.string_value = "otel"
        log.severity_number = 17
        log.trace_id = b"\xab" * 16
        path = self.path.removesuffix("logs") + "otlp/v1/logs"
        self.assertEqual(
            self.request(
                body=proto.SerializeToString(), path=path, headers={"Content-Type": "application/x-protobuf"}
            )[:2],
            (200, b""),
        )
        data = {
            "resourceLogs": [
                {
                    "scopeLogs": [
                        {
                            "logRecords": [
                                {"body": {"stringValue": "json"}, "traceId": "ab" * 16, "severityNumber": 1}
                            ]
                        }
                    ]
                }
            ]
        }
        self.assertEqual(self.request(body=data, path=path)[:2], (200, {}))
        _, d, _ = self.request("GET")
        self.assertEqual(len(d["items"]), 2)
        self.assertEqual({e["severity"] for e in d["items"]}, {"ERROR", "TRACE"})

    def test_errors_auth_bounds_and_revocation(self):
        cases = [
            ({"events": []}, 400, "invalid_events"),
            ({"events": [{"id": "unexpected", "message": "hi"}]}, 400, "invalid_events"),
            ({"events": [{"message": "a" * 65537}]}, 400, "invalid_events"),
            ({"events": [{"message": "x"}] * 1001}, 400, "invalid_events"),
        ]
        for body, status, code in cases:
            got, d, _ = self.request(body=body)
            self.assertEqual((got, d["error"]["code"]), (status, code))
        self.assertEqual(self.request(body=b"bad", headers={"Content-Encoding": "gzip"})[0], 400)
        self.assertEqual(
            self.request(body=gzip.compress(b"x" * (4 * 1024**2 + 1)), headers={"Content-Encoding": "gzip"})[
                0
            ],
            413,
        )
        self.assertEqual(
            self.request(body={"events": [{"message": "x"}]}, headers={"Authorization": "Bearer invalid"})[0],
            401,
        )
        self.assertEqual(self.request("GET", path=self.path.replace(self.resource, uid()))[0], 404)
        with self.s.tx():
            self.s.db.execute("UPDATE grants SET revoked_at='now'")
        self.assertEqual(self.request("GET")[0], 401)

    def test_legacy_ids_migration_and_dedup(self):
        body = {
            "resource_id": self.resource,
            "events": [
                {
                    "id": "old",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "severity": "warning",
                    "message": "legacy",
                }
            ],
        }
        with self.s.tx():
            self.assertEqual(self.api.dispatch("/v1/logs", "POST", body, True)["accepted"], 1)
            self.assertEqual(self.api.dispatch("/v1/logs", "POST", body, True)["duplicates"], 1)
        _, d, _ = self.request("GET")
        self.assertEqual(d["items"][0]["severity"], "WARN")
        public = d["items"][0]["id"]
        self.logs.db.close()
        self.logs = Logs(self.paths)
        self.api.logs = self.logs
        _, d, _ = self.request("GET")
        self.assertEqual(d["items"][0]["id"], public)
        self.assertEqual(self.logs.query({})["events"][0]["id"], "old")

    def test_canonical_workspace_and_resource_mapping(self):
        ws, connection, remote = uid(), uid(), uid()
        with self.s.tx():
            self.s.db.execute(
                "INSERT INTO connections VALUES(?,?,?,?,?,?,?)",
                (connection, "https://alshival.ai", "test", ws, "connected", None, "{}"),
            )
            self.s.set_setting("active_account", {"origin": "https://alshival.ai", "id": "test"})
            self.s.db.execute(
                "UPDATE items SET connection=?,remote_id=? WHERE id=?", (connection, remote, self.resource)
            )
        path = f"/v1/workspaces/{ws}/resources/{remote}/logs"
        self.assertEqual(self.request(body={"events": [{"message": "canonical"}]}, path=path)[0], 200)
        self.assertEqual(self.request("GET")[0], 404)
        self.assertEqual(self.request("GET", path=path.replace(remote, self.resource))[0], 404)
        with self.s.tx():
            self.s.set_setting("unavailable:" + self.resource, True)
        self.assertEqual(self.request("GET", path=path)[0], 404)

    def test_cloud_forwarding_adapter_preserves_legacy_queue(self):
        data = json.dumps(
            {
                "resource_id": self.resource,
                "events": [
                    {
                        "id": "stable",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "severity": "warning",
                        "message": "forward",
                    }
                ],
            }
        )
        self.assertEqual(monitoring.forward_payload("https://example.test/v1/logs", data), data.encode())
        cloud = json.loads(monitoring.forward_payload("https://api.alshival.ai" + self.path, data))
        self.assertEqual(set(cloud), {"events"})
        self.assertNotIn("id", cloud["events"][0])
        self.assertEqual(cloud["events"][0]["severity"], "WARN")

    def test_contract_vendor_is_byte_identical_to_pinned_source(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "docs/log-contract-provenance.json").read_text())
        archive = manifest["conformance_archive"]
        self.assertEqual(hashlib.sha256((root / archive["path"]).read_bytes()).hexdigest(), archive["sha256"])
        for name, digest in manifest["files"].items():
            self.assertEqual(
                hashlib.sha256((root / "service/alshitasks/cloud_contract" / name).read_bytes()).hexdigest(),
                digest,
            )
