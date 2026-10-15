"""Agent-independent, local-only assignment webhooks with a transactional outbox."""
import hashlib
import hmac
import http.client
import ipaddress
import json
import secrets
import socket
import ssl
import time
from urllib.parse import urlsplit

from .security import Secrets
from .store import Problem, now, uid
from .workspaces import LOCAL

EVENTS = ('task.assignment.changed', 'task.deleted')
DAY = 86400
PRIVATE = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '::1/128', 'fc00::/7'))


def migrate(store):
    with store.tx():
        store.db.execute('''CREATE TABLE IF NOT EXISTS webhooks(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, enabled INTEGER NOT NULL,
            board_id TEXT, assignee_id TEXT, events TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL)''')
        store.db.execute('''CREATE TABLE IF NOT EXISTS webhook_deliveries(
            id TEXT PRIMARY KEY, webhook_id TEXT NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
            generation INTEGER NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, next_at REAL NOT NULL,
            expires REAL NOT NULL, lease TEXT, lease_until REAL NOT NULL DEFAULT 0,
            status_code INTEGER, detail TEXT NOT NULL DEFAULT '', delivered_at TEXT)''')
        store.db.execute('CREATE INDEX IF NOT EXISTS webhook_due ON webhook_deliveries(state,next_at)')
        store.db.execute('CREATE TABLE IF NOT EXISTS webhook_task_state(task_id TEXT PRIMARY KEY, revision INTEGER NOT NULL)')
        store.db.execute("INSERT OR IGNORE INTO webhook_task_state SELECT id,revision FROM items WHERE kind='task' AND connection IS NULL")
        if not store.setting('webhook_instance_id'):
            store.set_setting('webhook_instance_id', uid())


def private_address(value):
    try:
        address = ipaddress.ip_address(value)
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address.version == n.version and address in n for n in PRIVATE)
    except ValueError:
        return False


def destination(url, resolve=False):
    try:
        if not isinstance(url, str) or len(url) > 2048 or any(ord(c) <= 32 for c in url):
            raise ValueError()
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError()
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if not 1 <= port <= 65535:
            raise ValueError()
        try:
            literal = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            literal = None
        if literal and not private_address(str(literal)):
            raise ValueError()
    except (ValueError, AttributeError):
        raise Problem('Use an HTTP(S) URL on loopback or a private network; credentials and fragments are not allowed') from None
    if not resolve:
        return parsed
    addresses = list(dict.fromkeys(a[4][0] for a in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)))
    if not addresses or not all(private_address(a) for a in addresses):
        raise Problem('Destination must resolve exclusively to loopback or private-network addresses')
    return parsed, addresses[0], port


def public(row):
    result = dict(row)
    result['enabled'] = bool(result['enabled'])
    result['events'] = json.loads(result['events'])
    return result


def get(store, ident):
    row = store.db.execute('SELECT * FROM webhooks WHERE id=?', (ident,)).fetchone()
    if not row:
        raise Problem('Webhook not found', 404)
    return row


def enqueue(store, hook, event):
    stamp = time.time()
    store.db.execute('''INSERT INTO webhook_deliveries(id,webhook_id,generation,payload,created,next_at,expires)
                        VALUES(?,?,?,?,?,?,?)''',
                     (uid(), hook['id'], hook['generation'], json.dumps(event, separators=(',', ':')), stamp, stamp, stamp + DAY))


def changed(store, item, old=None):
    """Called inside the same task mutation transaction; never during sync/import."""
    if item['kind'] != 'task':
        return
    row = store.db.execute('SELECT connection FROM items WHERE id=?', (item['id'],)).fetchone()
    if not row or row['connection']:
        return
    data, previous = item['data'], (old or {}).get('data', {})
    if not old or data.get('assignee') != previous.get('assignee') or bool(data.get('deleted')) != bool(previous.get('deleted')):
        store.db.execute('INSERT OR REPLACE INTO webhook_task_state VALUES(?,?)', (item['id'], item['revision']))
    if data.get('deleted') and not previous.get('deleted'):
        kind = 'task.deleted'
    elif not data.get('deleted') and not previous.get('deleted') and data.get('assignee') != previous.get('assignee'):
        kind = 'task.assignment.changed'
    else:
        return
    def identity(ident):
        if not ident:
            return None
        profile = store.db.execute('SELECT kind FROM team_profiles WHERE id=?', (ident,)).fetchone()
        return {'id': ident, 'kind': profile['kind'] if profile else 'person'}
    event = {'schema_version': 1, 'id': uid(), 'type': kind, 'occurred_at': now(),
             'source': {'instance_id': store.setting('webhook_instance_id'), 'workspace_id': LOCAL},
             'task': {'id': item['id'], 'board_id': data.get('board_id'), 'revision': item['revision']},
             'previous_assignee': identity(previous.get('assignee')),
             'assignee': identity(data.get('assignee'))}
    for hook in store.db.execute('SELECT * FROM webhooks WHERE enabled=1').fetchall():
        if kind not in json.loads(hook['events']):
            continue
        if hook['board_id'] and hook['board_id'] != data.get('board_id'):
            continue
        if hook['assignee_id'] and hook['assignee_id'] not in (previous.get('assignee'), data.get('assignee')):
            continue
        enqueue(store, hook, event)


