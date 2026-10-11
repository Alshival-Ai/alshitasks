import json
from pathlib import Path
import jsonschema
from .store import Problem

CONTRACT = Path(__file__).resolve().parents[2] / "contracts/project-tools.v1.json"
# Installed service is /usr/lib/alshitasks/service/alshitasks; repository uses same layout.


def tools():
    return {x["name"]: x for x in json.loads(CONTRACT.read_text())["tools"]}


def validate(name, args):
    if name not in tools():
        raise Problem("Unknown tool")
    try:
        jsonschema.validate(args, tools()[name]["inputSchema"], format_checker=jsonschema.FormatChecker())
    except jsonschema.ValidationError as e:
        raise Problem(e.message)
