import io
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_sync
from alshitasks.store import uid, now, Problem
from alshitasks import live_sync, sync_worker


class LiveSync(unittest.TestCase):
    setUp = test_sync.SyncAcceptance.setUp
    tearDown = test_sync.SyncAcceptance.tearDown

    def rows(self, count=8):
        rows = [dict(self.c, cursor=now())]
        with self.s.tx():
            self.s.db.execute('UPDATE connections SET cursor=?', (rows[0]['cursor'],))
            for _ in range(count - 1):
                row = dict(rows[0], id=uid(), workspace=uid())
                self.s.db.execute('INSERT INTO connections VALUES(:id,:origin,:account,:workspace,:state,:cursor,:capabilities)', row)
                rows.append(row)
        return rows

    def test_all_workspaces_notified_and_duplicate_revision_coalesced(self):
        rows = self.rows()
        payload = {'workspaces': [{'id': r['workspace'], 'revisions': {'project': 2}} for r in rows]}
        payload['workspaces'].append({'id': uid(), 'revisions': {'project': 2}})
        with self.s.tx():
            live_sync.mark_changes(self.s, self.c, payload)
            live_sync.mark_changes(self.s, self.c, payload)
        for row in rows:
            self.assertEqual(self.s.setting('sync_notice:' + row['id']), 1)
            self.assertEqual(sync_worker.due(self.s, row), 'urgent')

    def test_notice_during_sync_survives_acknowledgement(self):
        with self.s.tx():
            live_sync.mark_changes(self.s, self.c)
        def during(*args):
            with self.s.tx():
                live_sync.mark_changes(self.s, self.c)
        with patch.object(sync_worker, 'sync_connection', side_effect=during):
            sync_worker.sync_one(self.s, self.c)
        self.assertEqual(self.s.setting('sync_ack:' + self.c['id']), 1)
        self.assertEqual(self.s.setting('sync_notice:' + self.c['id']), 2)
        self.assertEqual(sync_worker.due(self.s, self.c), 'urgent')

    def test_failure_does_not_acknowledge_and_respects_retry_after(self):
        error = Problem('Rate limited', 429)
        error.retry_after = 120
        with self.s.tx():
            live_sync.mark_changes(self.s, self.c)
        with patch.object(sync_worker, 'sync_connection', side_effect=error):
            sync_worker.sync_one(self.s, self.c)
        self.assertIsNone(self.s.setting('sync_ack:' + self.c['id']))
        with self.s.tx():
            live_sync.mark_changes(self.s, self.c, recovered=True)
        self.assertGreater(self.s.setting('sync:' + self.c['id'])['next'], time.time() + 119)
        self.assertIsNone(sync_worker.due(self.s, self.c))

    def test_reconnect_resyncs_all_and_only_clears_network_backoff(self):
        rows = self.rows()
        with self.s.tx():
            for i, row in enumerate(rows):
                self.s.set_setting('sync:' + row['id'], {'status': 503 if i == 0 else 403, 'next': time.time() + 200})
            live_sync.mark_changes(self.s, self.c, recovered=True)
        self.assertEqual(self.s.setting('sync:' + rows[0]['id'])['next'], 0)
        self.assertGreater(self.s.setting('sync:' + rows[1]['id'])['next'], time.time())
        self.assertTrue(all(self.s.setting('sync_notice:' + r['id']) == 1 for r in rows))

    def test_fair_scheduler_reserves_poll_and_does_not_starve_eighth(self):
        rows = self.rows()
        with self.s.tx():
            for row in rows[:-1]:
                self.s.set_setting('sync_notice:' + row['id'], 1)
            self.s.set_setting('sync:' + rows[-1]['id'], {'poll_at': 0})
            scheduler = sync_worker.Scheduler()
            chosen = [scheduler.choose(self.s, rows, {})['id'] for _ in range(12)]
        self.assertEqual(chosen[2], rows[-1]['id'])
        self.assertEqual(set(chosen), {r['id'] for r in rows})

    def test_parser_handles_comments_multiline_and_bounds(self):
        stream = io.BytesIO(b': heartbeat\r\n\r\nevent: changed\ndata: {"workspaces":\ndata: []}\n\n')
        self.assertEqual(list(live_sync.frames(stream)), [('heartbeat', {}), ('changed', {'workspaces': []})])
        with self.assertRaises(ValueError):
            list(live_sync.frames(io.BytesIO(b'data: ' + b'x' * 65537)))
        self.assertEqual(live_sync.retry_after('12'), 12)
        self.assertEqual(live_sync.retry_after('invalid'), 0)

    def test_account_listener_delivers_eighth_workspace_to_graph_loop(self):
        rows = self.rows()
        stop, received = threading.Event(), threading.Event()
        eighth = rows[-1]
        with self.s.tx():
            self.s.set_setting('active_account', {'id': self.c['account'], 'origin': self.c['origin'], 'connection': self.c['id']})
        class Stream(io.BytesIO):
            headers = SimpleNamespace(get_content_type=lambda: 'text/event-stream')
        stream = Stream(('event: changed\ndata: ' + json.dumps({'workspaces': [{'id': eighth['workspace'], 'revisions': {'project': 3}}]}) + '\n\n').encode())
        def synced(store, connection):
            if connection['id'] == eighth['id']:
                received.set()
            with store.tx():
                store.db.execute('UPDATE connections SET cursor=? WHERE id=?', (now(), connection['id']))
        started = time.monotonic()
        with patch.object(live_sync.Portal, 'token', return_value='fixture'), patch('alshitasks.managed.guarded_transport', return_value=(None, lambda: None)), patch.object(live_sync.urllib.request, 'build_opener', return_value=SimpleNamespace(open=lambda *a, **k: stream)), patch.object(sync_worker, 'sync_connection', side_effect=synced), patch('alshitasks.accounts.discover'):
            graph = threading.Thread(target=sync_worker.graph_loop, args=(SimpleNamespace(store=self.s), stop))
            listener = threading.Thread(target=live_sync.listen, args=(self.s, self.c, stop, 0))
            graph.start(); listener.start()
            try:
                self.assertTrue(received.wait(5), 'Eighth workspace did not receive live sync')
                self.assertLess(time.monotonic() - started, 5)
            finally:
                stop.set(); self.s.sync_wake.set()
                listener.join(5); graph.join(5)
            self.assertFalse(listener.is_alive())
            self.assertFalse(graph.is_alive())

    def test_account_manager_uses_one_listener_for_eight_workspaces(self):
        rows = self.rows()
        with self.s.tx():
            self.s.set_setting('active_account', {'id': self.c['account'], 'origin': self.c['origin'], 'connection': self.c['id']})
            self.s.db.execute('UPDATE connections SET capabilities=?', (json.dumps({'features': [live_sync.FEATURE]}),))
        stop, entered, cancelled = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def listen(store, connection, cancel, generation, account_wide):
            calls.append((connection['id'], account_wide))
            entered.set()
            cancel.wait(5)
            cancelled.set()
        with patch.object(live_sync, 'listen', side_effect=listen):
            manager = threading.Thread(target=live_sync.notification_loop, args=(SimpleNamespace(store=self.s), stop))
            manager.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(calls, [(self.c['id'], True)])
                with self.s.tx():
                    self.s.set_setting('active_account', None)
                self.assertTrue(cancelled.wait(2))
            finally:
                stop.set(); manager.join(3)
        self.assertFalse(manager.is_alive())

    def test_cancellation_interrupts_blocked_network_read(self):
        import http.server
        import urllib.request
        release = threading.Event()
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(b': heartbeat\n\n'); self.wfile.flush()
                release.wait(5)
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        cancel, heartbeat = live_sync.ListenerStop(), threading.Event()
        try:
            with urllib.request.urlopen('http://127.0.0.1:' + str(server.server_port), timeout=45) as response:
                cancel.attach(response)
                def read():
                    try:
                        for name, data in live_sync.frames(response):
                            heartbeat.set()
                    except OSError:
                        pass
                reader = threading.Thread(target=read)
                reader.start()
                self.assertTrue(heartbeat.wait(2))
                cancel.set(); reader.join(2)
                self.assertFalse(reader.is_alive(), 'Cancelled socket reader remained blocked')
        finally:
            release.set(); server.shutdown(); server.server_close(); thread.join(2)

    def test_signout_generation_fences_listener(self):
        with self.s.tx():
            self.s.set_setting('active_account', {'id': self.c['account'], 'origin': self.c['origin'], 'connection': self.c['id']})
            self.assertTrue(live_sync.valid(self.s, self.c, 0))
            self.s.set_setting('authorization_generation', 1)
            self.assertFalse(live_sync.valid(self.s, self.c, 0))
            self.s.set_setting('active_account', None)
            self.assertFalse(live_sync.valid(self.s, self.c, 1))
