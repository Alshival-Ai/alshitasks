import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
from alshitasks import accounts, health, monitoring, notifications, resource_cache
from alshitasks.api import API
from alshitasks.logs import Logs
from alshitasks.paths import Paths
from alshitasks.portal import Portal
from alshitasks.requests import handle
from alshitasks.store import Store, Problem, uid, now


class OptionalPortal(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = Paths(self.temp.name)
        self.store = Store(self.paths)
        self.logs = Logs(self.paths)
        self.api = API(self.store, self.logs)
        self.connection = dict(id=uid(), origin='https://alshival.ai', account='fixture',
                               workspace=uid(), state='connected', cursor=None, capabilities='{}')
        with self.store.tx():
            self.resource = self.store.create({'kind': 'resource', 'data': {'title': 'Local'}, 'idempotency_key': uid()})['id']

    def tearDown(self):
        self.logs.db.close()
        self.store.db.close()
        self.temp.cleanup()

    def connect(self):
        with self.store.tx():
            self.store.db.execute('INSERT INTO connections VALUES(:id,:origin,:account,:workspace,:state,:cursor,:capabilities)', self.connection)
            self.store.set_setting('active_account', {'id': 'fixture', 'origin': self.connection['origin'], 'connection': self.connection['id']})

    def portal_item(self):
        self.connect()
        ident = uid()
        key = notifications.account(self.store)[1]
        with self.store.tx():
            self.store.db.execute("INSERT INTO app_notifications(id,source,account,remote_id,event_key,title,created_at) VALUES(?,?,?,?,?,?,?)", (ident, 'portal', key, uid(), uid(), 'Cached alert', now()))
            self.store.set_setting('notification_portal:' + key, {'authorized': True, 'generation': 0, 'last_success': time.time(), 'stale': False, 'workspaces': []})
        return ident

    def call(self, path, method='GET', body=None):
        return handle(self.api, path, method, body or {}, True, '')

    def local_write(self):
        return self.call('/v1/projects/items', 'POST', {'kind': 'note', 'data': {'title': 'Responsive'}, 'idempotency_key': uid()})

    def held(self, operation, during):
        entered, release = threading.Event(), threading.Event()
        errors = []
        def block(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Blocked request was not released')
            return {'updated': 1}
        def run():
            try:
                operation(block)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            done = threading.Event()
            def local():
                try:
                    during()
                except Exception as exc:
                    errors.append(exc)
                finally:
                    done.set()
            local_thread = threading.Thread(target=local)
            local_thread.start()
            self.assertTrue(done.wait(2), 'Local work waited for the external request')
        finally:
            release.set()
            worker.join(5)
            if 'local_thread' in locals():
                local_thread.join(5)
        self.assertFalse(worker.is_alive())
        if errors:
            raise errors[0]

    def test_notification_write_does_not_wait_and_ack_preserves_newer_action(self):
        ident = self.portal_item()
        self.call('/v1/notifications/actions', 'POST', {'id': ident, 'action': 'read'})
        def operation(block):
            with patch('alshitasks.portal.Portal.token', return_value='fixture'), patch('alshitasks.portal.remote', side_effect=block):
                notifications.flush_actions(self.store)
        def during():
            self.call('/v1/notifications/actions', 'POST', {'id': ident, 'action': 'unread'})
            self.local_write()
            self.assertFalse(self.call('/v1/notifications')['items'][0]['read'])
        self.held(operation, during)
        job = self.store.db.execute('SELECT * FROM notification_actions').fetchone()
        self.assertEqual(job['action'], 'unread')
        self.assertEqual(job['attempts'], 0)

    def test_mixed_read_all_is_local_and_queue_survives_restart(self):
        ident = self.portal_item()
        with self.store.tx():
            notifications.create(self.store, 'Local alert', '', 'test')
        with patch('alshitasks.portal.remote', side_effect=AssertionError('No network on local actions')):
            self.assertEqual(self.call('/v1/notifications/actions', 'POST', {'action': 'read_all'})['updated'], 2)
            self.call('/v1/notifications/actions', 'POST', {'id': ident, 'action': 'archive'})
            self.assertEqual(self.call('/v1/notifications')['unread_count'], 0)
        self.store.db.close()
        self.store = Store(self.paths)
        self.api = API(self.store, self.logs)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM notification_actions').fetchone()[0], 2)
        with patch('alshitasks.portal.Portal.token', return_value='fixture'), patch('alshitasks.portal.remote', side_effect=Problem('Lost response', 503)):
            notifications.flush_actions(self.store)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM notification_actions').fetchone()[0], 2)

    def test_refresh_preserves_pending_actions_and_agents_omit_stale_cache(self):
        ident = self.portal_item()
        row = self.store.db.execute('SELECT * FROM app_notifications WHERE id=?', (ident,)).fetchone()
        # Use the same deterministic identity as a real portal refresh.
        import uuid
        canonical = str(uuid.uuid5(uuid.NAMESPACE_URL, row['account'] + ':notification:' + row['remote_id']))
        with self.store.tx():
            self.store.db.execute('UPDATE app_notifications SET id=? WHERE id=?', (canonical, ident))
        self.call('/v1/notifications/actions', 'POST', {'id': canonical, 'action': 'read'})
        result = {'items': [{'id': row['remote_id'], 'workspace_id': None, 'event_type': 'test', 'title': 'Remote', 'body': '', 'destination': '', 'created_at': now(), 'read': False, 'archived': False}], 'workspaces': [], 'next_page': None}
        with patch('alshitasks.portal.Portal.token', return_value='fixture'), patch('alshitasks.portal.remote', return_value=result):
            notifications._sync_page(self.store, True)
        self.assertTrue(self.call('/v1/notifications')['items'][0]['read'])
        with self.store.tx():
            key = 'notification_portal:' + row['account']
            state = self.store.setting(key)
            state.update(last_success=0, stale=True)
            self.store.set_setting(key, state)
        with patch('alshitasks.portal.remote', side_effect=AssertionError('Agent reads must not wait')):
            self.assertEqual(self.call('/v1/notifications?agent_read=1')['items'], [])
            self.assertTrue(self.call('/v1/notifications/refresh', 'POST')['scheduled'])

    def test_disconnect_during_notification_request_never_acknowledges_old_generation(self):
        ident = self.portal_item()
        self.call('/v1/notifications/actions', 'POST', {'id': ident, 'action': 'read'})
        def operation(block):
            with patch('alshitasks.portal.Portal.token', return_value='fixture'), patch('alshitasks.portal.remote', side_effect=block):
                notifications.flush_actions(self.store)
        def during():
            self.call('/v1/portal/signout', 'POST')
            self.local_write()
        self.held(operation, during)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM notification_actions').fetchone()[0], 1)
        self.assertEqual(self.call('/v1/notifications')['items'], [])
        with self.store.tx():
            other = dict(self.connection, id=uid(), account='other')
            self.store.db.execute('INSERT INTO connections VALUES(:id,:origin,:account,:workspace,:state,:cursor,:capabilities)', other)
            self.store.set_setting('active_account', {'id': 'other', 'origin': other['origin'], 'connection': other['id']})
        with patch('alshitasks.portal.remote', side_effect=AssertionError('Cross-account replay')):
            notifications.flush_actions(self.store)

    def test_forwarding_request_and_credentials_do_not_hold_database(self):
        with self.store.tx():
            self.store.db.execute('INSERT INTO forwarding VALUES(?,?,?,?,1)', ('target', 'https://example.com/logs', self.resource, 'key'))
            monitoring.queue_forward(self.store, self.resource, [{'id': uid(), 'message': 'event'}])
        def operation(block):
            response = MagicMock()
            response.__enter__.return_value.status = 200
            def send(*args, **kwargs):
                block()
                return response
            with patch('alshitasks.security.Secrets.get', return_value='fixture'), patch('urllib.request.OpenerDirector.open', side_effect=send):
                monitoring.forward_once(self.store)
        self.held(operation, self.local_write)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM forward_queue').fetchone()[0], 0)

    def test_slow_refresh_token_does_not_hold_database_or_block_signout(self):
        self.connect()
        def operation(block):
            def retrieve(*args):
                block()
                return json.dumps({'access_token': 'fixture', 'expires_at': time.time() + 300})
            with patch('alshitasks.security.Secrets.get', side_effect=retrieve):
                with self.assertRaises(Problem):
                    Portal(self.store).token(self.connection)
        self.held(operation, lambda: self.call('/v1/portal/signout', 'POST'))

    def test_pause_migration_disables_configured_collectors_and_rejects_legacy_mutation(self):
        with self.store.tx():
            health.save(self.store, {'resource_id': self.resource, 'name': 'Check', 'config': {'kind': 'heartbeat'}})
            self.store.db.execute('INSERT INTO forwarding VALUES(?,?,?,?,1)', ('target', 'https://example.com/logs', self.resource, 'key'))
            self.store.set_setting('paused', True)
            self.store.set_setting('pause_retired', False)
        self.store.db.close()
        self.store = Store(self.paths)
        self.api = API(self.store, self.logs)
        self.assertEqual(self.store.db.execute('SELECT enabled FROM health_checks').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT enabled FROM forwarding').fetchone()[0], 0)
        self.assertFalse(self.call('/v1/settings')['paused'])
        self.assertIn('Re-enable', self.call('/v1/settings')['pause_migration'])
        with self.assertRaises(Problem) as error:
            self.call('/v1/settings', 'PATCH', {'paused': False})
        self.assertEqual(error.exception.status, 410)

    def test_portal_view_schedules_without_io_and_late_response_is_discarded(self):
        self.connect()
        with self.store.tx():
            self.store.db.execute('UPDATE items SET connection=?,remote_id=? WHERE id=?', (self.connection['id'], uid(), self.resource))
        path = '/v1/portal/logs?resource_id=' + self.resource
        with patch('alshitasks.portal.remote', side_effect=AssertionError('Cache reads must not wait')):
            result = self.call(path)
            self.assertEqual(result['cache']['state'], 'loading')
        key, job = next(iter(self.store.resource_refreshes.items()))
        def operation(block):
            with patch('alshitasks.portal.Portal.token', return_value='fixture'), patch('alshitasks.portal.remote', side_effect=block):
                resource_cache.refresh(self.store, key, job)
        self.held(operation, lambda: self.call('/v1/portal/signout', 'POST'))
        self.assertIsNone(self.store.setting(key))


if __name__ == '__main__':
    unittest.main()
