import http.client
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from alshitasks.paths import Paths
from alshitasks.store import Store, uid
from alshitasks.security import grant_create


class HTTPAcceptance(unittest.TestCase):
    def test_loopback_auth_resource_scope_batch_bounds_and_pause(self):
        with tempfile.TemporaryDirectory() as d:
            paths = Paths(d)
            s = Store(paths)
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.close()
            with s.tx():
                s.set_setting("http_port", port)
                r = s.create({"kind": "resource", "data": {"title": "Logs"}, "idempotency_key": uid()})
                g = grant_create(s, {"scopes": ["logs:write", "logs:read"], "resource_id": r["id"]})
            s.db.close()
            script = Path(__file__).resolve().parents[1] / "service/alshitasks-cli.py"
            p = subprocess.Popen(
                [sys.executable, str(script), "--root", d, "service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

            def request(method, route, body=None, headers=None):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    c.request(
                        method,
                        route,
                        json.dumps(body) if body is not None else None,
                        {"Content-Type": "application/json", **(headers or {})},
                    )
                except BrokenPipeError:
                    # The server can reject Content-Length before consuming an
                    # oversized body. Still assert its actual HTTP response.
                    pass
                r = c.getresponse()
                result = (r.status, json.loads(r.read()))
                c.close()
                return result

            try:
                for _ in range(100):
                    try:
                        status, _ = request("GET", "/v1/health")
                        break
                    except OSError:
                        time.sleep(0.03)
                self.assertEqual(status, 401)
                token = {"Authorization": "Bearer " + g["token"]}
                self.assertEqual(request("GET", "/v1/projects/items", headers=token)[0], 403)
                self.assertEqual(request("GET", "/v1/logs?resource_id=" + r["id"], headers=token)[0], 200)
                self.assertEqual(request("GET", "/v1/logs?resource_id=" + uid(), headers=token)[0], 403)
                self.assertEqual(
                    request(
                        "GET",
                        "/v1/logs?resource_id=" + r["id"],
                        headers={**token, "Origin": "https://evil.example"},
                    )[0],
                    403,
                )
                from datetime import datetime, timezone

                event = {
                    "id": "one",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "severity": "info",
                    "message": "works",
                    "attributes": {},
                }
                status, result = request(
                    "POST", "/v1/logs", {"resource_id": r["id"], "events": [event]}, token
                )
                self.assertEqual(status, 200)
                self.assertEqual(result["accepted"], 1)
                status, result = request(
                    "POST", "/v1/logs", {"resource_id": r["id"], "events": [event]}, token
                )
                self.assertEqual(result["duplicates"], 1)
                self.assertEqual(
                    request("POST", "/v1/logs", {"resource_id": r["id"], "events": [event] * 101}, token)[0],
                    400,
                )
                self.assertEqual(
                    request(
                        "POST",
                        "/v1/logs",
                        {"resource_id": r["id"], "events": [], "oversized": "x" * 1024**2},
                        token,
                    )[0],
                    413,
                )
            finally:
                p.terminate()
                p.communicate(timeout=10)
