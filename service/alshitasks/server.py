import fcntl
import http.server
import json
import os
import signal
import socket
import socketserver
import struct
import threading
import time
from .paths import Paths
from .store import Store, Problem
from .logs import Logs
from .api import API
from . import monitoring

MAX_BODY = 30 * 1024**2


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_api()

    def do_POST(self):
        self.handle_api()

    def do_PATCH(self):
        self.handle_api()

    def do_DELETE(self):
        self.handle_api()

    def handle_api(self):
        api = self.server.api
        try:
            owner = False
            if isinstance(self.server, UnixServer):
                _, uid, _ = struct.unpack(
                    "3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                )
                owner = uid == os.getuid()
                if not owner:
                    raise Problem("Unix user mismatch", 403)
                # Agent traffic carries its own scoped credential even on the owner socket.
                if self.headers.get("X-Alshival-Agent") == "1":
                    owner = False
            if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site") in (
                "cross-site",
                "same-site",
            ):
                raise Problem("Browser HTTP access is not supported; use the packaged desktop", 403)
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY or (self.path.startswith("/v1/logs") and length > 1024**2):
                raise Problem("Request exceeds size limit", 413)
            if self.headers.get("Transfer-Encoding"):
                raise Problem("Chunked requests unsupported")
            if length and self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise Problem("JSON content type required", 415)
            self.connection.settimeout(10)
            body = json.loads(self.rfile.read(length)) if length else {}
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if self.path.startswith("/v1/notifications"):
                value = api.dispatch(self.path, self.command, body, owner, token)
            elif self.path == "/v1/backup" and owner:
                with api.store.lock:
                    value = api.dispatch(self.path, self.command, body, owner, token)
            else:
                with api.store.tx():
                    value = api.dispatch(self.path, self.command, body, owner, token)
            self.respond(200, value)
        except Problem as e:
            self.respond(e.status, {"error": {"message": str(e)}})
        except (ValueError, TypeError, KeyError):
            self.respond(400, {"error": {"message": "Malformed request"}})
        except Exception:
            self.respond(500, {"error": {"message": "Service operation failed; your records are retained"}})

    def respond(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


def background(api, stop):
    while not stop.wait(2):
        if api.stopping:
            stop.set()
            break
        try:
            with api.store.tx():
                if api.store.setting("paused", False):
                    continue
            from .health import tick

            tick(api.store)
            with api.store.tx():
                monitoring.forward_once(api.store)
                api.logs.retain(**api.store.setting("retention", {"days": 7, "max_bytes": 1024**3}))
        except Exception:
            # Endpoint-specific status is durable; no credential-bearing diagnostics.
            pass


def run(root=None):
    os.umask(0o077)
    paths = Paths(root)
    lock = (paths.runtime / "service.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Alshival.Ai service already running for this user")
    if paths.socket.exists():
        paths.socket.unlink()
    store = Store(paths)
    logs = Logs(paths)
    api = API(store, logs)
    stop = threading.Event()
    unix = UnixServer(str(paths.socket), Handler)
    unix.api = api
    paths.socket.chmod(0o600)
    servers = [unix]
    port = store.setting("http_port", None)
    if port:
        listener = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        listener.api = api
        servers.append(listener)
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    from .sync_worker import graph_loop, file_loop, notification_loop

    from .notifier import inbox_worker

    for worker in (background, graph_loop, file_loop, notification_loop, inbox_worker):
        threading.Thread(target=worker, args=(api, stop), daemon=True).start()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    for server in servers:
        server.shutdown()
        server.server_close()
    paths.socket.unlink(missing_ok=True)
    logs.db.close()
    store.db.close()
