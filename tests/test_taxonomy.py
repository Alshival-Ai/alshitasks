import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from alshitasks.store import Store, Problem, uid
from alshitasks.paths import Paths
from alshitasks import taxonomy


class TaxonomyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Paths(self.temp.name))
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.db.close)

    def write(self, **args):
        with self.store.tx():
            return taxonomy.configure(
                self.store,
                dict(revision=taxonomy.catalog(self.store)["revision"], idempotency_key=uid(), **args),
            )

    def test_offline_defaults_labels_archive_and_partial_edit(self):
        first = taxonomy.catalog(self.store)
        self.assertEqual([r["name"] for r in first["types"]], ["Task", "Bug", "Story", "Improvement"])
        values = self.write(kind="label", operation="create", name="Backend")
        label = values["labels"][0]["id"]
        with self.store.tx():
            task = self.store.create(
                dict(kind="task", idempotency_key=uid(), data=dict(title="Task", label_ids=[label]))
            )
        self.assertEqual(task["data"]["type_id"], first["types"][0]["id"])
        self.write(kind="label", operation="archive", id=label)
        with self.store.tx():
            task = self.store.update(
                task["id"],
                dict(revision=task["revision"], idempotency_key=uid(), data=dict(title="Preserved")),
            )
        self.assertEqual(task["data"]["label_ids"], [label])
        with self.assertRaises(Problem), self.store.tx():
            self.store.create(
                dict(kind="task", idempotency_key=uid(), data=dict(title="Invalid", label_ids=[label]))
            )
        with self.store.tx():
            task = self.store.update(
                task["id"], dict(revision=task["revision"], idempotency_key=uid(), data=dict(label_ids=[]))
            )
        self.assertEqual(task["data"]["label_ids"], [])

    def test_unique_names_and_default_retirement(self):
        initial = taxonomy.catalog(self.store)
        with self.assertRaises(Problem):
            self.write(kind="type", operation="create", name="bUG")
        with self.assertRaises(Problem):
            self.write(kind="type", operation="archive", id=initial["types"][0]["id"])
        self.write(kind="type", operation="default", id=initial["types"][1]["id"])
        values = self.write(kind="type", operation="archive", id=initial["types"][0]["id"])
        self.assertTrue(values["types"][0]["archived"])
        with self.assertRaises(Problem), self.store.tx():
            taxonomy.configure(
                self.store,
                dict(
                    kind="label",
                    operation="create",
                    name="Stale",
                    revision=initial["revision"],
                    idempotency_key=uid(),
                ),
            )

    def test_workspace_isolation(self):
        values = self.write(kind="label", operation="create", name="Local")
        label = values["labels"][0]["id"]
        self.store.workspace = uid()
        self.assertEqual(taxonomy.catalog(self.store)["labels"], [])
        with self.assertRaises(Problem):
            taxonomy.validate(self.store, "task", dict(label_ids=[label]))

    def test_incremental_catalogue_keeps_unchanged_rows(self):
        selected = uid()
        first = taxonomy.catalog(self.store)
        taxonomy.receive(self.store, selected, first["types"], [], "one")
        changed = dict(first["types"][1], name="Defect", revision=2)
        taxonomy.receive(self.store, selected, [changed], [], "two")
        values = taxonomy.catalog(self.store, selected)
        self.assertEqual(len(values["types"]), 4)
        self.assertEqual(values["types"][1]["name"], "Defect")
        self.assertEqual(len(self.store.setting("taxonomy_latest:" + selected)["types"]), 4)

    def test_filters_apply_before_pagination_and_combine(self):
        values = self.write(kind="label", operation="create", name="Backend")
        label = values["labels"][0]["id"]
        with self.store.tx():
            first = self.store.create(
                dict(
                    kind="task",
                    idempotency_key=uid(),
                    data=dict(title="Match", type_id=values["types"][1]["id"], label_ids=[label]),
                )
            )
            self.store.create(
                dict(
                    kind="task",
                    idempotency_key=uid(),
                    data=dict(title="No label", type_id=values["types"][1]["id"]),
                )
            )
            self.store.create(
                dict(kind="task", idempotency_key=uid(), data=dict(title="Wrong type", label_ids=[label]))
            )
        result = self.store.list(type_ids=[values["types"][1]["id"]], label_ids=[label], limit=1)
        self.assertEqual([r["id"] for r in result["items"]], [first["id"]])
        self.assertIsNone(result["next"])
