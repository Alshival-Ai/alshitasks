import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from alshitasks.store import Store, uid, Problem
from alshitasks.paths import Paths
from alshitasks.api import API
from alshitasks.workspaces import LOCAL, scope
from alshitasks.accounts import activate, discover, signout


class Workspaces(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.s = Store(Paths(self.temp.name))
        self.api = API(self.s, None)
        self.account = uid()
        with self.s.tx():
            self.s.db.execute(
                "INSERT INTO connections VALUES(?,?,?,?,?,?,?)",
                (self.account, "https://alshival.ai", "7", "", "connected", None, "{}"),
            )
            activate(self.s, self.account, {"account_id": "7"})
        self.remote = [
            {"id": uid(), "name": "Shared", "scopes": ["project:read", "tasks:write"]} for _ in range(2)
        ]
        self.discover()
        self.ids = [r[0] for r in self.s.db.execute("SELECT id FROM connections WHERE workspace!=''")]

    def tearDown(self):
        self.s.db.close()
        self.temp.cleanup()

    def discover(self):
        with self.s.tx():
            self.s.set_setting("discovery", {"next": 0})
        with patch("alshitasks.portal.Portal.token", return_value="fixture"):
            discover(
                self.s,
                lambda origin, path, token: (
                    {"resource": origin + "/desktop/v1", "account_id": "7", "features": ["graph-sync-v2"]}
                    if path.endswith("/capabilities")
                    else {"workspaces": self.remote}
                ),
            )

    def test_icon_catalog_matches_renderer(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root/'desktop/renderer/workspace-icons.json').read_bytes(), (root/'service/alshitasks/assets/workspace-icons.json').read_bytes())

    def test_workspace_icons_local_persistence_validation_and_shared_proxy(self):
        local = '/v1/workspaces/icon?workspace_id='+LOCAL
        def call(path, method='GET', body=None):
            with self.s.tx():
                return self.api.dispatch(path,method,body,True)
        self.assertEqual(call(local)['icon'], '')
        self.assertEqual(call(local,'POST',{'icon':'rocket','revision':0})['revision'], 1)
        self.assertEqual(call('/v1/workspaces')['workspaces'][0]['icon'], 'rocket')
        with self.assertRaises(Problem):call(local,'POST',{'icon':'leaf','revision':0})
        with self.assertRaises(Problem):call(local,'POST',{'icon':'../file','revision':1})
        path = '/v1/workspaces/icon?workspace_id='+self.remote[0]['id']
        with patch('alshitasks.portal.Portal.token', return_value='fixture'), patch('alshitasks.portal.remote', return_value={'icon':'leaf','revision':2,'can_manage':True}) as remote:
            self.assertEqual(call(path,'POST',{'icon':'leaf','revision':1})['icon'],'leaf')
            self.assertEqual(remote.call_args.args[1], '/desktop/v1/workspaces/'+self.remote[0]['id']+'/icon')
        rows=call('/v1/workspaces')['workspaces']
        self.assertEqual(next(w for w in rows if w['id']==self.remote[0]['id'])['icon'],'leaf')
        with self.s.tx():self.s.db.execute("UPDATE connections SET state='revoked' WHERE id=?", (self.ids[0],))
        self.assertFalse(call(path)['can_manage'])
        with self.assertRaises(Problem):call(path,'POST',{'icon':'home','revision':2})

    def test_discovery_refreshes_existing_connection_capabilities(self):
        import json

        with self.s.tx():
            self.s.db.execute("UPDATE connections SET capabilities='{}'")
        self.discover()
        for row in self.s.db.execute("SELECT capabilities FROM connections"):
            self.assertIn("graph-sync-v2", json.loads(row[0])["features"])

    def test_account_navigation_replaced_on_activation(self):
        self.s.set_setting('account_navigation', [{'label':'Old admin','url':'/staff/users/'}])
        activate(self.s, self.account, {'account_id':'7'})
        self.assertEqual(self.s.setting('account_navigation'), [])

    def test_catalog_keeps_summary_metadata_and_local_identity(self):
        self.remote[0].update(kind='internal', settings_url='/workspace/settings/', unread=3, neural_labs_url='/workspace/labs/')
        self.discover()
        from alshitasks.workspaces import catalog
        rows = catalog(self.s)
        self.assertEqual(rows[0]['id'], LOCAL)
        remote = next(r for r in rows if r.get('portal_workspace_id') == self.remote[0]['id'])
        self.assertEqual(remote['id'], self.remote[0]['id'])
        self.assertIn(remote['connection_id'], self.ids)
        self.assertEqual((remote['kind'], remote['unread']), ('internal', 3))
        self.assertEqual(remote['settings_url'], '/workspace/settings/')
        self.assertEqual(remote['neural_labs_url'], '/workspace/labs/')
        self.s.db.execute("UPDATE connections SET state='disconnected'")
        self.assertFalse(any(r.get('neural_labs_url') for r in catalog(self.s)))

    def create(self, workspace, title="Board"):
        with self.s.tx(), scope(self.s, workspace):
            return self.s.create({"kind": "board", "data": {"title": title}, "idempotency_key": uid()})

    def test_workspace_reads_and_mutations_are_scoped_before_pagination(self):
        local = self.create(LOCAL)
        a, b = [self.create(w) for w in self.ids]
        with self.s.tx():
            rows = self.api.dispatch("/v1/projects/items?workspace_id=" + self.ids[0], owner=True)
            self.assertEqual([r["id"] for r in rows["items"]], [a["id"]])
            for ident in (local["id"], b["id"]):
                with self.assertRaises(Problem):
                    self.api.dispatch(
                        "/v1/projects/items/" + ident + "?workspace_id=" + self.ids[0], owner=True
                    )
                with self.assertRaises(Problem):
                    self.api.tool(
                        "project_update",
                        {
                            "workspace_id": self.ids[0],
                            "id": ident,
                            "revision": 1,
                            "data": {"title": "No"},
                            "idempotency_key": uid(),
                        },
                        owner=True,
                    )
            self.assertEqual(len(self.api.tool("workspace_list", {}, owner=True)["workspaces"]), 3)
            self.assertEqual(
                len(self.api.tool("project_boards", {"workspace_id": LOCAL}, owner=True)["items"]), 1
            )

    def test_discovery_idempotent_new_access_revocation_and_network_failure(self):
        self.discover()
        self.assertEqual(
            len(self.s.db.execute("SELECT * FROM connections WHERE workspace!=''").fetchall()), 2
        )
        self.remote.pop()
        self.discover()
        self.assertEqual(
            self.s.db.execute("SELECT COUNT(*) FROM connections WHERE state='revoked'").fetchone()[0], 1
        )
        with self.s.tx():
            self.s.set_setting("discovery", {"next": 0})
        with patch("alshitasks.portal.Portal.token", return_value="fixture"):

            def fail(*a):
                raise Problem("Offline", 503)

            discover(self.s, fail)
        self.assertEqual(
            self.s.db.execute("SELECT COUNT(*) FROM connections WHERE state='revoked'").fetchone()[0], 1
        )
        self.assertEqual(self.s.setting("discovery")["state"], "offline")

    def test_signout_retains_data_and_relogin_identity(self):
        board = self.create(self.ids[0])
        with self.s.tx(), patch("alshitasks.accounts.Secrets.delete"):
            signout(self.s)
        self.assertEqual(self.s.item(board["id"])["data"]["title"], "Board")
        self.assertIsNone(self.s.setting("active_account"))
        with self.s.tx():
            self.s.db.execute("UPDATE connections SET state='connected' WHERE id=?", (self.account,))
            activate(self.s, self.account, {"account_id": "7"})
        self.discover()
        self.assertEqual(
            self.s.db.execute("SELECT connection FROM items WHERE id=?", (board["id"],)).fetchone()[0],
            self.ids[0],
        )

    def test_portal_workspace_uuid_and_legacy_id_resolve_same_records(self):
        connection = self.ids[0]
        portal_id = self.s.db.execute('SELECT workspace FROM connections WHERE id=?', (connection,)).fetchone()[0]
        board = self.create(connection)
        with self.s.tx():
            canonical = self.api.tool('project_list', {'workspace_id': portal_id}, owner=True)
            legacy = self.api.tool('project_list', {'workspace_id': connection}, owner=True)
            self.assertEqual(canonical, legacy)
            self.assertEqual(canonical['items'][0]['id'], board['id'])
            self.assertEqual(self.s.db.execute('SELECT connection FROM items WHERE id=?', (board['id'],)).fetchone()[0], connection)
