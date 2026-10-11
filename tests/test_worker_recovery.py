import concurrent.futures
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_sync
from alshitasks import server
from alshitasks.portal import Portal


class WorkerRecovery(unittest.TestCase):
    setUp = test_sync.SyncAcceptance.setUp
    tearDown = test_sync.SyncAcceptance.tearDown

    def test_concurrent_settings_reads_do_not_mix_cached_statement_results(self):
        with self.s.tx():
            for i in range(8):
                self.s.set_setting('reader:' + str(i), {'worker': i})

        def reader(i):
            for _ in range(3000):
                self.assertEqual(self.s.setting('reader:' + str(i)), {'worker': i})

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(reader, range(8)))

    def test_worker_restarts_after_unexpected_failure_without_logging_content(self):
        stop = threading.Event()
        api = SimpleNamespace(store=self.s, stopping=False)
        attempts = []

        def worker(api, stop):
            attempts.append(True)
            if len(attempts) == 1:
                raise TypeError('private fixture content')
            self.assertEqual(api.store.setting('worker:worker')['state'], 'running')
            stop.set()

        with self.assertLogs(server.__name__, level='WARNING') as logs:
            server.supervise(api, stop, worker)
        self.assertEqual(len(attempts), 2)
        self.assertNotIn('private fixture content', ''.join(logs.output))

    def test_managed_account_detection_never_reverses_credential_lock_order(self):
        def detect(store):
            self.assertFalse(store.token_lock._is_owned())
            return True

        with patch('alshitasks.managed.enabled', side_effect=detect), patch('alshitasks.managed.token', return_value='fixture'):
            self.assertEqual(Portal(self.s).token(self.c), 'fixture')

    def test_retrying_worker_is_not_presented_as_up_to_date(self):
        from alshitasks.workspaces import catalog
        with self.s.tx():
            self.s.set_setting('active_account', {'id': self.c['account'], 'origin': self.c['origin']})
            self.s.set_setting('sync:' + self.c['id'], {'state': 'up-to-date'})
            self.s.set_setting('worker:graph_loop', {'state': 'retrying'})
            row = next(r for r in catalog(self.s) if r['id'] == self.c['id'])
        self.assertEqual(row['sync'], 'offline')
        self.assertIn('retrying', row['error'])
