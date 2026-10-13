import copy
import os
import json
import unittest
import test_sync
from alshitasks.store import uid, Problem
from alshitasks.portal import local_id, sync_connection, encode_data, decode, resolve_conflict


class Reliability(unittest.TestCase):
    setUp = test_sync.SyncAcceptance.setUp
    tearDown = test_sync.SyncAcceptance.tearDown
    sync = test_sync.SyncAcceptance.sync
    transport = test_sync.SyncAcceptance.transport
    update = test_sync.SyncAcceptance.update

    def create(self, kind="task", **data):
        with self.s.tx():
            return self.s.create({"kind": kind, "data": data, "idempotency_key": uid()})

    def link(self, a, b, kind="related"):
        with self.s.tx():
            return self.s.edge_create(
                {"source_id": a["id"], "target_id": b["id"], "kind": kind, "idempotency_key": uid()}
            )

    def test_workspace_graph_rules_and_mentions(self):
        a = self.create("note", title="Note")
        board = self.create("board", title="Second board")
        b = self.create("resource", title="Resource", board_id=board["id"])
        first = self.link(a, b)
        self.assertEqual(first["id"], self.link(b, a)["id"])
        with self.assertRaises(Problem):
            self.link(a, b, "depends_on")
        a = self.update(a, {"body": f"[Resource](#resource-{b['id']})"})
        with self.assertRaises(Problem), self.s.tx():
            self.s.edge_delete(first["id"], first["revision"])
        self.update(a, {"body": ""})
        self.assertEqual(len(self.s.edges()), 1)  # explicit relationship survives mention removal

    def test_mention_only_relationship_removed_with_mention(self):
        b = self.create("resource", title="Resource")
        a = self.create("note", title="Note", body=f"[Resource](#resource-{b['id']})")
        self.assertEqual(len(self.s.edges()), 1)
        self.update(a, {"body": ""})
        self.assertEqual(self.s.edges(), [])

    def test_links_translate_between_local_and_portal(self):
        self.record["kind"] = "resource"
        self.sync()
        ident = local_id(self.c["id"], self.record["id"])
        data = {"body": f"[Resource](#resource-{ident})"}
        encoded = encode_data(self.s, self.c["id"], data)
        self.assertIn("/portal/resources/" + self.record["id"] + "/", encoded["body"])
        record = dict(self.record, data=encoded)
        self.assertEqual(decode(self.s, self.c["id"], record)["data"]["body"], data["body"])

    def test_failed_file_does_not_block_task_upload(self):
        self.sync()
        local = self.s.item(local_id(self.c["id"], self.record["id"]))
        self.update(local, {"title": "Upload before broken file"})

        def transport(origin, path, token, body=None):
            result = self.transport(origin, path, token, body)
            if not body:
                result["attachments"] = [
                    {"id": uid(), "item_id": self.record["id"], "size": 3, "sha256": "bad", "name": "broken"}
                ]
            return result

        sync_connection(self.s, self.c, transport, token="fixture")
        self.assertEqual(self.record["data"]["title"], "Upload before broken file")

    def test_lost_update_response_replays_exact_request_before_new_remote_edit(self):
        self.sync()
        local = self.s.item(local_id(self.c["id"], self.record["id"]))
        self.update(local, {"title": "Local"})
        requests = []

        def unreliable(origin, path, token, body=None):
            if body:
                requests.append(copy.deepcopy(body))
            result = self.transport(origin, path, token, body)
            if body and len(requests) == 1:
                raise Problem("Response lost", 503)
            return result

        with self.assertRaises(Problem):
            sync_connection(self.s, self.c, unreliable, token="fixture")
        self.record["data"]["title"] = "Newer portal edit"
        self.record["revision"] += 1
        sync_connection(self.s, self.c, unreliable, token="fixture")
        self.assertEqual(requests[0], requests[1])
        self.sync()
        self.assertEqual(self.s.item(local["id"])["data"]["title"], "Newer portal edit")
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0], 0)

    def test_reviewed_fields_preserve_independent_changes(self):
        self.sync()
        local = self.s.item(local_id(self.c["id"], self.record["id"]))
        local = self.update(local, {"title": "Local", "body": "Local body"})
        self.record["data"]["title"] = "Portal"
        self.record["data"]["priority"] = "high"
        self.record["revision"] += 1
        self.sync()
        conflict = self.s.db.execute("SELECT * FROM conflicts").fetchone()
        with self.s.tx():
            resolve_conflict(
                self.s,
                {
                    "id": conflict["id"],
                    "choice": "fields",
                    "fields": {"title": "local"},
                    "revision": local["revision"],
                },
            )
        self.sync()
        self.assertEqual(self.record["data"]["title"], "Local")
        self.assertEqual(self.record["data"]["body"], "Local body")
        self.assertEqual(self.record["data"]["priority"], "high")

    def test_attachment_download_resumes_after_network_interruption(self):
        import hashlib, base64
        from alshitasks.sync_files import download_attachments

        self.sync()
        content = b"a" * (256 * 1024) + b"last chunk"
        meta = {
            "id": uid(),
            "item_id": self.record["id"],
            "name": "large.txt",
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        offsets = []

        def transport(origin, path, token, body=None):
            offset = int(path.split("offset=")[1])
            offsets.append(offset)
            if len(offsets) == 2:
                raise Problem("offline", 503)
            return {
                "offset": offset,
                "sha256": meta["sha256"],
                "content": base64.b64encode(content[offset : offset + 256 * 1024]).decode(),
            }

        with self.assertRaises(Problem):
            download_attachments(self.s, self.c, [meta], transport, "fixture")
        download_attachments(self.s, self.c, [meta], transport, "fixture")
        self.assertEqual(offsets, [0, 256 * 1024, 256 * 1024])
        ident = self.s.setting("attachment_local:" + self.c["id"] + ":" + meta["id"])
        self.assertEqual((self.s.paths.attachments / ident).read_bytes(), content)

    def test_token_refresh_does_not_hold_local_database_lock(self):
        import threading
        from unittest.mock import patch
        from alshitasks.portal import Portal

        started, release = threading.Event(), threading.Event()

        def refresh(*args, **kwargs):
            started.set()
            release.wait(2)
            return {"access_token": "new", "expires_in": 3600}

        with (
            patch(
                "alshitasks.portal.Secrets.get",
                return_value=json.dumps(
                    {
                        "access_token": "old",
                        "refresh_token": "refresh",
                        "client_id": "client",
                        "expires_at": 0,
                    }
                ),
            ),
            patch("alshitasks.portal.Secrets.put"),
            patch("alshitasks.portal.remote", side_effect=refresh),
        ):
            thread = threading.Thread(target=lambda: Portal(self.s).token(self.c))
            thread.start()
            self.assertTrue(started.wait(1))
            acquired = self.s.lock.acquire(timeout=0.1)
            if acquired:
                self.s.lock.release()
            release.set()
            thread.join(2)
            self.assertTrue(acquired)

    def test_ssh_key_is_private_local_configuration_not_attachment(self):
        import subprocess, base64
        from alshitasks import ssh, files

        resource = self.create("resource", title="Server")
        source = self.s.paths.data / "fixture-key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "isolated-test", "-f", str(source)],
            check=True,
            capture_output=True,
        )
        secret = source.read_bytes()
        with self.s.tx():
            result = ssh.import_key(
                self.s,
                resource["id"],
                str(source),
                {"alias": "alt-fixture", "host": "example.test", "username": "tester", "port": 22},
            )
        self.assertTrue(result["local_only"])
        row = self.s.db.execute("SELECT * FROM aliases WHERE resource_id=?", (resource["id"],)).fetchone()
        from pathlib import Path

        path = Path(row["identity_file"])
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.read_bytes(), secret)
        self.assertIn("Host alt-fixture", (self.s.paths.home / ".ssh/alshitasks.conf").read_text())
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 0)
        # Deliberately attached key files follow ordinary attachment rules.
        # Dedicated SSH setup above still produces no attachment or sync job.
        with self.s.tx():
            attached = files.attach(self.s, resource["id"], "server.ssh", base64.b64encode(secret).decode())
        self.assertEqual((self.s.paths.attachments / attached["id"]).read_bytes(), secret)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM aliases").fetchone()[0], 1)
        self.sync()
        connected = local_id(self.c["id"], self.record["id"])
        with self.s.tx():
            shared = files.attach(self.s, connected, "server.ssh", base64.b64encode(secret).decode())
        self.assertEqual(self.s.setting("file_job:" + shared["id"])["state"], "queued")

    def test_attachment_conflict_keeps_both_without_overwriting_local_bytes(self):
        import base64
        from alshitasks.files import attach, resolve_attachment

        self.sync()
        ident = local_id(self.c["id"], self.record["id"])
        with self.s.tx():
            file = attach(self.s, ident, "report.txt", base64.b64encode(b"local report").decode())
            key = "file_job:" + file["id"]
            job = self.s.setting(key)
            job["state"] = "conflict"
            self.s.set_setting(key, job)
            result = resolve_attachment(self.s, file["id"])
        copy_id = result["preserved"]["id"]
        self.assertNotEqual(copy_id, file["id"])
        self.assertEqual((self.s.paths.attachments / copy_id).read_bytes(), b"local report")
        self.assertEqual(self.s.setting("file_job:" + copy_id)["state"], "queued")
        self.assertEqual(self.s.setting(key)["state"], "sent")
