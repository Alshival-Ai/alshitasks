"""Local HTTP transaction boundary: external operations never own the database."""

from urllib.parse import urlsplit, parse_qs, urlencode
from .store import Problem, uid


def handle(api, path, method, body, owner, token):
    from .portal import Portal, remote
    from .security import Secrets, grant_create
    from .workspaces import scope, require_item
    from .managed import guarded_transport
    store = api.store
    url = urlsplit(path)
    route = url.path
    query = {k: v[-1] for k, v in parse_qs(url.query).items()}
    if not isinstance(body, dict):
        raise Problem('JSON object required')
    if route.startswith('/v1/notifications') or route == '/v1/desktop-toasts':
        return api.dispatch(path, method, body, owner, token)
    if route == '/v1/backup' and owner:
        with store.lock:
            return api.dispatch(path, method, body, owner, token)
    if route == '/v1/portal/connect' and method == 'POST' and owner:
        from .accounts import ORIGIN
        return Portal(store).connect(ORIGIN)
    if route == '/v1/secret' and method == 'POST' and owner:
        ident = uid()
        Secrets().put(ident, body['value'])
        return {'id': ident}
    if route == '/v1/team' and owner and query.get('workspace_id') not in (None, '00000000-0000-4000-8000-000000000001'):
        from .workspaces import connection_id
        with store.lock:
            ident = connection_id(store, query['workspace_id'])
            connection = Portal(store).connection(ident)
            if 'team-profiles-v1' not in __import__('json').loads(connection['capabilities']).get('features', []):
                raise Problem('This portal needs an update before it can manage profiles', 409)
            send, check = guarded_transport(store, connection, remote)
        credential = Portal(store).token(connection)
        result = send(connection['origin'], '/desktop/v1/workspaces/' + connection['workspace'] + '/team', credential,
                      **({'body': body} if method != 'GET' else {}))
        with store.tx():
            check()
            if method != 'GET':
                store.sync_wake.set()
        return result
    if route == '/v1/agent-grants' and method == 'POST' and owner:
        from .platform_support import mcp_command
        with store.tx():
            result = grant_create(store, body)
        try:
            Secrets().put(result['id'], result['token'])
        except Exception:
            with store.tx():
                store.db.execute('DELETE FROM grants WHERE id=?', (result['id'],))
            raise
        return {'id': result['id'], 'config': {'mcpServers': {'alshitasks': mcp_command(result['id'])}}}
    if owner and route in ('/v1/portal/workspaces', '/v1/portal/select', '/v1/drafts/resubmit'):
        with store.lock:
            if route == '/v1/drafts/resubmit':
                job = store.db.execute("SELECT * FROM outbox WHERE id=? AND state='draft'", (body['id'],)).fetchone()
                if not job:
                    raise Problem('Draft unavailable', 404)
                ident = job['connection']
            else:
                ident = body.get('connection') or query.get('connection')
            connection = Portal(store).connection(ident)
            send, check = guarded_transport(store, connection, remote)
        credential = Portal(store).token(connection)
        remote_path = '/desktop/v1/workspaces'
        if route == '/v1/drafts/resubmit':
            remote_path += '/' + connection['workspace'] + '/snapshot'
        result = send(connection['origin'], remote_path, credential)
        with store.tx():
            check()
            if route == '/v1/portal/workspaces':
                return result
            if route == '/v1/drafts/resubmit':
                store.db.execute("UPDATE outbox SET state='queued',reason='' WHERE id=? AND state='draft'", (job['id'],))
                return {'queued': True}
            if body['workspace'] not in [w['id'] for w in result['workspaces']]:
                raise Problem('Workspace permission unavailable', 403)
            if connection['workspace'] and connection['workspace'] != body['workspace']:
                new = uid()
                store.set_setting('credential:' + new, store.setting('credential:' + ident, ident))
                store.db.execute('INSERT INTO connections VALUES(?,?,?,?,?,?,?)', (new, connection['origin'], connection['account'], body['workspace'], 'connected', None, connection['capabilities']))
                return {'selected': new}
            store.db.execute('UPDATE connections SET workspace=?,cursor=NULL WHERE id=?', (body['workspace'], ident))
            return {'selected': True}
    sensitive = owner and method != 'GET' and route in ('/v1/ssh', '/v1/ssh/key', '/v1/ssh/open', '/v1/forwarding')
    check = lambda: None
    prepared = None
    if sensitive:
        resource = body.get('resource_id') or query.get('resource_id')
        with store.lock, scope(store, query.get('workspace_id')):
            if not resource and route == '/v1/forwarding' and body.get('id'):
                target = store.db.execute('SELECT resource_id FROM forwarding WHERE id=?', (body['id'],)).fetchone()
                resource = target[0] if target else None
            row = require_item(store, resource)
            connection = Portal(store).connection(row['connection']) if row['connection'] else None
            remote_id = store.db.execute('SELECT remote_id FROM items WHERE id=?', (resource,)).fetchone()[0]
            if connection and (route != '/v1/forwarding' or method == 'POST'):
                send, check = guarded_transport(store, connection, remote)
        online_check = check
        def check():
            online_check()
            current = store.db.execute('SELECT connection,remote_id FROM items WHERE id=?', (resource,)).fetchone()
            require_item(store, resource)
            if not current or (current['connection'], current['remote_id']) != (row['connection'], remote_id):
                raise Problem('Resource changed; retry the action', 409)
        if connection and (route != '/v1/forwarding' or method == 'POST'):
            credential = Portal(store).token(connection)
            after = None
            while True:
                suffix = '?' + urlencode({'after': after}) if after else ''
                page = send(connection['origin'], '/desktop/v1/workspaces/' + connection['workspace'] + '/resources' + suffix, credential)
                if any(r['id'] == remote_id for r in page['items']):
                    break
                after = page.get('next')
                if not after:
                    raise Problem('Current online resource authorization required', 403)
        if route == '/v1/forwarding' and body.get('credential'):
            Secrets().get(body['credential'])
        if route in ('/v1/ssh', '/v1/ssh/key') and method == 'POST':
            from .ssh import inspect_alias
            prepared = inspect_alias(store, body)
        if route == '/v1/ssh/open' and method == 'POST':
            from .ssh import launch_terminal
            with store.lock, scope(store, query.get('workspace_id')):
                check()
                require_item(store, resource)
                row = store.db.execute('SELECT alias FROM aliases WHERE resource_id=?', (resource,)).fetchone()
                if not row:
                    raise Problem('SSH alias unavailable', 404)
            return launch_terminal(row['alias'])
    with store.tx():
        check()
        return api.dispatch(path, method, body, owner, token, proof=(check, prepared) if sensitive else None)
