import sys
import tempfile
import time
import os
import json
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
from alshitasks import managed
from alshitasks.accounts import activate, signout
from alshitasks.paths import Paths
from alshitasks.portal import Portal
from alshitasks.store import Store, Problem


class ManagedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Paths(self.temp.name))
        self.store.paths.managed = True  # Explicit synthetic enrollment fixture.
        self.value = dict(status='ready', version=1, account_id='7', resource=managed.ORIGIN + '/desktop/v1', token='fixture', generation='one', expires_at=time.time()+3600)
        self.mock = patch('alshitasks.managed.snapshot', side_effect=lambda: self.value)
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.db.close)

    def reconcile(self, account='7'):
        self.store.db.commit()
        with self.store.tx():
            self.store.set_setting('managed_next', 0)
        managed.reconcile(self.store, lambda *a: {'account_id': account, 'resource': self.value['resource']})

    def test_no_keyring_and_rotation_preserves_connection(self):
        with patch('alshitasks.security.Secrets.get', side_effect=AssertionError('keyring')):
            self.reconcile()
            account = self.store.setting('active_account')
            connection = Portal(self.store).connection(account['connection'])
            self.assertEqual(Portal(self.store).token(connection), 'fixture')
            self.value.update(token='rotated', generation='two')
            self.reconcile()
            self.assertEqual(account, self.store.setting('active_account'))
            self.assertEqual(Portal(self.store).token(connection), 'rotated')
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM connections').fetchone()[0], 1)

    def test_unchanged_enrollment_does_not_repeat_network_discovery(self):
        self.reconcile()
        with self.store.tx():
            self.store.set_setting('managed_next', 0)
            self.store.set_setting('discovery', {'next':time.time()+300, 'capabilities_at':time.time()+3600})
        expected = self.store.setting('discovery')
        with patch('alshitasks.portal.remote', side_effect=AssertionError('unexpected network')):
            managed.reconcile(self.store)
        self.assertEqual(self.store.setting('discovery'), expected)

    def test_other_account_retained_and_fenced(self):
        self.store.db.execute('INSERT INTO connections VALUES(?,?,?,?,?,?,?)', ('old', managed.ORIGIN, '8', '', 'connected', None, '{}'))
        activate(self.store, 'old', {'account_id': '8'})
        self.reconcile()
        self.assertEqual(self.store.setting('active_account')['id'], '7')
        self.assertEqual(Portal(self.store).connection('old')['state'], 'disconnected')
        with self.assertRaises(Problem):
            Portal(self.store).token(Portal(self.store).connection('old'))
        with self.assertRaises(Problem):
            signout(self.store)

    def test_wrong_identity_cannot_activate(self):
        self.reconcile('8')
        self.assertIsNone(self.store.setting('active_account'))

    def test_revocation_blocks_token_and_retains_account(self):
        self.reconcile()
        c = Portal(self.store).connection(self.store.setting('active_account')['connection'])
        self.value = {'status': 'revoked'}
        with self.assertRaises(Problem):
            Portal(self.store).token(c)
        self.assertIsNotNone(self.store.setting('active_account'))

    def test_public_install_unchanged(self):
        self.value = None
        with patch.object(managed, 'ROOT', Path(self.temp.name) / 'unprovisioned'):
            managed.reconcile(self.store)
            self.assertFalse(managed.enabled(self.store))

    def test_explicit_root_never_uses_host_enrollment(self):
        self.store.paths.managed = False
        managed.reconcile(self.store)
        self.assertFalse(managed.enabled(self.store))
        self.assertIsNone(self.store.setting('active_account'))

    def test_worker_stops_after_account_transition(self):
        self.reconcile()
        c = Portal(self.store).connection(self.store.setting('active_account')['connection'])
        def remote(*args):
            with self.store.tx():
                self.store.set_setting('authorization_generation', 99)
            return {'items': []}
        transport, _ = managed.guarded_transport(self.store, c, remote)
        with self.assertRaises(Problem):
            transport('fixture')


@unittest.skipUnless(hasattr(os, 'getuid') and os.getuid() == 0, 'root-owned Linux fixture')
class ManagedFileTests(unittest.TestCase):
    def test_valid_tampered_symlink_expired_and_permissions(self):
        with tempfile.TemporaryDirectory(dir='/root') as tmp:
            root = Path(tmp)
            path = root / '0'
            path.mkdir()
            value = dict(status='ready', version=1, uid=0, account_id='7', machine_id='fixture', generation='one', resource=managed.ORIGIN+'/desktop/v1', token='fixture', expires_at=time.time()+3600)
            def write():
                raw = json.dumps(value).encode()
                (path/'credential.json').write_bytes(raw)
                (path/'credential.json').chmod(0o400)
                (path/'identity.json').write_text(json.dumps(dict(value, token=None, credential_sha256=hashlib.sha256(raw).hexdigest())))
                (path/'identity.json').chmod(0o444)
            with patch.object(managed, 'ROOT', root):
                write()
                self.assertEqual(managed.snapshot()['token'], 'fixture')
                (path/'credential.json').write_text('{}')
                self.assertEqual(managed.snapshot()['status'], 'unavailable')
                write()
                (path/'credential.json').chmod(0o444)
                self.assertEqual(managed.snapshot()['status'], 'unavailable')
                (path/'credential.json').unlink()
                (path/'credential.json').symlink_to(path/'identity.json')
                self.assertEqual(managed.snapshot()['status'], 'unavailable')
                (path/'credential.json').unlink()
                value['expires_at'] = 0
                write()
                self.assertEqual(managed.snapshot()['status'], 'unavailable')
