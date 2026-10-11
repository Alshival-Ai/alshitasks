import base64
import json
from unittest.mock import patch
from test_workspaces import Workspaces
from alshitasks import publishing
from alshitasks.store import uid, Problem
from alshitasks.files import attach
from alshitasks.workspaces import LOCAL, scope


class Publishing(Workspaces):
    def prepare(self, root=False):
        with self.s.tx():
            self.s.db.execute(
                "UPDATE connections SET capabilities=?", (json.dumps({"features": ["board-import-v1"]}),)
            )
            self.s.set_setting("workspace:" + self.ids[0], {"scopes": ["project:read", "board:write"]})
            board = None if root else self.s.create({"kind":"board","data":{"title":"Publish"},"idempotency_key":uid()})
            task = self.s.create(
                {
                    "kind": "task",
                    "data": {"title": "Keep identity", "board_id": board["id"] if board else None},
                    "idempotency_key": uid(),
                }
            )
            attachment = attach(self.s, task["id"], "example.txt", base64.b64encode(b"file").decode())
            job = publishing.start(
                self.s, {"workspace_id": self.ids[0], "board_id": board["id"] if board else LOCAL}
            )
        return task, attachment, self.s.setting("publication:" + job["id"])

    def test_publication_freezes_items_files_edges_and_statuses(self):
        task, attachment, job = self.prepare()
        with self.s.tx():
            with self.assertRaises(Problem):
                self.s.update(
                    task["id"],
                    {"revision": task["revision"], "data": {"title": "Changed"}, "idempotency_key": uid()},
                )
            with self.assertRaises(Problem):
                attach(self.s, task["id"], "new.txt", "")
            with self.assertRaises(Problem):
                self.s.status_save({"data": {"board_id": job["board_id"], "name": "New"}})
        self.assertEqual(
            publishing.archive_bytes(self.s, job["manifest"]),
            publishing.archive_bytes(self.s, job["manifest"]),
        )

    def test_lost_commit_response_resumes_without_rebinding_or_duplication(self):
        task, attachment, job = self.prepare(root=True)
        result = {
            "items": {r["id"]: dict(r, id=uid()) for r in job["manifest"]["items"]},
            "statuses": {},
            "edges": {},
            "attachments": {attachment["id"]: uid()},
        }
        calls = []
        committed = False

        def remote(origin, path, token, body):
            nonlocal committed
            calls.append(body["operation"])
            if body["operation"] == "begin":
                return {"received": [], "result": result if committed else None}
            if body["operation"] == "chunk":
                return {"received": body["position"]}
            if body["operation"] == "commit":
                committed = True
                raise OSError("Response lost")

        with (
            patch("alshitasks.portal.Portal.token", return_value="fixture"),
            patch("alshitasks.portal.remote", side_effect=remote),
        ):
            publishing.resume_all(self.s)
            self.assertIsNone(
                self.s.db.execute("SELECT connection FROM items WHERE id=?", (task["id"],)).fetchone()[0]
            )
            with self.s.tx():
                publishing.dispatch(self.s, "POST", {"retry": job["id"]})
            publishing.resume_all(self.s)
        self.assertEqual(calls.count("commit"), 1)
        self.assertEqual(self.s.setting("publication:" + job["id"])["state"], "complete")
        self.assertEqual(
            self.s.db.execute("SELECT connection FROM items WHERE id=?", (task["id"],)).fetchone()[0],
            self.ids[0],
        )
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1)
        with self.s.tx(), scope(self.s, self.ids[0]):
            self.assertEqual(self.s.item(task["id"])["data"]["board_id"], job["board_id"])

    def test_cancel_unlocks_only_after_portal_acknowledgement(self):
        task, _, job = self.prepare()
        with self.s.tx():
            publishing.dispatch(self.s, "POST", {"cancel": job["id"]})
        self.assertTrue(self.s.setting("publishing:" + job["source_board"]))
        with (
            patch("alshitasks.portal.Portal.token", return_value="fixture"),
            patch("alshitasks.portal.remote", return_value={"cancelled": True}),
        ):
            publishing.resume_all(self.s)
        self.assertIsNone(self.s.setting("publishing:" + job["source_board"]))
        self.assertEqual(self.s.setting("publication:" + job["id"])["state"], "cancelled")
        self.assertIsNone(
            self.s.db.execute("SELECT connection FROM items WHERE id=?", (task["id"],)).fetchone()[0]
        )
