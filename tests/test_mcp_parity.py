import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ContractParity(unittest.TestCase):
    def test_canonical_project_schemas_match_pinned_portal_contract(self):
        source = ROOT / 'contracts/portal-project-tools.v1.json'
        provenance = json.loads((ROOT / 'contracts/portal-project-tools.provenance.json').read_text())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), provenance['sha256'])
        portal = json.loads(source.read_text())['tools']
        local = {t['name']: t for t in json.loads((ROOT / 'contracts/project-tools.v1.json').read_text())['tools']}
        for tool in portal:
            self.assertEqual(local[tool['name']], tool, tool['name'])


import test_sync
from alshitasks.api import API
from alshitasks.mcp_parity import call
from alshitasks.portal import local_id
from alshitasks.store import Problem, uid
from alshitasks.workspaces import scope


class RecordParity(unittest.TestCase):
    def setUp(self):
        self.fixture = test_sync.SyncAcceptance()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.s = self.fixture.s
        self.c = self.fixture.c
        self.s.set_setting('active_account', {'origin': self.c['origin'], 'id': self.c['account'], 'connection': self.c['id']})
        self.s.set_setting('workspace:' + self.c['id'], {'name': 'Fixture', 'scopes': ['project:read', 'project:write']})
        self.s.db.commit()
        self.fixture.sync()
        self.api = API(self.s, None)

    def call(self, name, **args):
        with self.s.tx():
            return call(self.api, name, dict(workspace_id=self.c['workspace'], **args), owner=True)

    def test_read_write_and_retry_use_portal_ids_without_rewriting_cache(self):
        remote = self.fixture.record['id']
        cached = local_id(self.c['id'], remote)
        item = self.call('project_get', id=remote)
        self.assertEqual(item['id'], remote)
        self.assertEqual(item['data']['board_id'], self.fixture.board['id'])
        self.assertNotIn('author_id', item)
        args = dict(id=remote, revision=item['revision'], data={'title': 'Updated'}, idempotency_key=uid())
        updated = self.call('project_update', **args)
        self.assertEqual(updated['id'], remote)
        self.assertEqual(self.call('project_update', **args), updated)
        self.assertEqual(self.s.item(cached)['data']['title'], 'Updated')
        self.assertEqual(self.s.db.execute('SELECT connection FROM items WHERE id=?', (cached,)).fetchone()[0], self.c['id'])
        with self.assertRaises(Problem):
            call(self.api, 'project_get', {'id':remote}, owner=True)
        with self.assertRaises(Problem):
            call(self.api, 'project_get', {'workspace_id':uid(), 'id':remote}, owner=True)

    def test_canonical_pagination_and_output_schema(self):
        expected = sorted(r['id'] for r in self.fixture.records)
        first = self.call('project_list', limit=1)
        self.assertEqual(first['items'][0]['id'], expected[0])
        self.assertEqual(first['next'], expected[0])
        second = self.call('project_list', limit=1, after=first['next'])
        self.assertEqual(second['items'][0]['id'], expected[1])
        self.assertIsNone(second['next'])
        self.assertNotIn('author_id', first['items'][0])

    def test_member_ids_and_assignments_use_portal_identity(self):
        member = 'member:' + uid()
        self.s.set_setting('members:' + self.c['id'], [{'id':member, 'name':'Teammate', 'email':'fixture@example.invalid'}])
        self.s.set_setting('member_remote:' + self.c['id'] + ':' + member, '42')
        self.s.db.commit()
        members = self.call('project_members')
        self.assertEqual(members['items'][0]['id'], '42')
        self.assertEqual(self.call('project_members', after='42')['items'], [])
        item = self.call('project_get', id=self.fixture.record['id'])
        changed = self.call('project_update', id=item['id'], revision=item['revision'], data={'assignee':'42'}, idempotency_key=uid())
        self.assertEqual(changed['data']['assignee'], '42')

    def test_catalog_exposes_portal_workspace_uuid(self):
        result = call(self.api, 'workspace_list', {}, owner=True)
        self.assertIn(self.c['workspace'], [w['id'] for w in result['workspaces']])
        self.assertNotIn(self.c['id'], [w['id'] for w in result['workspaces']])


    def test_graph_status_and_comment_references_are_canonical(self):
        import copy
        from alshitasks.portal import sync_connection
        edge, status, comment = uid(), uid(), uid()
        records = copy.deepcopy(self.fixture.records)
        records[1]['data']['status_id'] = status
        records.append(dict(id=comment, kind='comment', revision=1,
                            data=dict(records[1]['data'], parent_id=self.fixture.record['id'], title='Comment')))
        def snapshot(*args, **kwargs):
            return dict(items=records, complete=True, next=None,
                        statuses=[dict(id=status, board_id=self.fixture.board['id'], name='Todo',
                                       category='todo', color='#112233', position=0, is_default=True,
                                       retired=False, legacy_state="todo", revision=1)],
                        edges=[dict(id=edge, source_id=self.fixture.board['id'],
                                    target_id=self.fixture.record['id'], kind='related', revision=1,
                                    created_at='2026-10-12T00:00:00Z')])
        sync_connection(self.s, self.c, snapshot, token='fixture')
        edges = self.call('project_edges')['edges']
        self.assertEqual(edges[0]['id'], edge)
        self.assertEqual(edges[0]['source_id'], self.fixture.board['id'])
        self.assertEqual(edges[0]['target_id'], self.fixture.record['id'])
        statuses = self.call('project_statuses', board_id=self.fixture.board['id'])
        self.assertEqual(statuses['statuses'][0]['id'], status)
        comments = self.call('project_comments', id=self.fixture.record['id'])
        self.assertEqual(comments['items'][0]['id'], comment)
        self.assertEqual(comments['items'][0]['data']['parent_id'], self.fixture.record['id'])
        removed = self.call('project_unlink', id=edge, revision=edges[0]['revision'], idempotency_key=uid())
        self.assertEqual(removed['id'], edge)
        self.assertTrue(removed['deleted'])
