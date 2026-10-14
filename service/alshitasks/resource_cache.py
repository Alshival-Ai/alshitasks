"""Bounded asynchronous cache for optional portal resource views."""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from .store import Problem


def read(store, path, query):
    from .workspaces import require_item
    from .portal import Portal
    require_item(store, query.get('resource_id'))
    row = store.db.execute("SELECT connection,remote_id FROM items WHERE id=? AND kind='resource'", (query.get('resource_id'),)).fetchone()
    if not row or not row['connection']:
        raise Problem('Choose a connected workspace resource')
    connection = Portal(store).connection(row['connection'])
    from .eligibility import capture
    generation = capture(store, connection)
    filters = {k: v for k, v in query.items() if k in ('since', 'until', 'severity', 'text', 'start', 'end', 'q', 'trace_id', 'cursor') and v}
    filters.update(resource_id=row['remote_id'], limit=min(200, max(1, int(query.get('limit', 100)))))
    kind = path.rsplit('/', 1)[1]
    route = '/desktop/v1/workspaces/' + connection['workspace'] + '/' + kind + '?' + urlencode(filters)
    key = 'portal_cache:' + hashlib.sha256((connection['id'] + route).encode()).hexdigest()
    state = store.setting(key, {})
    stale = state.get('updated_at', 0) < time.time() - 30 or state.get('generation') != generation
    jobs = getattr(store, 'resource_refreshes', None)
    if jobs is None:
        jobs = store.resource_refreshes = {}
    if stale and state.get('next_at', 0) <= time.time() and len(jobs) < 64:
        jobs.setdefault(key, (connection, generation, route, kind, row['remote_id']))
    result = dict(state.get('data') or ({'resources': []} if kind == 'monitoring' else {'items': [], 'events': [], 'next_cursor': None}))
    result['cache'] = {'stale': stale, 'updated_at': state.get('updated_at'), 'pending': key in jobs,
                       'state': 'unavailable' if state.get('error') else 'loading' if not state.get('data') else 'cached',
                       'error': state.get('error', '')}
    return result


def refresh(store, key, job):
    from .portal import Portal, remote
    from .eligibility import check
    from .managed import guarded_transport
    connection, generation, route, kind, resource = job
    try:
        check(store, connection, generation)
        send, validate = guarded_transport(store, connection, remote)
        token = Portal(store).token(connection)
        result = send(connection['origin'], route, token)
        if kind == 'monitoring':
            result = {'resources': [r for r in result['resources'] if r['id'] == resource]}
        with store.tx():
            check(store, connection, generation)
            validate()
            store.set_setting(key, {'data': result, 'updated_at': time.time(), 'generation': generation})
    except Exception:
        with store.tx():
            try:
                check(store, connection, generation)
            except Problem:
                return
            state = store.setting(key, {})
            state.update(error='Portal view temporarily unavailable', next_at=time.time() + 30)
            store.set_setting(key, state)
    finally:
        with store.lock:
            getattr(store, 'resource_refreshes', {}).pop(key, None)


def resource_cache_loop(api, stop):
    active = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='portal-views') as pool:
        while not stop.wait(0.25):
            active = {k: f for k, f in active.items() if not f.done()}
            with api.store.lock:
                for key, job in list(getattr(api.store, 'resource_refreshes', {}).items()):
                    if len(active) >= 4:
                        break
                    if key not in active:
                        active[key] = pool.submit(refresh, api.store, key, job)
