"""One MCP protocol implementation for local stdio and remote HTTP."""
import json
from .contract import tools
from .store import Problem

def respond(request, call, version, read_only=False):
    from .notifications import INPUT

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
    try:
        method = request["method"]
        params = request.get("params", {})
        ident = request.get("id")
        if ident is None:
            return None
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "alshitasks",
                    "version": version,
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
                    (
                        "/v1/logs"
                        if name == "logs_query"
                        else "/v1/notifications"
                        if name == "notifications_read"
                        else "/v1/monitoring"
                    )
                    + "?"
                    + urlencode(args),

                )
            else:
                value = call(
                    "/v1/mcp",
                    "POST",
                    {"name": name, "arguments": args},

                )
            result = {
                "content": [{"type": "text", "text": json.dumps(value)}],
                "structuredContent": value if isinstance(value, dict) else {"result": value},
            }
        else:
            raise Problem("Unsupported MCP method", 404)
        return {"jsonrpc": "2.0", "id": ident, "result": result}
    except Exception as e:
        if request.get("id") is None:
            return None
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
        return {"jsonrpc": "2.0", "id": request["id"], **response}