def dispatch(store, path, method, body, owner):
    if not owner:
        raise Problem('Only the local owner can manage webhooks', 403)
    if store.workspace not in (None, LOCAL):
        raise Problem('Webhooks currently support local boards only', 409)
    parts = path.removeprefix('/v1/webhooks').strip('/').split('/')
    ident = parts[0]
    if not ident and method == 'GET':
        return {'webhooks': [public(r) for r in store.db.execute('SELECT * FROM webhooks ORDER BY created_at,id')],
                'instance_id': store.setting('webhook_instance_id')}
    old = get(store, ident) if ident else None
    if len(parts) == 2:
        if parts[1] == 'deliveries' and method == 'GET':
            return {'deliveries': [dict(r, event=json.loads(r['payload'])) for r in store.db.execute(
                'SELECT id,payload,state,attempts,created,next_at,status_code,detail,delivered_at FROM webhook_deliveries WHERE webhook_id=? ORDER BY created DESC LIMIT 100', (ident,))]}
        if parts[1] == 'test' and method == 'POST':
            if not old['enabled']:
                raise Problem('Enable the webhook before sending a test', 409)
            event = {'schema_version': 1, 'id': uid(), 'type': 'webhook.test', 'occurred_at': now(),
                     'source': {'instance_id': store.setting('webhook_instance_id'), 'workspace_id': LOCAL}}
            enqueue(store, old, event)
            return {'queued': True, 'event_id': event['id']}
    if len(parts) == 4 and parts[1] == 'deliveries' and parts[3] == 'retry' and method == 'POST':
        row = store.db.execute('SELECT * FROM webhook_deliveries WHERE id=? AND webhook_id=?', (parts[2], ident)).fetchone()
        if not row:
            raise Problem('Delivery not found', 404)
        if not old['enabled'] or row['generation'] != old['generation'] or row['state'] != 'failed':
            raise Problem('Only a failed delivery for the current enabled configuration can be retried', 409)
        store.db.execute("UPDATE webhook_deliveries SET state='pending',next_at=?,expires=?,detail='' WHERE id=?", (time.time(), time.time()+DAY, row['id']))
        return {'queued': True}
    if len(parts) != 1:
        raise Problem('Unknown webhook operation', 404)
    if ident and method == 'DELETE':
        store.db.execute('DELETE FROM webhooks WHERE id=?', (ident,))
        # The ordinary credential cleanup worker also handles retryable failures.
        store.set_setting('credential_cleanup:webhook:' + ident, True)
        return {'deleted': True}
    if method not in ('POST', 'PATCH') or (method == 'PATCH' and not ident):
        raise Problem('Unsupported webhook operation', 405)
    if set(body) - {'name', 'url', 'enabled', 'board_id', 'assignee_id', 'events', 'rotate_secret', 'generation'}:
        raise Problem('Unknown webhook field')
    if old and body.get('generation') != old['generation']:
        raise Problem('Webhook changed; reload before saving', 409)
    values = dict(old) if old else {'name': '', 'url': '', 'enabled': False, 'board_id': None, 'assignee_id': None, 'events': list(EVENTS)}
    if old:
        values['events'] = json.loads(values['events'])
    values.update({k: v for k, v in body.items() if k in values})
    if not isinstance(values['name'], str) or not 1 <= len(values['name'].strip()) <= 120:
        raise Problem('Choose a webhook name of 1–120 characters')
    if type(values['enabled']) not in (bool, int) or values['enabled'] not in (True, False, 0, 1):
        raise Problem('Enabled must be a boolean')
    if not isinstance(values['events'], list) or not values['events'] or any(e not in EVENTS for e in values['events']):
        raise Problem('Select supported task events')
    destination(values['url'])
    if values['board_id']:
        board = store.item(values['board_id'])
        connection = store.db.execute('SELECT connection FROM items WHERE id=?', (board['id'],)).fetchone()[0]
        if board['kind'] != 'board' or connection or board['data'].get('deleted'):
            raise Problem('Select a local board')
    if values['assignee_id'] and values['assignee_id'] != 'local':
        from .team import profile
        profile(store, values['assignee_id'])
    ident = ident or uid()
    secret = None
    if not old or body.get('rotate_secret'):
        secret = secrets.token_urlsafe(32)
        Secrets().put('webhook:' + ident, secret)
    generation = old['generation'] + 1 if old else 1
    store.db.execute('''INSERT INTO webhooks VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
        name=excluded.name,url=excluded.url,enabled=excluded.enabled,board_id=excluded.board_id,
        assignee_id=excluded.assignee_id,events=excluded.events,generation=excluded.generation''',
        (ident, values['name'].strip(), values['url'], int(values['enabled']), values['board_id'] or None,
         values['assignee_id'] or None, json.dumps(values['events']), generation, now()))
    store.db.execute("UPDATE webhook_deliveries SET state='cancelled',detail='Configuration changed',lease=NULL,lease_until=0 WHERE webhook_id=? AND state IN ('pending','sending')", (ident,))
    result = {'webhook': public(get(store, ident))}
    if secret:
        result['secret'] = secret
    return result


