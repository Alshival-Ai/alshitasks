"""Optional Ubuntu provisioning. Secrets never enter the app database/keyring."""
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from .store import Problem, uid

ROOT = Path('/var/lib/alshival/desktop')
ORIGIN = 'https://alshival.ai'


def read_file(path, owner, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner
                or info.st_mode & (0o077 if private else 0o022) or info.st_size > 16384):
            raise ValueError('Unsafe managed state')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return stream.read(16385)
    finally:
        os.close(fd)


def snapshot():
    path = ROOT / str(os.getuid())
    try:
        # Check every ancestor before opening; all are root-controlled.
        for parent in reversed([path, *path.parents]):
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Unsafe managed directory')
        identity = json.loads(read_file(path / 'identity.json', 0))
        if identity.get('status') != 'ready':
            return identity
        raw = read_file(path / 'credential.json', os.getuid(), private=True)
        if hashlib.sha256(raw).hexdigest() != identity.get('credential_sha256'):
            raise ValueError('Managed snapshot changed')
        credential = json.loads(raw)
        if (credential.get('uid') != os.getuid() or credential.get('version') != 1
                or credential.get('resource') != ORIGIN + '/desktop/v1'
                or any(credential.get(k) != identity.get(k) for k in
                       ('account_id', 'machine_id', 'generation', 'expires_at'))):
            raise ValueError('Managed identity mismatch')
        if credential.get('expires_at', 0) <= time.time():
            return dict(identity, status='unavailable')
        return credential
    except FileNotFoundError:
        # No enrollment is normal for public installations. Partial state fails closed.
        if not path.exists():
            return None
        return {'status': 'unavailable'}
    except (OSError, ValueError, TypeError):
        return {'status': 'unavailable'}


def enabled(store):
    return bool(store.setting('managed_account')) or snapshot() is not None


def token(store, connection):
    value = snapshot()
    if (not value or value.get('status') != 'ready'
            or connection['origin'] != ORIGIN or connection['account'] != value.get('account_id')):
        raise Problem('Ubuntu account connection is unavailable; local data is retained', 401)
    return value['token']


def guarded_transport(store, connection, transport):
    """Fence old workers at each request, including long attachment transfers."""
    with store.lock:
        generation = store.setting('authorization_generation', 0)
    def check():
        if store.setting('authorization_generation', 0) != generation:
            raise Problem('Account changed; work retained locally', 401)
        if enabled(store):
            token(store, connection)
    def send(*args, **kwargs):
        with store.lock:
            check()
        result = transport(*args, **kwargs)
        with store.lock:
            check()
        return result
    return send, check


def reconcile(store, transport=None):
    from .accounts import activate
    from .portal import remote
    value = snapshot()
    with store.tx():
        if value is None and not store.setting('managed_account'):
            return
        if store.setting('managed_next', 0) > time.time():
            return
        store.set_setting('managed_next', time.time() + 30)
        store.set_setting('managed_account', True)
    if not value or value.get('status') != 'ready':
        with store.tx():
            store.set_setting('discovery', {'state': (value or {}).get('status', 'unavailable'), 'next': time.time() + 30})
        return
    try:
        capabilities = (transport or remote)(ORIGIN, '/desktop/v1/capabilities', value['token'])
        if capabilities.get('resource') != value['resource'] or str(capabilities.get('account_id')) != value['account_id']:
            raise Problem('Ubuntu account identity did not match the portal', 401)
        with store.sync_lock, store.tx():
            # Re-read after network I/O so revocation/rotation cannot activate stale state.
            fresh = snapshot()
            if not fresh or fresh.get('generation') != value['generation'] or fresh.get('status') != 'ready':
                return
            current = store.setting('active_account')
            same = current and current['origin'] == ORIGIN and current['id'] == value['account_id']
            if not same:
                store.set_setting('authorization_generation', store.setting('authorization_generation', 0) + 1)
                store.db.execute("UPDATE connections SET state='disconnected' WHERE account!=? OR origin!=?", (value['account_id'], ORIGIN))
                store.db.execute("UPDATE outbox SET state='draft',reason='Previous account; retained locally' WHERE state='queued' AND connection IN (SELECT id FROM connections WHERE account!=? OR origin!=?)", (value['account_id'], ORIGIN))
                store.set_setting('active_account', None)
            row = store.db.execute("SELECT id FROM connections WHERE origin=? AND account=? AND workspace='' ORDER BY rowid LIMIT 1", (ORIGIN, value['account_id'])).fetchone()
            ident = row[0] if row else uid()
            if not row:
                store.db.execute('INSERT INTO connections VALUES(?,?,?,?,?,?,?)', (ident, ORIGIN, value['account_id'], '', 'connected', None, json.dumps(capabilities)))
            store.db.execute("UPDATE connections SET state='connected' WHERE id=?", (ident,))
            store.set_setting('managed_credential', ident)
            if not same or current['connection'] != ident:
                activate(store, ident, capabilities)
            else:
                for item in store.db.execute('SELECT id FROM connections WHERE origin=? AND account=?', (ORIGIN, value['account_id'])).fetchall():
                    store.set_setting('credential:' + item[0], ident)
            store.set_setting('discovery', {'next': 0})
    except Problem as error:
        with store.tx():
            store.set_setting('discovery', {'state': 'unavailable', 'error': str(error), 'next': time.time() + 30})
