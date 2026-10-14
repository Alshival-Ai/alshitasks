"""Offline artwork, permission boundaries and network race regression tests."""
from test_workspaces import Workspaces
from unittest.mock import patch
from alshitasks import workspace_icons as icons
from alshitasks.store import Problem, Store
from alshitasks.paths import Paths
from alshitasks.accounts import signout


class IconSync(Workspaces):
    def setUp(self):
        super().setUp()
        self.remote[0].update(icon='heart', icon_color='#123456', icon_revision=4)
        self.remote[0]['scopes'].append('board:write')
        self.discover()
        self.ident = self.ids[0]
        self.path = '/v1/workspaces/icon?workspace_id=' + self.remote[0]['id']
        self.shared = dict(icon='heart', icon_color='#123456', revision=4, can_manage=True)
        self.posts = []

    def get(self, body=None):
        with self.s.tx():
            return self.api.dispatch(self.path, 'GET' if body is None else 'POST', body, True)

    def save(self, **extra):
        return self.get(dict(icon='rocket', icon_color='#abcdef', revision=self.get()['revision'], **extra))

    def transport(self, origin, path, token, body=None):
        if body is not None:
            self.posts.append(body)
            self.assertEqual(body['revision'], self.shared['revision'])
            self.shared.update(body, revision=body['revision'] + 1)
        return dict(self.shared)

    def sync(self, transport=None):
        connection = dict(self.s.db.execute('SELECT * FROM connections WHERE id=?', (self.ident,)).fetchone())
        with patch('alshitasks.portal.Portal.token', return_value='fixture'):
            icons.sync_one(self.s, connection, transport or self.transport)

    def retry(self):
        with self.s.tx():
            value = icons.selection(self.s, self.ident)
            value['next'] = 0
            self.s.set_setting('workspace_art:' + self.ident, value)

    def test_offline_discovery_restart_and_sync(self):
        self.assertEqual(self.save()['sync_state'], 'pending')
        self.sync(lambda *args: (_ for _ in ()).throw(Problem('offline', 503)))
        self.discover()
        self.assertEqual(self.get()['icon'], 'rocket')
        self.s.db.close()
        self.s = Store(Paths(self.temp.name))
        self.api.store = self.s
        self.assertEqual(self.get()['icon'], 'rocket')
        self.retry()
        self.sync()
        self.assertEqual(self.get()['sync_state'], 'synced')
        self.assertEqual(len(self.posts), 1)
        self.discover()  # An older in-flight directory response must not undo the acknowledged write.
        self.assertEqual(self.get()['icon'], 'rocket')

    def test_read_only_customization_needs_explicit_publish(self):
        self.remote[0]['scopes'].remove('board:write')
        self.discover()
        self.assertEqual(self.save()['sync_state'], 'local')
        self.remote[0]['scopes'].append('board:write')
        self.discover()
        self.sync()
        self.assertEqual(self.posts, [])
        self.get({'action':'sync', 'revision':self.get()['revision']})
        self.sync()
        self.assertEqual(self.get()['sync_state'], 'synced')

    def test_conflict_keeps_local_until_explicit_choice(self):
        self.save()
        self.shared.update(icon='star', revision=5)
        self.sync()
        self.assertEqual(self.get()['sync_state'], 'conflict')
        self.assertEqual(self.get()['icon'], 'rocket')
        self.assertEqual(self.posts, [])
        self.get({'action':'sync', 'revision':self.get()['revision']})
        self.sync()
        self.assertEqual(self.shared['icon'], 'rocket')

    def test_use_portal_and_keep_local(self):
        self.save()
        self.shared.update(icon='star', revision=5)
        self.sync()
        self.get({'action':'keep_local', 'revision':self.get()['revision']})
        self.discover()
        self.assertEqual(self.get()['icon'], 'rocket')
        self.get({'action':'use_portal', 'revision':self.get()['revision']})
        self.assertEqual(self.get()['icon'], 'star')

    def test_lost_response_is_acknowledged_without_duplicate_write(self):
        self.save()
        def lost(*args):
            result = self.transport(*args)
            if len(args) == 4:
                raise Problem('lost', 503)
            return result
        self.sync(lost)
        self.retry()
        self.sync()
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.get()['sync_state'], 'synced')

    def test_permission_rejection_does_not_revoke_tasks_connection(self):
        self.save()
        self.sync(lambda *args: (_ for _ in ()).throw(Problem('denied', 403)))
        self.assertEqual(self.get()['sync_state'], 'local')
        self.assertEqual(self.s.db.execute('SELECT state FROM connections WHERE id=?', (self.ident,)).fetchone()[0], 'connected')

    def test_newer_edit_survives_inflight_write(self):
        self.save()
        def edit(*args):
            result = self.transport(*args)
            if len(args) == 4:
                self.get({'icon':'leaf', 'revision':self.get()['revision']})
            return result
        self.sync(edit)
        self.assertEqual(self.get()['icon'], 'leaf')
        self.assertEqual(self.get()['sync_state'], 'pending')
        self.sync()
        self.assertEqual(self.shared['icon'], 'leaf')

    def test_signout_during_fetch_prevents_publish(self):
        self.save()
        def logout(*args):
            with self.s.tx(), patch('alshitasks.security.Secrets.delete'):
                signout(self.s)
            return dict(self.shared)
        self.sync(logout)
        self.assertEqual(self.posts, [])
        self.assertEqual(self.get()['icon'], 'rocket')

    def test_unauthenticated_and_rate_limited_retry_retain_edits(self):
        for status in (401, 429, 500):
            self.save()
            self.sync(lambda *args: (_ for _ in ()).throw(Problem('unavailable', status)))
            self.assertEqual(self.get()['sync_state'], 'pending')
            self.assertEqual(self.get()['icon'], 'rocket')
            self.retry()
