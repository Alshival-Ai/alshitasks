"""Bounded asynchronous cache for optional portal resource views."""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from .store import Problem


def read(store, path, query):
    from .workspaces import require_item
    from .portal import Portal
    require_item(store, query.get('resource_id'))
    row = store.db.execute("SELECT connection,remote_id FROM items WHERE id=? AND kind='resource'", (query.get('resource_id'),)).fetchone()
    if row and not row['connection'] and path == '/v1/portal/logs':
        from .log_permissions import empty
        return empty('unknown')
    if not row or not row['connection']:
        raise Problem('Choose a connected workspace resource')
    connection = Portal(store).connection(row['connection'])
    from . import log_permissions
    kind = path.rsplit('/', 1)[1]
    log_epoch = None
    if kind == 'logs':
        access = log_permissions.availability(store, connection)
        if access != 'available':
            if access == 'unknown':
                log_permissions.schedule_discovery(store)
            return log_permissions.empty(access)
        log_epoch = log_permissions.epoch(store, connection)
    from .eligibility import capture
    generation = capture(store, connection)
    filters = {k: v for k, v in query.items() if k in ('since', 'until', 'severity', 'text', 'start', 'end', 'q', 'trace_id', 'cursor') and v}
    filters.update(resource_id=row['remote_id'], limit=min(200, max(1, int(query.get('limit', 100)))))
    route = '/desktop/v1/workspaces/' + connection['workspace'] + '/' + kind + '?' + urlencode(filters)
    key = 'portal_cache:' + hashlib.sha256((connection['id'] + route).encode()).hexdigest()
    if kind == 'logs':
        key = 'portal_logs_cache:' + connection['id'] + ':' + key.rsplit(':', 1)[1]
    state = store.setting(key, {})
    if state.get('generation') != generation:
        state = {}
    stale = state.get('updated_at', 0) < time.time() - 30 or state.get('generation') != generation
    jobs = getattr(store, 'resource_refreshes', None)
    if jobs is None:
        jobs = store.resource_refreshes = {}
    if stale and state.get('next_at', 0) <= time.time() and len(jobs) < 64:
        jobs.setdefault(key, (connection, generation, route, kind, row['remote_id'], log_epoch))
    result = dict(state.get('data') or ({'resources': []} if kind == 'monitoring' else {'items': [], 'events': [], 'next_cursor': None}))
    result['cache'] = {'stale': stale, 'updated_at': state.get('updated_at'), 'pending': key in jobs,
                       'state': 'unavailable' if state.get('error') else 'loading' if not state.get('data') else 'cached',
                       'error': state.get('error', '')}
    if kind == 'logs':
        result['portal_logs'] = 'available'
    return result


def refresh(store, key, job):
    from .portal import Portal, remote
    from .eligibility import check
    from .managed import guarded_transport
    from . import log_permissions
    connection, generation, route, kind, resource, log_epoch = job
    def permitted():
        check(store, connection, generation)
        if kind == 'logs':
            log_permissions.check(store, connection, log_epoch)
    try:
        with store.lock:
            permitted()
        send, validate = guarded_transport(store, connection, remote)
        token = Portal(store).token(connection)
        with store.lock:
            permitted()
        result = send(connection['origin'], route, token)
        if kind == 'monitoring':
            result = {'resources': [r for r in result['resources'] if r['id'] == resource]}
        with store.tx():
            permitted()
            validate()
            store.set_setting(key, {'data': result, 'updated_at': time.time(), 'generation': generation})
    except Exception as error:
        with store.tx():
            try:
                permitted()
            except Problem:
                return
            if kind == 'logs' and isinstance(error, Problem) and error.status in (401, 403):
                log_permissions.denied(store, connection, error.status)
                return
            state = store.setting(key, {})
            if isinstance(error, Problem) and error.status in (401, 403, 404):
                state.pop('data', None)
                state.pop('updated_at', None)
            state.update(error='Portal view temporarily unavailable', next_at=time.time() + 30, generation=generation)
            store.set_setting(key, state)
    finally:
        with store.lock:
            jobs = getattr(store, 'resource_refreshes', {})
            if jobs.get(key) is job:
                jobs.pop(key, None)


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
