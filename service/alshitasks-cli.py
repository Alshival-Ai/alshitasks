#!/usr/bin/python3
import argparse
import http.client
import json
import os
import socket
import sys
from pathlib import Path
from alshitasks.paths import Paths
from alshitasks.store import Problem
from alshitasks.platform_support import WINDOWS, lock_file


class Connection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=30)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


def call(paths, url, method="GET", body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if WINDOWS:
        endpoint = json.loads(paths.endpoint.read_text(encoding="utf-8"))
        c = http.client.HTTPConnection("127.0.0.1", endpoint["port"], timeout=30)
        headers["X-Alshival-Local"] = endpoint["token"]
    else:
        c = Connection(paths.socket)
    if token is not None:
        headers.update({"Authorization": "Bearer " + token, "X-Alshival-Agent": "1"})
    c.request(method, url, json.dumps(body) if body is not None else None, headers)
    r = c.getresponse()
    value = json.loads(r.read())
    c.close()
    if r.status >= 400:
        raise Problem(value["error"]["message"], r.status)
    return value


def mcp(paths, grant=None, read_only=False):
    from alshitasks.contract import tools
    from alshitasks.security import Secrets

    token = Secrets().get(grant) if grant else ""
    from alshitasks.notifications import INPUT

    catalog = list(tools().values()) + [
        {
            "name": "notifications_read",
            "description": "Read local and authorized portal alerts. Does not mark alerts read.",
            "readOnly": True,
            "inputSchema": INPUT,
        },
        {
            "name": "logs_query",
            "description": "Query local resource logs without a portal account; supports filters and pagination",
            "readOnly": True,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resource_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    **{
                        k: {"type": "string"}
                        for k in ("workspace_id", "since", "until", "severity", "text", "cursor")
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "monitoring_status",
            "description": "Read confirmed resource health",
            "readOnly": True,
            "inputSchema": {
                "type": "object",
                "properties": {"resource_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    ]
    for line in sys.stdin:
        if len(line) > 1024**2:
            raise Problem("MCP request exceeds 1 MiB", 413)
        request = {}
        try:
            request = json.loads(line)
            method = request["method"]
            params = request.get("params", {})
            ident = request.get("id")
            if ident is None:
                continue
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "alshitasks",
                        "version": json.loads(
                            (Path(__file__).resolve().parents[1] / "package.json").read_text()
                        )["version"],
                    },
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {
                    "tools": [
                        dict(
                            name=t["name"],
                            description=t["description"],
                            inputSchema=t["inputSchema"],
                            **({"outputSchema": t["outputSchema"]} if "outputSchema" in t else {}),
                            annotations={"readOnlyHint": t["readOnly"], "destructiveHint": not t["readOnly"]},
                        )
                        for t in catalog
                    ]
                }
            elif method == "tools/call":
                name = params["name"]
                args = params.get("arguments", {})
                if read_only and (name not in tools() or not tools()[name]["readOnly"]):
                    if name not in ("logs_query", "monitoring_status", "notifications_read"):
                        raise Problem("This MCP connection was explicitly started read-only", 403)
                if name in ("logs_query", "monitoring_status", "notifications_read"):
                    from urllib.parse import urlencode

                    if name == "notifications_read":
                        from alshitasks.notifications import validate

                        validate(args)
                        args = dict(args, agent_read="1")
                    elif set(args) - (
                        {
                            "resource_id",
                            "workspace_id",
                            "since",
                            "until",
                            "severity",
                            "text",
                            "cursor",
                            "limit",
                        }
                        if name == "logs_query"
                        else {"resource_id"}
                    ):
                        raise Problem("Invalid query")
                    value = call(
                        paths,
                        (
                            "/v1/logs"
                            if name == "logs_query"
                            else "/v1/notifications"
                            if name == "notifications_read"
                            else "/v1/monitoring"
                        )
                        + "?"
                        + urlencode(args),
                        token=token if grant else None,
                    )
                else:
                    value = call(
                        paths,
                        "/v1/mcp",
                        "POST",
                        {"name": name, "arguments": args},
                        token=token if grant else None,
                    )
                result = {
                    "content": [{"type": "text", "text": json.dumps(value)}],
                    "structuredContent": value if isinstance(value, dict) else {"result": value},
                }
            else:
                raise Problem("Unsupported MCP method", 404)
            print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}), flush=True)
        except Exception as e:
            if request.get("id") is None:
                continue
            if request.get("method") == "tools/call":
                status = e.status if isinstance(e, Problem) else 503
                status = 422 if status in (400, 413) else status
                codes = {
                    401: ("unauthorized", "Authentication required."),
                    403: ("forbidden", "Project operation is not permitted."),
                    404: ("not_found", "Project item is unavailable."),
                    409: ("conflict", "Project changed; reread before retrying."),
                    422: ("invalid_arguments", "Check the project arguments and current revision."),
                    503: ("unavailable", "Project service is temporarily unavailable."),
                }
                if status not in codes:
                    status = 503
                code, message = codes[status]
                value = {"error": {"code": code, "message": message, "status": status}}
                response = {"result": {"isError": True,
                            "content": [{"type": "text", "text": json.dumps(value)}],
                            "structuredContent": value}}
            else:
                response = {"error": {"code": -32602, "message": "Invalid MCP request"}}
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], **response}), flush=True)


