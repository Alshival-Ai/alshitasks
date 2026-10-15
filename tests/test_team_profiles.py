import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from alshitasks.api import API
from alshitasks.store import Store, Problem, uid
from alshitasks.security import grant_create
from alshitasks.team import save, members, actor, principal
from alshitasks.workspaces import LOCAL
from alshitasks.mcp_parity import call


class TeamProfiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(SimpleNamespace(data=Path(self.tmp.name)))
        self.addCleanup(self.store.db.close)
        self.api = API(self.store, None)
        with self.store.tx():
            self.droid = save(self.store, {'name': 'Build droid', 'kind': 'droid'})
            self.reviewer = save(self.store, {'name': 'Review droid', 'kind': 'droid'})
            self.key = grant_create(self.store, {'name': 'Worker', 'profile_id': self.droid['id'], 'scopes': ['project:read', 'project:write']})
            self.review_key = grant_create(self.store, {'name': 'Reviewer', 'profile_id': self.reviewer['id'], 'scopes': ['project:read', 'project:write', 'project:approve']})

    def invoke(self, name, key=None, **args):
        if name in ('project_create', 'project_update', 'project_action'):
            args.setdefault('idempotency_key', uid())
        with self.store.tx():
            return call(self.api, name, args, token=(key or self.key)['token'])

    def test_identity_assignment_filters_and_audit(self):
        identity = self.invoke('project_identity')['actor']
        self.assertEqual(identity['id'], self.droid['id'])
        self.assertEqual(identity['credential_id'], self.key['id'])
        item = self.invoke('project_create', kind='task', data={'title': 'Build it', 'assignee': self.droid['id'], 'reviewer': self.reviewer['id']})
        self.assertEqual(self.store.item(item['id'])['author_id'], self.droid['id'])
        self.assertEqual(len(self.invoke('project_list', assignee=self.droid['id'])['items']), 1)
        self.assertEqual(self.invoke('project_list', assignee=self.reviewer['id'])['items'], [])
        self.assertEqual(len(self.invoke('project_list', reviewer=self.reviewer['id'])['items']), 1)
        audit = json.loads(self.store.db.execute('SELECT actor FROM event_actors ORDER BY sequence DESC').fetchone()[0])
        self.assertEqual(audit['id'], self.droid['id'])
        self.assertEqual(actor()['id'], 'local')

    def test_deactivation_revokes_all_keys_and_keeps_assignments(self):
        item = self.invoke('project_create', kind='task', data={'title': 'Keep history', 'assignee': self.droid['id']})
        with self.store.tx():
            rotated = grant_create(self.store, {'name': 'Rotation', 'profile_id': self.droid['id'], 'scopes': ['project:read']})
            inactive = save(self.store, {'active': False, 'revision': self.droid['revision']}, self.droid['id'])
        for key in (self.key, rotated):
            with self.assertRaises(Problem):
                self.invoke('project_identity', key)
        self.assertEqual(self.store.item(item['id'])['data']['assignee'], self.droid['id'])
        with self.store.tx():
            save(self.store, {'active': True, 'revision': inactive['revision']}, self.droid['id'])
        with self.assertRaises(Problem):
            self.invoke('project_identity')

    def test_workspace_scope_and_owner_only_management(self):
        with self.assertRaises(Problem):
            self.invoke('project_members', workspace_id=uid())
        with self.assertRaises(Problem):
            self.api.dispatch('/v1/team', 'POST', {'name': 'Escalation'}, token=self.key['token'])
        with self.assertRaises(Problem):
            self.api.dispatch('/v1/settings', 'PATCH', {'local_members': []}, token=self.key['token'])
        self.assertIsNone(self.store.workspace)
        self.assertEqual(actor()['id'], 'local')

    def test_review_cannot_be_bypassed_by_status_or_assignment(self):
        item = self.invoke('project_create', kind='task', data={'title': 'Review', 'assignee': self.droid['id'], 'reviewer': self.reviewer['id'], 'state': 'review'})
        done = next(s for s in self.store.statuses() if s['category'] == 'done')
        for data in ({'state': 'done'}, {'state': 'done', 'reviewer': None}, {'status_id': done['id']}):
            with self.assertRaises(Problem):
                self.invoke('project_update', id=item['id'], revision=item['revision'], data=data)
        approved = self.invoke('project_action', self.review_key, id=item['id'], revision=item['revision'], action='approve')
        self.assertEqual(approved['data']['state'], 'done')
        with self.assertRaises(Problem):
            self.invoke('project_create', kind='task', data={'title': 'No self review', 'assignee': self.droid['id'], 'reviewer': self.droid['id']})

    def test_request_context_is_restored_after_failure(self):
        with principal(self.store, False, self.key['token']):
            self.assertEqual(self.store.workspace, LOCAL)
            with self.assertRaises(Problem):
                with principal(self.store, False, 'bad'):
                    pass
            self.assertEqual(actor()['id'], self.droid['id'])
        self.assertEqual(actor()['id'], 'local')
