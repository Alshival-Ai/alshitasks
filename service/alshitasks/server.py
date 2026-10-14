import hmac
import secrets
import http.server
import json
import logging
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
from .platform_support import WINDOWS, lock_file

MAX_BODY = 30 * 1024**2


class UnixServer(socketserver.ThreadingMixIn, getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)):
    daemon_threads = True


def local_owner(handler):
    if isinstance(handler.server, UnixServer):
        _, uid, _ = struct.unpack("3i", handler.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            raise Problem("Unix user mismatch", 403)
        return handler.headers.get("X-Alshival-Agent") != "1"
    expected = getattr(handler.server, "owner_token", None)
    if expected is not None:
        supplied = handler.headers.get("X-Alshival-Local", "")
        if not hmac.compare_digest(supplied, expected):
            raise Problem("Local authentication required", 401)
        return handler.headers.get("X-Alshival-Agent") != "1"
    return False


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
        from .cloud_logs import ROUTE
        from urllib.parse import urlsplit
        if ROUTE.fullmatch(urlsplit(self.path).path):
            from .cloud_http import handle
            return handle(self)
        api = self.server.api
        try:
            owner = local_owner(self)
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


def supervise(api, stop, worker):
    """Unexpected failures must not silently disable a background worker."""
    while not stop.is_set() and not api.stopping:
        try:
            with api.store.tx():
                api.store.set_setting('worker:' + worker.__name__, {'state': 'running'})
            worker(api, stop)
            if stop.is_set() or api.stopping:
                return
        except Exception:
            # Exception text can contain request content or credentials.
            logging.getLogger(__name__).warning('Background worker %s interrupted; retrying', worker.__name__)
        try:
            with api.store.tx():
                api.store.set_setting('worker:' + worker.__name__, {'state': 'retrying', 'at': time.time()})
        except Exception:
            pass  # A temporary local database failure must also recover.
        stop.wait(1)


def run(root=None):
    os.umask(0o077)
    paths = Paths(root)
    lock = (paths.runtime / "service.lock").open("a")
    try:
        lock_file(lock)
    except BlockingIOError:
        raise SystemExit("Alshival.Ai service already running for this user")
    if paths.socket.exists():
        paths.socket.unlink()
    store = Store(paths)
    logs = Logs(paths)
    api = API(store, logs)
    stop = threading.Event()
    unix = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) if WINDOWS else UnixServer(str(paths.socket), Handler)
    unix.api = api
    if WINDOWS:
        unix.owner_token = secrets.token_urlsafe(48)
        temporary = paths.endpoint.with_suffix(".tmp")
        temporary.write_text(json.dumps({"port": unix.server_port, "token": unix.owner_token, "pid": os.getpid()}), encoding="utf-8")
        temporary.replace(paths.endpoint)
    else:
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
    from .workspace_icons import icon_loop

    for worker in (background, graph_loop, file_loop, notification_loop, inbox_worker, icon_loop):
        threading.Thread(target=supervise, args=(api, stop, worker), daemon=True, name=worker.__name__).start()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    for server in servers:
        server.shutdown()
        server.server_close()
    paths.socket.unlink(missing_ok=True)
    paths.endpoint.unlink(missing_ok=True)
    logs.db.close()
    store.db.close()
