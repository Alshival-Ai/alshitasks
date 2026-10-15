"""Outbound SSE hints, isolated from durable checkpoint-based graph delivery."""
import json
import os
import random
import socket
import threading
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime

from .monitoring import NoRedirect
from .store import Problem
from .portal import Portal

FEATURE = 'graph-account-events-v1'


class ListenerStop(threading.Event):
    """Interrupt a blocked urllib read when account ownership or pause changes."""
    response = None

    def attach(self, response):
        self.response = response
        if self.is_set():
            self.set()

    def set(self):
        super().set()
        # HTTPResponse.close can wait on the reader's buffer lock. Shutdown wakes
        # that reader first; its context manager then closes the response normally.
        raw = getattr(getattr(self.response, 'fp', None), 'raw', None)
        sock = getattr(raw, '_sock', None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
                if os.name == 'nt':
                    # Winsock's timeout select can remain blocked after shutdown.
                    # Detach prevents a later buffered close from closing a reused handle.
                    socket.close(sock.detach())
            except OSError:
                pass


def retry_after(value):
    try:
        return max(0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return 0


def frames(stream):
    """Bounded SSE parser; heartbeats prove liveness without scheduling work."""
    name, data, size = 'message', [], 0
    while True:
        raw = stream.readline(65537)
        if not raw:
            return
        size += len(raw)
        if len(raw) > 65536 or size > 262144:
            raise ValueError('Event exceeds bounds')
        line = raw.decode('utf-8').rstrip('\r\n')
        if not line:
            if data:
                yield name, json.loads('\n'.join(data))
            else:
                yield 'heartbeat', {}
            name, data, size = 'message', [], 0
        elif line.startswith('event:'):
            name = line[6:].strip()
        elif line.startswith('data:'):
            data.append(line[5:].lstrip(' '))


def mark_changes(store, connection, payload=None, *, recovered=False):
    rows = store.db.execute("SELECT * FROM connections WHERE origin=? AND account=? AND state='connected' AND workspace!=''", (connection['origin'], connection['account'])).fetchall()
    hints = None if payload is None else {str(w['id']): w['revisions'] for w in payload['workspaces']}
    for row in rows:
        ident = row['id']
        if hints is not None:
            if row['workspace'] not in hints:
                continue
            revision = hints[row['workspace']]
            if store.setting('live_revision:' + ident) == revision:
                continue
            store.set_setting('live_revision:' + ident, revision)
        store.set_setting('sync_notice:' + ident, int(store.setting('sync_notice:' + ident, 0)) + 1)
        if recovered:
            schedule = store.setting('sync:' + ident, {})
            if schedule.get('status') in (502, 503, 504):
                schedule['next'] = 0
                store.set_setting('sync:' + ident, schedule)
    store.sync_wake.set()


def live_key(store, connection):
    return 'live:' + store.setting('credential:' + connection['id'], connection['id'])


def set_health(store, connection, **values):
    key = live_key(store, connection)
    old = store.setting(key, {})
    if any(old.get(k) != v for k, v in values.items()):
        store.set_setting(key, dict(old, **values))


def listen(store, connection, stop, generation, account_wide=True):
    from .managed import guarded_transport
    attempt = 0
    while not stop.is_set():
        wait = 0
        try:
            with store.lock:
                if not valid(store, connection, generation):
                    return
            token = Portal(store).token(connection)
            _, check = guarded_transport(store, connection, lambda: None)
            path = '/desktop/v1/events' if account_wide else '/desktop/v1/workspaces/' + connection['workspace'] + '/events'
            request = urllib.request.Request(connection['origin'] + path, headers={'Authorization': 'Bearer ' + token, 'Accept': 'text/event-stream', 'X-Alshival-Desktop-Features': 'efficient-sync-v1'})
            check()
            with urllib.request.build_opener(NoRedirect).open(request, timeout=45) as stream:
                if isinstance(stop, ListenerStop):
                    stop.attach(stream)
                if stream.headers.get_content_type() != 'text/event-stream':
                    raise ValueError('Expected event stream')
                first = True
                for name, payload in frames(stream):
                    if stop.is_set():
                        return
                    with store.tx():
                        if not valid(store, connection, generation):
                            return
                        check()
                        if name in ('reconnect', 'revoked'):
                            if name == 'revoked':
                                store.set_setting('discovery', {'next': 0})
                            break
                        if first:
                            from .sync_schedule import FEATURE as efficient
                            if efficient not in json.loads(connection['capabilities']).get('features', []):
                                mark_changes(store, connection, recovered=True)
                            # Refresh capability identity on transport recovery, once per stream.
                            discovery = store.setting('discovery', {})
                            discovery.update(next=0, capabilities_at=0)
                            store.set_setting('discovery', discovery)
                            first = False
                        set_health(store, connection, state='live', heartbeat=time.time(), next_retry=None)
                        if name == 'discovery':
                            discovery = store.setting('discovery', {})
                            discovery['next'] = 0
                            store.set_setting('discovery', discovery)
                            store.sync_wake.set()
                        elif name == 'notifications':
                            from .notifications import account
                            _, key = account(store)
                            state = store.setting('notification_portal:' + key, {})
                            store.set_setting('notification_notice:' + key, int(store.setting('notification_notice:' + key, 0)) + 1)
                            state['next'] = 0
                            store.set_setting('notification_portal:' + key, state)
                        elif name == 'changed':
                            if account_wide:
                                mark_changes(store, connection, payload)
                            else:
                                store.set_setting('sync_notice:' + connection['id'], int(store.setting('sync_notice:' + connection['id'], 0)) + 1)
                                store.sync_wake.set()
                            set_health(store, connection, last_notification=time.time())
                    attempt = 0
        except urllib.error.HTTPError as error:
            wait = retry_after(error.headers.get('Retry-After'))
            error.close()
        except Exception:
            pass  # Diagnostics below are deliberately content- and credential-free.
        if stop.is_set():
            return
        attempt += 1
        delay = max(wait, min(30, random.uniform(0.75, 1.25) * 2 ** min(attempt, 5)))
        with store.tx():
            if not valid(store, connection, generation):
                return
            set_health(store, connection, state='reconnecting', next_retry=time.time() + delay)
        stop.wait(delay)


def valid(store, connection, generation):
    from .eligibility import check
    try:
        check(store, connection, generation)
        return True
    except Problem:
        return False


def notification_loop(api, stop):
    store, active = api.store, {}
    try:
        while not stop.is_set():
            desired = {}
            with store.lock:
                account = store.setting('active_account')
                generation = store.setting('authorization_generation', 0)
                if account:
                    root = Portal(store).connection(account['connection'])
                    if FEATURE in json.loads(root['capabilities']).get('features', []):
                        desired[(root['id'], generation, True)] = (root, True)
                    else:
                        # Legacy endpoints are rotated fairly; all copies also reconcile over HTTP.
                        rows = [dict(r) for r in store.db.execute("SELECT * FROM connections WHERE state='connected' AND origin=? AND account=? AND workspace!='' ORDER BY id", (account['origin'], account['id']))
                                if 'graph-events-v2' in json.loads(r['capabilities']).get('features', [])]
                        if rows:
                            start = (int(time.monotonic() // 30) * 4) % len(rows)
                            for row in (rows[start:] + rows[:start])[:4]:
                                desired[(row['id'], generation, False)] = (row, False)
            for ident, (thread, cancel) in list(active.items()):
                if ident not in desired or not thread.is_alive():
                    cancel.set()
                    del active[ident]
            for ident, (connection, account_wide) in desired.items():
                if ident not in active:
                    cancel = ListenerStop()
                    thread = threading.Thread(target=listen, args=(store, connection, cancel, generation, account_wide), daemon=True, name='portal-events')
                    active[ident] = (thread, cancel)
                    thread.start()
            stop.wait(0.5)
    finally:
        for thread, cancel in active.values():
            cancel.set()
