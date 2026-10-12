import copy
import json
import unittest
from test_sync import SyncAcceptance
from alshitasks import taxonomy
from alshitasks.store import uid, Problem
from alshitasks.portal import local_id


class TaxonomySync(unittest.TestCase):
    setUp = SyncAcceptance.setUp
    tearDown = SyncAcceptance.tearDown
    sync = SyncAcceptance.sync
    update = SyncAcceptance.update

    def prepare(self):
        self.remote_catalog = taxonomy.catalog(self.s)
        self.remote_catalog["revision"] = "remote-one"
        self.record["data"]["type_id"] = self.remote_catalog["types"][0]["id"]
        self.c["capabilities"] = json.dumps({"features": ["task-taxonomy-v1"]})
        with self.s.tx():
            self.s.db.execute("UPDATE connections SET capabilities=?", (self.c["capabilities"],))
            self.s.set_setting("workspace:" + self.c["id"], {"can_manage_taxonomy": True})
        self.s.workspace = self.c["id"]
        self.sync()

    def transport(self, origin, path, token, body=None):
        if body and body["tool"] == "project_taxonomy_write":
            args = body["arguments"]
            if args["revision"] != self.remote_catalog["revision"]:
                raise Problem("Conflict", 409)
            self.remote_catalog["labels"].append(
                dict(id=args["id"], name=args["name"], archived=False, revision=1)
            )
            self.remote_catalog["revision"] += "-next"
            return {"result": copy.deepcopy(self.remote_catalog)}
        result = SyncAcceptance.transport(self, origin, path, token, body)
        if not body:
            result.update(
                types=copy.deepcopy(self.remote_catalog["types"]),
                labels=copy.deepcopy(self.remote_catalog["labels"]),
                taxonomy_revision=self.remote_catalog["revision"],
            )
        return result

    def test_two_way_type_updates_and_overlapping_review(self):
        self.prepare()
        local = self.s.item(local_id(self.c["id"], self.record["id"]))
        self.record["data"]["type_id"] = self.remote_catalog["types"][1]["id"]
        self.record["revision"] += 1
        self.sync()
        local = self.s.item(local["id"])
        self.assertEqual(local["data"]["type_id"], self.record["data"]["type_id"])
        self.update(local, {"type_id": self.remote_catalog["types"][2]["id"]})
        self.sync()
        self.assertEqual(self.record["data"]["type_id"], self.remote_catalog["types"][2]["id"])
        local = self.s.item(local["id"])
        self.update(local, {"type_id": self.remote_catalog["types"][0]["id"]})
        self.record["data"]["type_id"] = self.remote_catalog["types"][3]["id"]
        self.record["revision"] += 1
        self.sync()
        self.assertEqual(
            self.s.db.execute("SELECT COUNT(*) FROM conflicts WHERE resolved=0").fetchone()[0], 1
        )

    def test_catalogue_conflict_preserves_local_until_explicit_review(self):
        self.prepare()
        with self.s.tx():
            taxonomy.configure(
                self.s,
                dict(
                    kind="label",
                    operation="create",
                    name="Local label",
                    revision=taxonomy.catalog(self.s)["revision"],
                    idempotency_key=uid(),
                ),
            )
        self.remote_catalog["revision"] = "peer-change"
        self.sync()
        values = taxonomy.catalog(self.s)
        self.assertEqual(values["labels"][0]["name"], "Local label")
        self.assertTrue(values["review"])
        with self.s.tx():
            taxonomy.review(self.s, {"choice": "local", "revision": "peer-change"})
        self.sync()
        self.assertEqual(self.remote_catalog["labels"][0]["name"], "Local label")
        self.assertFalse(taxonomy.catalog(self.s)["review"])

    def test_archived_type_requires_review_instead_of_endless_retry(self):
        self.prepare()
        local = self.s.item(local_id(self.c["id"], self.record["id"]))
        self.update(local, {"type_id": self.remote_catalog["types"][1]["id"]})
        self.remote_catalog["types"][1]["archived"] = True
        self.remote_catalog["revision"] = "archived"
        self.sync()
        row = self.s.db.execute("SELECT state,reason FROM outbox WHERE item_id=?", (local["id"],)).fetchone()
        self.assertEqual(row["state"], "conflict")
        self.assertIn("archived", row["reason"])
        self.assertEqual(self.s.item(local["id"])["data"]["type_id"], self.remote_catalog["types"][1]["id"])
