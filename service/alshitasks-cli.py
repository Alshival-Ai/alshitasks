#!/usr/bin/python3
import argparse
import fcntl
import http.client
import json
import os
import socket
import sys
from pathlib import Path
from alshitasks.paths import Paths
from alshitasks.store import Problem


class Connection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=30)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


def call(paths, url, method="GET", body=None, token=None):
    c = Connection(paths.socket)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers.update({"Authorization": "Bearer " + token, "X-Alshival-Agent": "1"})
    c.request(method, url, json.dumps(body) if body is not None else None, headers)
    r = c.getresponse()
    value = json.loads(r.read())
    c.close()
    if r.status >= 400:
        raise Problem(value["error"]["message"], r.status)
    return value


def mcp(paths, grant=None):
    from alshitasks.contract import tools
    from alshitasks.security import Secrets

    token = Secrets().get(grant) if grant else ""
    catalog = list(tools().values()) + [
        {
            "name": "logs_query",
            "description": "Query at most 100 resource log events",
            "readOnly": True,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resource_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
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
                            annotations={"readOnlyHint": t["readOnly"]},
                        )
                        for t in catalog
                    ]
                }
            elif method == "tools/call":
                name = params["name"]
                args = params.get("arguments", {})
                if not grant and (name not in tools() or not tools()[name]["readOnly"]):
                    if name not in ("logs_query", "monitoring_status"):
                        raise Problem("Write access requires an explicit Secret Service grant", 403)
                if name in ("logs_query", "monitoring_status"):
                    from urllib.parse import urlencode

                    if set(args) - ({"resource_id", "limit"} if name == "logs_query" else {"resource_id"}):
                        raise Problem("Invalid query")
                    value = call(
                        paths,
                        ("/v1/logs" if name == "logs_query" else "/v1/monitoring") + "?" + urlencode(args),
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
            if request.get("id") is not None:
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request["id"],
                            "error": {
                                "code": -32602,
                                "message": str(e) if isinstance(e, Problem) else "Invalid MCP request",
                            },
                        }
                    ),
                    flush=True,
                )


def main():
    p = argparse.ArgumentParser(prog="alshitasks")
    p.add_argument("--root", help=argparse.SUPPRESS)
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("service", "status", "pause", "resume", "stop", "desktop"):
        commands.add_parser(name)
    c = commands.add_parser("mcp")
    c.add_argument("--grant", help="Secret Service entry ID holding a scoped local token; default read-only")
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
        return mcp(paths, args.grant)
    if args.command == "restore":
        from alshitasks.files import restore

        with (paths.runtime / "service.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Problem("Stop the service before restoring", 409)
            print(json.dumps(restore(paths, args.path)))
            return
    if args.command == "desktop":
        os.execv("/usr/lib/alshitasks/electron/electron", ["alshitasks", "/usr/lib/alshitasks"])
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
        result = call(paths, "/v1/settings", "PATCH", {"paused": args.command == "pause"})
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
