import base64
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/usr/lib/alshitasks/service")
from alshitasks.paths import Paths
from alshitasks.store import Store, uid, now
from alshitasks.logs import Logs
from alshitasks.files import attach

root = Path(sys.argv[2])
paths = Paths(root)
if sys.argv[1] == "seed":
    store = Store(paths)
    logs = Logs(paths)
    with store.tx():
        board = store.create(
            {"kind": "board", "data": {"title": "Upgrade fixture"}, "idempotency_key": uid()}
        )
        task = store.create(
            {
                "kind": "task",
                "data": {
                    "title": "Keep this task",
                    "board_id": board["id"],
                    "checklist": [{"id": uid(), "text": "Preserve checklist", "done": False}],
                },
                "idempotency_key": uid(),
            }
        )
        note = store.create(
            {
                "kind": "note",
                "data": {"title": "Keep this note", "board_id": board["id"], "body": "Fixture content"},
                "idempotency_key": uid(),
            }
        )
        resource = store.create(
            {
                "kind": "resource",
                "data": {"title": "Keep this resource", "board_id": board["id"]},
                "idempotency_key": uid(),
            }
        )
        store.create(
            {
                "kind": "comment",
                "data": {"parent_id": task["id"], "body": "Keep this comment"},
                "idempotency_key": uid(),
            }
        )
        store.edge_create(
            {"source_id": task["id"], "target_id": note["id"], "kind": "related", "idempotency_key": uid()}
        )
        attach(store, task["id"], "fixture.txt", base64.b64encode(b"Preserved attachment").decode())
    logs.ingest(
        resource["id"],
        [
            {
                "id": "upgrade-event",
                "timestamp": now(),
                "severity": "info",
                "message": "Keep this log",
                "attributes": {},
            }
        ],
    )
    store.db.close()
    logs.db.close()
result = {}
for name, tables in [
    ("projects.sqlite", ["items", "statuses", "edges", "events", "replay", "attachments"]),
    ("logs.sqlite", ["logs"]),
]:
    db = sqlite3.connect(paths.data / name)
    result[name] = {
        table: sorted(db.execute("SELECT * FROM " + table).fetchall(), key=str) for table in tables
    }
    db.close()
result["files"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths.attachments.iterdir()}
fingerprint = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
expected = root / "expected.json"
if sys.argv[1] == "seed":
    expected.write_text(
        json.dumps(
            {
                "fingerprint": fingerprint,
                "counts": {
                    name: {table: len(rows) for table, rows in tables.items()}
                    for name, tables in result.items()
                    if name != "files"
                },
            }
        )
    )
else:
    assert json.loads(expected.read_text())["fingerprint"] == fingerprint, "Upgrade changed fixture data"
    print(
        "Upgrade preserved tasks, notes, resources, comments, statuses, relationships, history, replay receipts, attachments and logs."
    )