def send(url, payload, secret, event_id):
    parsed, address, port = destination(url, resolve=True)
    stamp = str(int(time.time()))
    raw = payload.encode()
    signature = hmac.new(secret.encode(), stamp.encode()+b'.'+raw, hashlib.sha256).hexdigest()
    cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
    connection = cls(parsed.hostname, port, timeout=10, **({'context': ssl.create_default_context()} if parsed.scheme == 'https' else {}))
    # Pin the validated IP through connect; TLS still verifies the original hostname.
    connection._create_connection = lambda target, timeout, source_address=None: socket.create_connection((address, port), timeout, source_address)
    try:
        connection.request('POST', (parsed.path or '/') + ('?'+parsed.query if parsed.query else ''), body=raw,
                           headers={'Content-Type': 'application/json', 'User-Agent': 'AlshiTasks-Webhooks/1',
                                    'X-AlshiTasks-Event-Id': event_id, 'X-AlshiTasks-Timestamp': stamp,
                                    'X-AlshiTasks-Signature': 'sha256='+signature})
        return connection.getresponse().status  # No redirect or response-body logging.
    finally:
        connection.close()


def deliver_once(store, transport=send, clock=time.time):
    stamp = clock()
    with store.tx():
        store.db.execute('DELETE FROM webhook_deliveries WHERE created<?', (stamp-30*DAY,))
        store.db.execute("UPDATE webhook_deliveries SET state='failed',detail='Delivery window expired',lease=NULL WHERE state IN ('pending','sending') AND expires<=?", (stamp,))
        row = store.db.execute('''SELECT d.*,w.url FROM webhook_deliveries d JOIN webhooks w ON w.id=d.webhook_id
            WHERE w.enabled=1 AND d.generation=w.generation AND d.expires>? AND d.next_at<=?
            AND (d.state='pending' OR (d.state='sending' AND d.lease_until<=?)) ORDER BY d.created,d.id LIMIT 1''', (stamp,stamp,stamp)).fetchone()
        if not row:
            return False
        row = dict(row)
        lease = uid()
        store.db.execute("UPDATE webhook_deliveries SET state='sending',attempts=attempts+1,lease=?,lease_until=? WHERE id=?", (lease,stamp+60,row['id']))
    code = None
    try:
        secret = Secrets().get('webhook:'+row['webhook_id'])
        if not secret:
            raise Problem('Signing credential unavailable')
        code = transport(row['url'],row['payload'],secret,json.loads(row['payload'])['id'])
        success = 200 <= code < 300
        retry = code in (408,425,429) or code >= 500
        detail = 'Accepted by receiver' if success else f'Receiver returned HTTP {code}'
    except Problem:
        success, retry, detail = False, False, 'Destination or signing credential unavailable; check configuration'
    except Exception:
        success, retry, detail = False, True, 'Connection failed; retrying within the delivery window'
    stamp = clock()
    delay = min(3600, 5 * 2**min(row['attempts'],10))
    state = 'delivered' if success else 'pending' if retry and stamp+delay < row['expires'] else 'failed'
    with store.tx():
        store.db.execute('''UPDATE webhook_deliveries SET state=?,next_at=?,status_code=?,detail=?,delivered_at=?,lease=NULL,lease_until=0
            WHERE id=? AND lease=? AND state='sending' ''',
            (state,stamp+delay,code,detail,now() if success else None,row['id'],lease))
    return True


def webhook_loop(api, stop):
    while not stop.is_set() and not api.stopping:
        worked = deliver_once(api.store)
        stop.wait(0.05 if worked else 1)
