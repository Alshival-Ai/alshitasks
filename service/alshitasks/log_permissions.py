"""Account-scoped discovery is the sole authority for optional portal log reads."""
from .eligibility import capture, AuthorizationChanged
from .store import Problem


def availability(store, connection):
    if not connection:
        return 'unknown'
    with store.lock:
        try:
            capture(store, connection)
        except Problem:
            return 'unknown'
        blocked = store.setting('portal_logs_blocked:' + connection['id'])
        if blocked:
            return blocked
        metadata = store.setting('workspace:' + connection['id'])
        if not isinstance(metadata, dict) or str(metadata.get('id')) != connection['workspace']:
            return 'unknown'
        scopes = metadata.get('scopes')
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            return 'unknown'
        return 'available' if 'logs:read' in scopes else 'restricted'


def schedule_discovery(store, immediate=False):
    # Normal reads respect discovery's offline backoff. A new denial wakes it once.
    if store.setting('active_account'):
        schedule = store.setting('discovery', {})
        if immediate or 'next' not in schedule:
            store.set_setting('discovery', dict(schedule, next=0))
        store.sync_wake.set()


def epoch(store, connection):
    return store.setting('portal_logs_epoch:' + connection['id'], 0)


def check(store, connection, expected):
    if availability(store, connection) != 'available' or epoch(store, connection) != expected:
        raise AuthorizationChanged('Workspace log access changed', 409)


def invalidate(store, connection):
    ident = connection['id']
    store.set_setting('portal_logs_epoch:' + ident, epoch(store, connection) + 1)
    store.db.execute('DELETE FROM settings WHERE key LIKE ?', ('portal_logs_cache:' + ident + ':%',))
    jobs = getattr(store, 'resource_refreshes', {})
    for key, job in list(jobs.items()):
        if job[0]['id'] == ident and job[3] == 'logs':
            jobs.pop(key, None)


def discovered(store, connection):
    # Called only after a complete successful discovery has committed its metadata.
    store.set_setting('portal_logs_blocked:' + connection['id'], None)
    if availability(store, connection) != 'available':
        invalidate(store, connection)


def denied(store, connection, status):
    invalidate(store, connection)
    store.set_setting('portal_logs_blocked:' + connection['id'], 'restricted' if status == 403 else 'unknown')
    schedule_discovery(store, immediate=True)


def empty(state):
    return {'items': [], 'events': [], 'next_cursor': None, 'portal_logs': state,
            'cache': {'stale': False, 'updated_at': None, 'pending': False,
                      'state': state, 'error': ''}}
