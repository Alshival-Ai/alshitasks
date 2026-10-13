"""Run the same ingestion corpus against the pinned cloud API and real local HTTP."""

import gzip
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "tests"))
from test_cloud_logs import CloudLogs
from fastapi.testclient import TestClient
from alshival_api.app import create_app
from alshival_api.config import Settings
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

fixture = CloudLogs()
fixture.setUp()
try:
    domain, store, limits = AsyncMock(), AsyncMock(), AsyncMock()
    domain.call.return_value = {
        "key_id": "00000000-0000-4000-8000-000000000010",
        "quota_bytes": 10000000,
        "retention_days": 7,
    }
    cloud = TestClient(
        create_app(Settings(service_secret="test", cursor_secret="test"), domain, store, limits)
    )
    cases = [
        ({"events": [{"message": "default"}]}, 200),
        (
            {
                "events": [
                    {
                        "timestamp": "2026-01-01T01:00:00+01:00",
                        "message": "trace",
                        "severity": "trace",
                        "source": "worker",
                        "trace_id": "AB" * 16,
                        "span_id": "CD" * 8,
                        "attributes": {"n": 42},
                    }
                ]
            },
            200,
        ),
        ({"events": [{"message": "x"}] * 1000}, 200),
        ({"events": []}, 400),
        ({"events": [{"message": "x", "id": "unsupported"}]}, 400),
        ({"events": [{"message": "x", "timestamp": "2026-01-01"}]}, 400),
        ({"events": [{"message": "x", "severity": "warning"}]}, 400),
        ({"events": [{"message": "x", "trace_id": "abc"}]}, 400),
        ({"events": [{"message": "x"}] * 1001}, 400),
        ({"events": [{"message": "x" * 65537}]}, 400),
    ]
    with cloud:
        for body, expected in cases:
            raw = json.dumps(body).encode()
            headers = {"Authorization": "Bearer alsh_ik_fixture", "Content-Type": "application/json"}
            remote = cloud.post(fixture.path, content=raw, headers=headers)
            local, payload, _ = fixture.request(body=body)
            assert (remote.status_code, local) == (expected, expected), (remote.text, payload)
            if expected == 200:
                assert remote.json()["accepted"] == payload["accepted"]
                # Cloud normalized event fields must survive the SQLite adapter without loss.
                normalized = store.insert.call_args.args[3]
                for event, _ in normalized:
                    matches = fixture.logs.db.execute(
                        "SELECT * FROM logs WHERE message=? ORDER BY received_at DESC LIMIT 1",
                        (event["message"],),
                    ).fetchall()
                    assert matches
                    r = matches[0]
                    from alshitasks.cloud_logs import severity

                    assert (
                        severity(r["severity"]),
                        r["source"],
                        r["trace_id"],
                        r["span_id"],
                        json.loads(r["attributes"]),
                    ) == (
                        event["severity"],
                        event["source"],
                        event["trace_id"],
                        event["span_id"],
                        event["attributes"],
                    )
            else:
                assert remote.json()["error"]["code"] == payload["error"]["code"]
        for raw, headers, expected in [
            (gzip.compress(b'{"events":[{"message":"gzip"}]}'), {"Content-Encoding": "gzip"}, 200),
            (b"broken", {"Content-Encoding": "gzip"}, 400),
            (gzip.compress(b"x" * (4 * 1024**2 + 1)), {"Content-Encoding": "gzip"}, 413),
        ]:
            remote = cloud.post(
                fixture.path,
                content=raw,
                headers={
                    "Authorization": "Bearer alsh_ik_fixture",
                    "Content-Type": "application/json",
                    **headers,
                },
            )
            local, payload, _ = fixture.request(body=raw, headers=headers)
            assert remote.status_code == local == expected
            if expected != 200:
                assert remote.json()["error"]["code"] == payload["error"]["code"]
        proto = ExportLogsServiceRequest()
        log = proto.resource_logs.add().scope_logs.add().log_records.add()
        log.body.string_value = "protobuf"
        log.severity_number = 9
        route = fixture.path.removesuffix("logs") + "otlp/v1/logs"
        for raw, content in [
            (proto.SerializeToString(), "application/x-protobuf"),
            (b'{"resourceLogs":[]}', "application/json"),
        ]:
            remote = cloud.post(
                route,
                content=raw,
                headers={"Authorization": "Bearer alsh_ik_fixture", "Content-Type": content},
            )
            local, payload, _ = fixture.request(body=raw, path=route, headers={"Content-Type": content})
            assert remote.status_code == local == 200
            assert remote.content == (payload if isinstance(payload, bytes) else json.dumps(payload).encode())
    print(
        "PASS: identical JSON, gzip and OTLP corpus against cloud and localhost, including normalized fields and error codes."
    )
finally:
    fixture.tearDown()
