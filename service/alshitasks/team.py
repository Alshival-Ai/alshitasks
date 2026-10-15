"""Workspace profiles are assignable identities, independent of login accounts."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json

from .store import Problem, now, uid
from .workspaces import LOCAL

_actor = ContextVar('alshitasks_actor', default=None)


def actor():
    return _actor.get() or {'id': 'local', 'name': 'Me', 'kind': 'person', 'owner': True}


def migrate(store):
    with store.tx():
        store.db.execute('''CREATE TABLE IF NOT EXISTS team_profiles(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 1)''')
        store.db.execute('''CREATE TABLE IF NOT EXISTS profile_grants(
            grant_id TEXT PRIMARY KEY REFERENCES grants(id) ON DELETE CASCADE,
            profile_id TEXT NOT NULL REFERENCES team_profiles(id), expires_at TEXT)''')
        store.db.execute('''CREATE TABLE IF NOT EXISTS event_actors(
            sequence INTEGER PRIMARY KEY REFERENCES events(sequence) ON DELETE CASCADE,
            actor TEXT NOT NULL)''')
        if not store.setting('team_profiles_migrated'):
            for member in store.setting('local_members', []):
                store.db.execute('INSERT OR IGNORE INTO team_profiles(id,name,kind) VALUES(?,?,?)',
                                 (member['id'], member['name'], 'person'))
            store.set_setting('team_profiles_migrated', True)


def members(store):
    return [dict(r, active=bool(r['active']), has_login=False, workspace_id=LOCAL)
            for r in store.db.execute('SELECT * FROM team_profiles ORDER BY name,id')]


def profile(store, ident, *, active=False):
    row = store.db.execute('SELECT * FROM team_profiles WHERE id=?', (ident,)).fetchone()
    if not row or (active and not row['active']):
        raise Problem('Choose an active profile in this workspace', 404)
    return dict(row)


def save(store, body, ident=None):
    if store.workspace not in (None, LOCAL):
        raise Problem('Manage connected workspace profiles in its portal', 409)
    if set(body) - {'name', 'kind', 'active', 'revision'}:
        raise Problem('Unknown profile field')
    old = profile(store, ident) if ident else {}
    if old and body.get('revision') != old['revision']:
        raise Problem('Profile changed; reload before saving', 409)
    name = body.get('name', old.get('name', ''))
    kind = body.get('kind', old.get('kind', 'person'))
    active = body.get('active', bool(old.get('active', True)))
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or kind not in ('person', 'droid') or type(active) is not bool:
        raise Problem('Supply a name, Person or Droid type, and active status')
    ident = ident or 'profile:' + uid()
    if old:
        store.db.execute('UPDATE team_profiles SET name=?,kind=?,active=?,revision=revision+1 WHERE id=?',
                         (name.strip(), kind, active, ident))
    else:
        store.db.execute('INSERT INTO team_profiles(id,name,kind,active) VALUES(?,?,?,?)', (ident, name.strip(), kind, active))
    if not active:
        store.db.execute('UPDATE grants SET revoked_at=? WHERE revoked_at IS NULL AND id IN (SELECT grant_id FROM profile_grants WHERE profile_id=?)', (now(), ident))
    store.event(ident, 'profile.update' if old else 'profile.create', {'name': name.strip(), 'kind': kind, 'active': active})
    return next(m for m in members(store) if m['id'] == ident)


def bind(store, grant_id, body):
    ident = body.get('profile_id')
    if not ident:
        return
    if store.workspace not in (None, LOCAL):
        raise Problem('Local credentials cannot access a connected workspace', 403)
    profile(store, ident, active=True)
    expires = body.get('expires_at')
    if expires:
        try:
            parsed = datetime.fromisoformat(expires.replace('Z', '+00:00'))
            if parsed.tzinfo is None or parsed <= datetime.now(timezone.utc):
                raise ValueError()
            expires = parsed.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, AttributeError):
            raise Problem('Expiry must be a future timestamp with a timezone')
    store.db.execute('INSERT INTO profile_grants VALUES(?,?,?)', (grant_id, ident, expires))


def grant_profile(store, grant):
    binding = store.db.execute('SELECT * FROM profile_grants WHERE grant_id=?', (grant['id'],)).fetchone()
    if not binding:
        return None
    person = profile(store, binding['profile_id'], active=True)
    if binding['expires_at'] and binding['expires_at'] <= now():
        raise Problem('Credential expired', 401)
    if store.workspace not in (None, LOCAL):
        raise Problem('Credential belongs to the local workspace', 403)
    return dict(person, active=bool(person['active']), has_login=False, id=person['id'], credential_id=grant['id'], issuer_id='local',
                workspace_id=LOCAL, owner=False, scopes=json.loads(grant['scopes']))


@contextmanager
def principal(store, owner, token, workspace=None):
    from .security import authenticate
    from .workspaces import scope
    value = None
    if not owner:
        grant = authenticate(store, token)
        value = grant_profile(store, grant)
    if value:
        if workspace not in (None, LOCAL):
            raise Problem('Credential belongs to the local workspace', 403)
        workspace = LOCAL
    marker = _actor.set(value or ({'id': 'local', 'name': 'Me', 'kind': 'person', 'owner': True} if owner else
                                 {'id': 'key:' + grant['id'], 'name': grant['name'], 'credential_id': grant['id'], 'owner': False}))
    try:
        with scope(store, workspace):
            yield
    finally:
        _actor.reset(marker)


def validate_assignment(store, data, old=None):
    for field in ('assignee', 'reviewer'):
        ident = data.get(field)
        if not isinstance(ident, str) or not ident.startswith(('profile:', 'contact:')):
            continue
        if old and old.get(field) == ident:
            continue  # Historical references survive deactivation.
        if store.workspace not in (None, LOCAL):
            raise Problem('Choose a profile from the selected workspace')
        profile(store, ident, active=True)
    if data.get('assignee') and data.get('assignee') == data.get('reviewer'):
        raise Problem('Assignee and reviewer must be different members')


def validate_review(old, data):
    current = actor()
    if not current.get('workspace_id') or old.get('state') == data.get('state'):
        return
    if data.get('state') == 'done' and old.get('reviewer'):
        if ('project:approve' not in current.get('scopes', []) or current['id'] != old['reviewer']
                or current['id'] == old.get('assignee') or old.get('state') != 'review'):
            raise Problem('Only the assigned reviewer with approval permission can approve work in review', 403)
