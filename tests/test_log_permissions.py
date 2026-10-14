import threading
import time
import unittest
from unittest.mock import patch

from test_optional_portal import OptionalPortal
from alshitasks import accounts, log_permissions, resource_cache
from alshitasks.store import Problem, uid, now


class LogPermissions(unittest.TestCase):
    setUp = OptionalPortal.setUp
    tearDown = OptionalPortal.tearDown
    connect = OptionalPortal.connect
    call = OptionalPortal.call
    local_write = OptionalPortal.local_write
    held = OptionalPortal.held

    def fixture(self, scopes=None):
        self.connect()
        with self.store.tx():
            self.store.db.execute('UPDATE items SET connection=?,remote_id=? WHERE id=?', (self.connection['id'], uid(), self.resource))
            if scopes is not None:
                self.store.set_setting('workspace:' + self.connection['id'], {'id': self.connection['workspace'], 'scopes': scopes})
        self.path = '/v1/portal/logs?resource_id=' + self.resource
        self.setup = '/v1/logs/setup?resource_id=' + self.resource

    def discovery(self, scopes):
        with self.store.tx():
            self.store.set_setting('discovery', {'next': 0})
        def transport(origin, route, token):
            if route.endswith('/capabilities'):
                return {'resource': origin + '/desktop/v1', 'account_id': 'fixture', 'scopes': ['logs:read']}
            return {'workspaces': [{'id': self.connection['workspace'], 'name': 'Fixture', 'scopes': scopes}]}
        with patch('alshitasks.portal.Portal.token', return_value='fixture'):
            accounts.discover(self.store, transport)

    def queued(self):
        self.assertEqual(self.call(self.path)['portal_logs'], 'available')
        return next(iter(self.store.resource_refreshes.items()))

    def test_free_and_unknown_never_fetch_credentials_or_queue_logs(self):
        self.fixture([])
        with patch('alshitasks.portal.Portal.token') as token, patch('alshitasks.portal.remote') as remote:
            for scopes, expected in [([], 'restricted'), (None, 'unknown')]:
                with self.store.tx():
                    self.store.set_setting('workspace:' + self.connection['id'], None if scopes is None else {'id':self.connection['workspace'], 'scopes':scopes})
                for _ in range(4):
                    self.assertEqual(self.call(self.setup)['portal_logs'], expected)
                    result = self.call(self.path)
                    self.assertEqual(result, log_permissions.empty(expected))
                self.assertFalse(getattr(self.store, 'resource_refreshes', {}))
            token.assert_not_called()
            remote.assert_not_called()
        self.assertEqual(self.store.setting('discovery')['next'], 0)

    def test_missing_metadata_offline_discovery_retains_backoff(self):
        self.fixture()
        self.call(self.path)
        with patch('alshitasks.portal.Portal.token', return_value='fixture'):
            accounts.discover(self.store, lambda *a: (_ for _ in ()).throw(Problem('Offline', 503)))
        schedule = self.store.setting('discovery')
        for _ in range(4):
            self.assertEqual(self.call(self.path)['portal_logs'], 'unknown')
            self.call(self.setup)
        self.assertEqual(self.store.setting('discovery'), schedule)
        self.assertEqual(schedule['state'], 'offline')

    def test_upgrade_downgrade_invalidates_cache_and_queue(self):
        self.fixture([])
        self.discovery(['project:read', 'logs:read'])
        key, job = self.queued()
        with patch('alshitasks.portal.Portal.token', return_value='fixture'), patch('alshitasks.portal.remote', return_value={'items':[{'message':'Cloud'}]}):
            resource_cache.refresh(self.store, key, job)
        self.assertEqual(self.call(self.path)['items'][0]['message'], 'Cloud')
        self.discovery(['project:read'])
        self.assertIsNone(self.store.setting(key))
        self.assertFalse(self.store.resource_refreshes)
        self.assertEqual(self.call(self.path), log_permissions.empty('restricted'))
        self.discovery(['project:read', 'logs:read'])
        self.assertEqual(self.call(self.path)['items'], [])
        self.assertTrue(self.store.resource_refreshes)

    def test_queued_work_rechecks_before_credentials(self):
        self.fixture(['logs:read'])
        key, job = self.queued()
        self.discovery(['project:read'])
        with patch('alshitasks.portal.Portal.token') as token, patch('alshitasks.portal.remote') as remote:
            resource_cache.refresh(self.store, key, job)
            token.assert_not_called()
            remote.assert_not_called()

    def test_permission_removed_during_credential_read_prevents_send(self):
        self.fixture(['logs:read'])
        key, job = self.queued()
        def credential(_):
            with self.store.tx():
                self.store.set_setting('workspace:' + self.connection['id'], {'id': self.connection['workspace'], 'scopes': []})
            return 'fixture'
        with patch('alshitasks.portal.Portal.token', side_effect=credential), patch('alshitasks.portal.remote') as remote:
            resource_cache.refresh(self.store, key, job)
            remote.assert_not_called()

    def test_held_request_cannot_restore_after_downgrade_and_reupgrade(self):
        self.fixture(['logs:read'])
        key, job = self.queued()
        def operation(block):
            with patch('alshitasks.portal.Portal.token', return_value='fixture'), patch('alshitasks.portal.remote', side_effect=block):
                resource_cache.refresh(self.store, key, job)
        def during():
            self.discovery(['project:read'])
            self.discovery(['project:read', 'logs:read'])
            self.local_write()
            self.logs.ingest(self.resource, [{'id':uid(),'timestamp':now(),'severity':'info','message':'Still local','attributes':{}}])
            self.assertEqual(self.call('/v1/logs?resource_id=' + self.resource)['events'][0]['message'], 'Still local')
            self.queued()
        self.held(operation, during)
        self.assertIsNone(self.store.setting(key))
        self.assertIn(key, self.store.resource_refreshes, 'Old completion must not remove the new job')

    def test_403_suppresses_until_successful_discovery(self):
        self.fixture(['logs:read'])
        key, job = self.queued()
        with patch('alshitasks.portal.Portal.token', return_value='fixture'), patch('alshitasks.portal.remote', side_effect=Problem('Forbidden',403)):
            resource_cache.refresh(self.store, key, job)
        for _ in range(4):
            self.assertEqual(self.call(self.path), log_permissions.empty('restricted'))
        with patch('alshitasks.portal.Portal.token', return_value='fixture'):
            accounts.discover(self.store, lambda *a: (_ for _ in ()).throw(Problem('Offline',503)))
        self.assertEqual(self.call(self.path)['portal_logs'], 'restricted')
        self.discovery(['project:read', 'logs:read'])
        self.assertTrue(self.call(self.path)['cache']['pending'])

    def test_network_failure_remains_distinct_from_authorization(self):
        self.fixture(['logs:read'])
        key, job = self.queued()
        with patch('alshitasks.portal.Portal.token', return_value='fixture'), patch('alshitasks.portal.remote', side_effect=Problem('Offline',503)):
            resource_cache.refresh(self.store, key, job)
        value=self.call(self.path)
        self.assertEqual(value['portal_logs'], 'available')
        self.assertTrue(value['cache']['error'])
        self.assertFalse(value['cache']['pending'])
        with self.store.tx():
            self.store.set_setting(key, {})
        key, job = self.queued()
        with patch('alshitasks.portal.Portal.token', side_effect=Problem('Unauthorized',401)):
            resource_cache.refresh(self.store, key, job)
        self.assertEqual(self.call(self.path), log_permissions.empty('unknown'))
        self.assertEqual(self.store.setting('discovery')['next'], 0)

    def test_account_change_blocks_old_jobs(self):
        self.fixture(['logs:read'])
        key, job = self.queued()
        with self.store.tx():
            self.store.set_setting('active_account', {'id':'other','origin':self.connection['origin'],'connection':self.connection['id']})
        with patch('alshitasks.portal.Portal.token') as token:
            resource_cache.refresh(self.store, key, job)
            token.assert_not_called()
        self.assertIsNone(self.store.setting(key))
