import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from alshitasks.paths import Paths
from alshitasks.store import Store, uid
from alshitasks.logs import Logs
from alshitasks.api import API
from alshitasks.contract import tools


class Protocol(unittest.TestCase):
    def test_contract_read_write_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Paths(d))
            logs = Logs(store.paths)
            api = API(store, logs)
            try:
                with store.tx():
                    created = api.tool(
                        "project_create",
                        {"kind": "task", "idempotency_key": uid(), "data": {"title": "First"}},
                        owner=True,
                    )
                    for name, args in [
                        ("project_list", {}),
                        ("project_get", {"id": created["id"]}),
                        ("project_boards", {}),
                        ("project_members", {}),
                        ("project_statuses", {}),
                        ("project_comments", {"id": created["id"]}),
                        ("project_edges", {}),
                        (
                            "project_update",
                            {
                                "id": created["id"],
                                "revision": 1,
                                "idempotency_key": uid(),
                                "data": {"body": "Edit"},
                            },
                        ),
                        (
                            "project_status_write",
                            {
                                "operation": "create",
                                "idempotency_key": uid(),
                                "revision": api.status_catalog()["revision"],
                                "name": "Custom",
                                "category": "todo",
                            },
                        ),
                    ]:
                        result = api.tool(name, args, owner=True)
                        jsonschema.validate(result, tools()[name]["outputSchema"])
            finally:
                store.db.close()
                logs.db.close()

    def test_explicit_read_only_grant_over_mcp_transport(self):
        from alshitasks.security import grant_create
        from alshitasks.store import Problem

        with tempfile.TemporaryDirectory() as d:
            store = Store(Paths(d))
            logs = Logs(store.paths)
            api = API(store, logs)
            try:
                with store.tx():
                    grant = grant_create(store, {"scopes": ["project:read"]})
                    result = api.dispatch(
                        "/v1/mcp", "POST", {"name": "project_list", "arguments": {}}, token=grant["token"]
                    )
                    self.assertIn("items", result)
                    with self.assertRaises(Problem) as raised:
                        api.dispatch(
                            "/v1/mcp",
                            "POST",
                            {
                                "name": "project_create",
                                "arguments": {
                                    "kind": "task",
                                    "data": {"title": "Denied"},
                                    "idempotency_key": uid(),
                                },
                            },
                            token=grant["token"],
                        )
                    self.assertEqual(raised.exception.status, 403)
            finally:
                store.db.close()
                logs.db.close()

    def test_graph_retry_checks_receipt_before_observed_revision(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Paths(d))
            logs = Logs(store.paths)
            api = API(store, logs)
            try:
                with store.tx():
                    first = store.create({"kind": "task", "data": {"title": "A"}, "idempotency_key": uid()})
                    second = store.create({"kind": "task", "data": {"title": "B"}, "idempotency_key": uid()})
                    args = {
                        "source_id": first["id"],
                        "target_id": second["id"],
                        "kind": "depends_on",
                        "expected_revision": 0,
                        "idempotency_key": uid(),
                    }
                    edge = api.tool("project_link", args, owner=True)
                    self.assertEqual(api.tool("project_link", args, owner=True), edge)
                    unlink = {"id": edge["id"], "revision": edge["revision"], "idempotency_key": uid()}
                    deleted = api.tool("project_unlink", unlink, owner=True)
                    self.assertEqual(api.tool("project_unlink", unlink, owner=True), deleted)
            finally:
                store.db.close()
                logs.db.close()

    def test_real_service_mcp_and_read_only(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "service/alshitasks-cli.py"
        with tempfile.TemporaryDirectory() as d:
            service = subprocess.Popen(
                [sys.executable, str(script), "--root", d, "service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            try:
                for _ in range(100):
                    if (Path(d) / ("runtime/service.json" if os.name == "nt" else "runtime/service.sock")).exists():
                        break
                    time.sleep(0.05)
                requests = [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "project_list", "arguments": {}},
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {
                            "name": "project_create",
                            "arguments": {
                                "kind": "task",
                                "data": {"title": "Denied"},
                                "idempotency_key": uid(),
                            },
                        },
                    },
                ]
                requests.append(
                    {
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "tools/call",
                        "params": {
                            "name": "logs_query",
                            "arguments": {
                                "workspace_id": "00000000-0000-4000-8000-000000000001",
                                "severity": "info",
                                "text": "offline logs",
                                "limit": 10,
                            },
                        },
                    }
                )
                requests.append(
                    {
                        "jsonrpc": "2.0",
                        "id": 6,
                        "method": "tools/call",
                        "params": {
                            "name": "notifications_read",
                            "arguments": {"filter": "unread", "limit": 5},
                        },
                    }
                )
                p = subprocess.run(
                    [sys.executable, str(script), "--root", d, "mcp", "--read-only"],
                    input="".join(json.dumps(r) + "\n" for r in requests),
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(p.returncode, 0, p.stderr)
                results = [json.loads(x) for x in p.stdout.splitlines()]
                self.assertEqual(results[0]["result"]["serverInfo"]["name"], "alshitasks")
                self.assertEqual(len(results[1]["result"]["tools"]), 19)
                self.assertIn("result", results[2])
                self.assertTrue(results[3]["result"]["isError"])
                self.assertEqual(results[3]["result"]["structuredContent"]["error"]["status"], 403)
                self.assertEqual(
                    results[4]["result"]["structuredContent"], {"events": [], "next_cursor": None}
                )
                self.assertEqual(results[5]["result"]["structuredContent"], {"items": [], "next_page": None})
                log_tool = next(t for t in results[1]["result"]["tools"] if t["name"] == "logs_query")
                self.assertIn("cursor", log_tool["inputSchema"]["properties"])
                if os.name != "nt":
                    self.assertEqual((Path(d) / "runtime/service.sock").stat().st_mode & 0o777, 0o600)
                # Default owner mode works without a desktop keyring and keeps revision checks.
                write = requests[3]
                write["params"]["arguments"]["data"]["title"] = "Local owner write"
                p = subprocess.run(
                    [sys.executable, str(script), "--root", d, "mcp"],
                    input=json.dumps(write) + "\n", capture_output=True, text=True, check=True,
                )
                created = json.loads(p.stdout)["result"]["structuredContent"]
                update = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
                    "name": "project_update", "arguments": {"id": created["id"], "revision": 999,
                    "idempotency_key": uid(), "data": {"title": "Conflict"}}}}
                p = subprocess.run(
                    [sys.executable, str(script), "--root", d, "mcp"],
                    input=json.dumps(update) + "\n", capture_output=True, text=True, check=True,
                )
                self.assertTrue(json.loads(p.stdout)["result"]["isError"])
                self.assertEqual(json.loads(p.stdout)["result"]["structuredContent"]["error"]["status"], 409)
                p = subprocess.run([sys.executable, str(script), "--root", d, "mcp",
                                    "--read-only", "--grant", "fixture"], capture_output=True)
                self.assertNotEqual(p.returncode, 0)

            finally:
                service.terminate()
                service.communicate(timeout=10)
