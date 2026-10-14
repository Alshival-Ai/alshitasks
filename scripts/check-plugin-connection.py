"""Read-only smoke check of a generated local MCP config; never prints workspace content."""
import argparse
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("config", type=Path)
args = parser.parse_args()
server = json.loads(args.config.read_text())["mcpServers"]["alshival-local"]
requests = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "alshival-plugin-check", "version": "0.1.0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "workspace_list", "arguments": {}}},
]
process = subprocess.run([server["command"], *server["args"]], input="".join(json.dumps(r) + "\n" for r in requests),
                         text=True, capture_output=True, timeout=45)
assert process.returncode == 0, "MCP process failed"
responses = {value["id"]: value for line in process.stdout.splitlines() if (value := json.loads(line))}
assert len(responses) == 3
for response in responses.values():
    assert "error" not in response and not response["result"].get("isError"), "MCP request failed"
assert responses[1]["result"]["serverInfo"]["name"] == "alshitasks"
tools = responses[2]["result"]["tools"]
assert any(tool["name"] == "workspace_list" for tool in tools)
assert "workspaces" in responses[3]["result"]["structuredContent"]
print(f"PASS: initialize, {len(tools)} tools discovered, workspace_list succeeded; no writes performed.")
