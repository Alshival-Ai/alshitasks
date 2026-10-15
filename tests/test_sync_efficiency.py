import unittest
import time
import test_sync
from alshitasks import sync_worker, live_sync


class CadenceTests(unittest.TestCase):
    setUp = test_sync.SyncAcceptance.setUp
    tearDown = test_sync.SyncAcceptance.tearDown

    def test_live_idle_waits_but_stale_stream_falls_back_within_minute(self):
        with self.s.tx():
            self.s.set_setting(live_sync.live_key(self.s, self.c), {'state': 'live', 'heartbeat': time.time()})
            self.s.set_setting('sync:' + self.c['id'], {'poll_at': time.time()+300, 'last_success': time.time()-56})
        self.assertIsNone(sync_worker.due(self.s, self.c))
        with self.s.tx():
            self.s.set_setting(live_sync.live_key(self.s, self.c), {'state':'live', 'heartbeat':time.time()-41})
        self.assertEqual(sync_worker.due(self.s, self.c), 'poll')

    def test_queued_edits_ignore_long_idle_poll_deadline(self):
        with self.s.tx():
            self.s.set_setting('sync:' + self.c['id'], {'poll_at':time.time()+300})
            self.s.set_setting('sync_notice:' + self.c['id'], 1)
        self.assertEqual(sync_worker.due(self.s, self.c), 'urgent')

    def test_backoff_is_preserved_even_with_stale_events(self):
        with self.s.tx():
            self.s.set_setting('sync:' + self.c['id'], {'next':time.time()+120, 'poll_at':0})
        self.assertIsNone(sync_worker.due(self.s, self.c))
