"""Local artwork with a durable, independent portal synchronization queue."""

import time
from .store import Problem
from .workspaces import LOCAL
from .workspace_art import appearance, color, defaults, ICON_IDS


def art(value):
    return {key: value[key] for key in ('icon', 'icon_color')}


def local_selection(store):
    value = store.setting('local_workspace_icon', {'revision': 0})
    return dict(value, **appearance(LOCAL, value.get('icon'), value.get('icon_color')))


def selection(store, ident):
    if ident == LOCAL:
        return dict(local_selection(store), sync_state='local')
    value = store.setting('workspace_art:' + ident)
    if value is not None:
        return value
    meta = store.setting('workspace:' + ident, {})
    row = store.db.execute('SELECT workspace FROM connections WHERE id=?', (ident,)).fetchone()
    artwork = appearance(row['workspace'], meta.get('icon'), meta.get('icon_color'))
    return dict(artwork, revision=0, base=dict(artwork, revision=meta.get('icon_revision', 0)),
                sync_state='synced', next=0)


def receive(store, ident, meta):
    """Discovery updates the remote baseline, never an unsynchronized local choice."""
    value = selection(store, ident)
    incoming = dict(appearance(meta['id'], meta.get('icon'), meta.get('icon_color')),
                    revision=meta.get('icon_revision', 0))
    if incoming['revision'] < max(value.get('remote', {}).get('revision', 0), value['base']['revision']):
        return
    value['remote'] = incoming
    if value['sync_state'] == 'synced':
        if art(value) != art(incoming):
            value['revision'] += 1
        value.update(incoming, revision=value['revision'], base=incoming)
    store.set_setting('workspace_art:' + ident, value)


def can_sync(store, ident):
    return 'board:write' in store.setting('workspace:' + ident, {}).get('scopes', [])


def dispatch(store, method, body, owner):
    if not owner:
        raise Problem('Desktop owner authorization required', 403)
    ident = store.workspace
    if ident is None:
        raise Problem('Choose a workspace')
    if method not in ('GET', 'POST'):
        raise Problem('Unsupported icon operation', 405)
    value = selection(store, ident)
    row = None if ident == LOCAL else store.db.execute('SELECT * FROM connections WHERE id=?', (ident,)).fetchone()
    identity = LOCAL if row is None else row['workspace']
    if method == 'POST':
        if body.get('revision') != value['revision']:
            raise Problem('The icon changed. Reopen settings and try again.', 409)
        action = body.get('action', 'save')
        if action not in ('save', 'use_portal', 'keep_local', 'sync'):
            raise Problem('Unknown icon action')
        if action == 'use_portal':
            if not value.get('remote'):
                raise Problem('No portal icon has been received yet', 409)
            value.update(art(value['remote']), base=value['remote'], sync_state='synced')
        elif action == 'keep_local':
            value['sync_state'] = 'local'
        else:
            if action == 'sync' and (ident == LOCAL or not can_sync(store, ident)):
                raise Problem('Workspace management permission is required to sync', 403)
            if action == 'save':
                icon = body.get('icon')
                if not isinstance(icon, str) or icon not in {'', *ICON_IDS}:
                    raise Problem('Choose an icon from the workspace icon pack')
                try:
                    artwork = defaults(identity) if body.get('reset') or not icon else {
                        'icon': icon, 'icon_color': color(body.get('icon_color', value['icon_color']))}
                except ValueError as error:
                    raise Problem(str(error)) from error
                value.update(artwork)
            if action == 'sync':
                value['base'] = value.get('remote', value['base'])
            # A personal customization never becomes a shared edit merely because permissions changed.
            personal = value['sync_state'] == 'local'
            value['sync_state'] = ('pending' if ident != LOCAL and can_sync(store, ident)
                                   and (not personal or action == 'sync') else 'local')
        value.update(revision=value['revision'] + 1, next=0, attempt=0)
        value.pop('error', None)
        store.set_setting('local_workspace_icon' if ident == LOCAL else 'workspace_art:' + ident, value)
        store.sync_wake.set()
    return dict(value, can_manage=True, can_sync=ident != LOCAL and can_sync(store, ident),
                default=defaults(identity))


def sync_one(store, connection, transport=None):
    """Network I/O runs outside the DB lock; stale workers cannot acknowledge newer edits."""
    from .portal import Portal, remote
    from .managed import guarded_transport
    ident = connection['id']
    with store.lock:
        value = selection(store, ident)
        if value['sync_state'] != 'pending' or value.get('next', 0) > time.time():
            return
        generation = store.setting('authorization_generation', 0)
        def valid():
            current = store.db.execute('SELECT state FROM connections WHERE id=?', (ident,)).fetchone()
            account = store.setting('active_account')
            return (current and current['state'] == 'connected' and account
                    and (account['origin'], account['id']) == (connection['origin'], connection['account'])
                    and store.setting('authorization_generation', 0) == generation)
        if not valid():
            return
        transport, check = guarded_transport(store, connection, transport or remote)
    try:
        token = Portal(store).token(connection)
        path = '/desktop/v1/workspaces/' + connection['workspace'] + '/icon'
        shared = transport(connection['origin'], path, token)
        with store.lock:
            if not valid() or selection(store, ident)['revision'] != value['revision']:
                return
        # Matching values also recover an accepted write whose response was lost.
        state = 'synced'
        if art(shared) != art(value):
            if not shared['can_manage']:
                state = 'local'
            elif art(shared) != art(value['base']):
                state = 'conflict'
            else:
                shared = transport(connection['origin'], path, token,
                                   dict(art(value), revision=shared['revision']))
        with store.tx():
            check()
            if not valid():
                return
            current = selection(store, ident)
            current['remote'] = shared
            if current['revision'] == value['revision']:
                current.update(sync_state=state, next=0, attempt=0)
                current.pop('error', None)
                if state == 'synced':
                    current['base'] = shared
            elif state == 'synced':
                # A newer local edit was saved while this write was in flight.
                current['base'] = shared
            store.set_setting('workspace_art:' + ident, current)
    except Exception as error:
        status = error.status if isinstance(error, Problem) else 503
        with store.tx():
            if not valid():
                return
            current = selection(store, ident)
            if current['revision'] != value['revision']:
                return
            attempt = current.get('attempt', 0) + 1
            current.update(attempt=attempt, next=time.time() + max(getattr(error, 'retry_after', 0), min(300, 2 ** min(attempt, 8))),
                           error='Saved locally. Sync will retry when the portal is available.')
            if status in (400, 403, 404, 405):
                current.update(sync_state='local', error='Saved on this device. Portal icon updates are unavailable or not permitted.')
            # A 409 is fetched again before deciding whether it is a genuine conflict.
            store.set_setting('workspace_art:' + ident, current)


def icon_loop(api, stop):
    from .sync_worker import connections
    while not stop.wait(2):
        for connection in connections(api.store):
            if stop.is_set():
                return
            sync_one(api.store, connection)
