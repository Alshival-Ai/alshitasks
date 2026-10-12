"""Canonical portal IDs at the MCP boundary, without rewriting offline sync storage."""
import json
from .store import Problem
from .workspaces import LOCAL, scope


class Ids:
    def __init__(self, store, connection):
        self.store = store
        self.connection = connection
        self.public = {}
        if connection not in (None, LOCAL):
            for r in store.db.execute('SELECT id,remote_id FROM items WHERE connection=? AND remote_id IS NOT NULL', (connection,)):
                self.public[r['id']] = r['remote_id']
            for status in store.statuses():
                remote = store.setting('status_remote:' + status['id'])
                if remote:
                    self.public[status['id']] = remote
            for member in store.setting('members:' + connection, []):
                remote = store.setting('member_remote:' + connection + ':' + member['id'])
                if remote is not None:
                    self.public[member['id']] = str(remote)
            prefix = 'edge_local:' + connection + ':'
            for row in store.db.execute('SELECT key,value FROM settings WHERE substr(key,1,?)=?', (len(prefix), prefix)):
                self.public[json.loads(row['value'])] = row['key'][len(prefix):]
        self.local = {remote: local for local, remote in self.public.items()}

    def convert(self, value, outward=False, key=''):
        mapping = self.public if outward else self.local
        if isinstance(value, dict):
            return {k: self.convert(v, outward, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self.convert(v, outward, key) for v in value]
        if isinstance(value, str):
            if key in {'id', 'board_id', 'parent_id', 'source_id', 'target_id', 'status_id',
                       'replacement_id', 'assignee', 'reviewer', 'order'}:
                return mapping.get(value, value)
            if key == 'body':
                from .graph import translate
                return translate(value, lambda ident: mapping.get(ident, ident))
        return value


def call(api, name, args, owner=False, token=''):
    from .contract import validate
    from .security import authorize
    from .workspaces import catalog
    validate(name, args)
    if name == 'workspace_list':
        result = api.tool(name, args, owner, token)
        for workspace in result['workspaces']:
            workspace.pop('connection_id', None)
        return result
    # An unqualified local call must not aggregate or mutate unrelated shared workspaces.
    if not args.get('workspace_id') and any(not w.get('local') for w in catalog(api.store)):
        raise Problem('Select workspace_id from workspace_list', 422)
    with scope(api.store, args.get('workspace_id')):
        ids = Ids(api.store, api.store.workspace)
        converted = ids.convert(args)
        # Keep canonical cursor ordering; sync row UUIDs use a different order.
        if name in {'project_list', 'project_comments', 'project_members'}:
            if not owner:
                authorize(api.store, token, 'project:read')
            if name == 'project_list':
                filters = {k: v for k, v in converted.items() if k != 'workspace_id'}
                result = api.store.list(**filters, canonical_ids=True)
            elif name == 'project_members':
                rows = sorted((ids.convert(m, True) for m in api.members()), key=lambda m: m['id'])
                rows = [m for m in rows if m['id'] > args.get('after', '')]
                limit = args.get('limit', 100)
                return api.conform_tool_result(name, {'items': rows[:limit], 'next': rows[limit-1]['id'] if len(rows) > limit else None})
            else:
                api.store.item(converted['id'])
                limit = args.get('limit', 100)
                rows = api.store.db.execute("SELECT * FROM items WHERE kind='comment' AND json_extract(data,'$.parent_id')=? AND COALESCE(remote_id,id)>? ORDER BY COALESCE(remote_id,id) LIMIT ?", (converted['id'], args.get('after', ''), limit+1)).fetchall()
                result = {'items': [api.store.row(r) for r in rows[:limit]],
                          'next': (rows[limit-1]['remote_id'] or rows[limit-1]['id']) if len(rows)>limit else None}
        else:
            result = api.tool(name, converted, owner, token)
        # A mutation may have created additional ID mappings.
        return api.conform_tool_result(name, Ids(api.store, api.store.workspace).convert(result, True))