def main():
    p = argparse.ArgumentParser(prog="alshitasks")
    p.add_argument("--root", help=argparse.SUPPRESS)
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("service", "status", "pause", "resume", "stop"):
        commands.add_parser(name)
    c = commands.add_parser("desktop")
    c.add_argument("--notification")
    c = commands.add_parser("mcp")
    access = c.add_mutually_exclusive_group()
    access.add_argument("--grant", help="Secret Service entry ID holding a scoped local token; default local-owner access")
    access.add_argument("--read-only", action="store_true",
                        help="Restrict the local MCP connection to read-only tools")
    c = commands.add_parser("grant")
    c.add_argument("--scopes", default="project:read")
    c.add_argument("--resource")
    c.add_argument("--name", default="MCP client")
    c = commands.add_parser("backup")
    c.add_argument("path")
    c = commands.add_parser("restore")
    c.add_argument("path")
    args = p.parse_args()
    paths = Paths(args.root)
    if args.command == "service":
        from alshitasks.server import run

        return run(args.root)
    if args.command == "mcp":
        return mcp(paths, args.grant, args.read_only)
    if args.command == "restore":
        from alshitasks.files import restore

        with (paths.runtime / "service.lock").open("a") as lock:
            try:
                lock_file(lock)
            except BlockingIOError:
                raise Problem("Stop the service before restoring", 409)
            print(json.dumps(restore(paths, args.path)))
            return
    if args.command == "desktop":
        if WINDOWS:
            import subprocess
            app = Path(__file__).resolve().parents[3] / "Alshival.Ai.exe"
            subprocess.Popen([str(app)] + (["--notification", str(__import__("uuid").UUID(args.notification))] if args.notification else []))
            return
        os.execv(
            "/usr/lib/alshitasks/electron/electron",
            ["alshitasks", "/usr/lib/alshitasks"]
            + (
                ["--notification", str(__import__("uuid").UUID(args.notification))]
                if args.notification
                else []
            ),
        )
    if args.command == "grant":
        from alshitasks.security import Secrets

        result = call(
            paths,
            "/v1/projects/keys",
            "POST",
            {"name": args.name, "scopes": args.scopes.split(","), "resource_id": args.resource},
        )
        try:
            Secrets().put(result["id"], result["token"])
        except Exception:
            call(paths, "/v1/projects/keys/" + result["id"], "DELETE")
            raise
        print(json.dumps({"grant": result["id"]}))
        return
    if args.command == "backup":
        result = call(paths, "/v1/backup", "POST", {"path": args.path})
    elif args.command in ("pause", "resume"):
        raise Problem("Global pause has been retired. Disable individual monitoring checks or forwarding destinations, disconnect your portal account, or stop the service.", 410)
    elif args.command == "stop":
        result = call(paths, "/v1/stop", "POST", {})
    else:
        result = call(paths, "/v1/settings")
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (Problem, OSError) as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)
