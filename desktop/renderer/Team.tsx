import { useEffect, useState } from 'react';
import { LOCAL_WORKSPACE } from './project-board/api';
import './teams.css';

type Profile = {id: string; name: string; kind: string; active: boolean; revision: number; has_login?: boolean};
type Credential = {id: string; name: string; profile_id: string; scopes: string[]; expires_at?: string; revoked_at?: string};
export function Team({workspace}: {workspace: string}) {
  const [members, setMembers] = useState<Profile[]>([]), [credentials, setCredentials] = useState<Credential[]>([]);
  const [selected, setSelected] = useState<Profile | null>(null), [creating, setCreating] = useState(false);
  const [search, setSearch] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [permissions, setPermissions] = useState<string[]>(['project:read','project:write','project:approve']);
  const request = (method = 'GET', body?: unknown) => window.tasks.request('/v1/team?workspace_id=' + encodeURIComponent(workspace), method, body) as Promise<any>;
  async function refresh() { const value = await request(); setMembers(value.members); setCredentials(value.credentials || []); if(value.permissions) setPermissions(value.permissions); }
  useEffect(() => {let current = true; setSelected(null); setCreating(false); setResult(null); setError(''); request().then(value => {if (current) {setMembers(value.members); setCredentials(value.credentials || []); if(value.permissions) setPermissions(value.permissions);}}).catch(e => {if (current) setError(String(e));}); return () => {current = false;};}, [workspace]);
  async function work(operation: () => Promise<any>) {setBusy(true);setError('');try {await operation();await refresh();} catch(e) {setError(String(e));} finally {setBusy(false);}}
  const local = workspace === LOCAL_WORKSPACE;
  return <section id="board-team" className="panel team-app" aria-label="Team">
    <header className="team-heading"><div><p className="team-eyebrow">Better together</p><h2>Team <span className="team-count">{members.length}</span></h2><p className="muted">People and droids working together.</p></div><button onClick={() => {setCreating(true);setSelected(null);setResult(null);}}>Create profile</button></header>
    {error && <p role="alert">{error}</p>}
    {result && <section role="status"><h3>Credential created</h3><p>Copy this setup now. Configure your external agent to read tasks assigned to this profile.</p><pre>{JSON.stringify(result, null, 2)}</pre><button onClick={() => {void navigator.clipboard.writeText(JSON.stringify(result, null, 2));}}>Copy setup</button><button onClick={() => setResult(null)}>Dismiss</button></section>}
    <div className={creating || selected ? "team-layout has-editor" : "team-layout"}><div className="team-directory"><label className="team-search"><span className="sr-only">Search members</span><input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Find a teammate…" /></label>
    <div className="team-grid">{members.filter(m => m.name.toLowerCase().includes(search.toLowerCase())).map(m => <button className="team-card" key={m.id} onClick={() => {setSelected(m);setCreating(false);setResult(null);}}><span className="team-card-top"><span className="team-avatar">{m.name.slice(0,2).toUpperCase()}</span><span className="team-role">{m.kind === 'droid' ? 'Droid' : 'Person'}</span></span><strong>{m.name}</strong><span className="team-email">{m.has_login ? 'Login account' : 'No login account'}</span><span className="team-card-bottom">{m.active ? 'Workspace member' : 'Inactive'}</span></button>)}</div>
    {!members.length && !error && <p>Create a profile to assign work to a person or droid.</p>}</div>
    {(creating || selected) && <aside className="team-editor"><button className="team-close" aria-label="Close member details" onClick={() => {setCreating(false);setSelected(null);}}>×</button><h3>{selected?.name || 'Create profile'}</h3>
    {selected?.has_login ? <p>Manage login accounts and invitations in the workspace portal.</p> : <><p>An assignable workspace identity. No email or login account is required.</p><form key={selected?.id || 'new'} onSubmit={e => {e.preventDefault();const values = new FormData(e.currentTarget);void work(async () => {const saved = await request('POST', {action: 'save', id: selected?.id, name: values.get('name'), kind: values.get('kind'), active: selected ? values.get('active') === 'on' : true, revision: selected?.revision});setSelected(saved);setCreating(false);});}}>
    <label>Name<input name="name" required maxLength={120} defaultValue={selected?.name || ''}/></label><label>Type<select aria-label="Type" name="kind" defaultValue={selected?.kind || 'person'}><option value="person">Person</option><option value="droid">Droid</option></select></label>
    {selected && <><label><input type="checkbox" name="active" defaultChecked={selected.active}/> Active</label><p className="muted">Deactivation revokes credentials and keeps past assignments and activity. Reactivating requires new credentials.</p></>}
    <button disabled={busy}>{selected ? 'Save profile' : 'Create profile'}</button></form>
    {selected?.active && <details><summary>Create credential</summary><form onSubmit={e => {e.preventDefault();const values=new FormData(e.currentTarget);void work(async () => {const body = {name: values.get('name'), profile_id: selected.id, scopes: values.getAll('scopes'), expires_at: values.get('expires') ? new Date(String(values.get('expires'))).toISOString() : null};setResult(local ? await window.tasks.request('/v1/agent-grants','POST',body) : await request('POST', {action: 'credential', ...body}));});}}>
    <label>Credential name<input name="name" required maxLength={100} defaultValue={selected.name}/></label><fieldset><legend>Workspace permissions</legend>{permissions.map(scope => <label key={scope}><input name="scopes" type="checkbox" value={scope} defaultChecked={scope==='project:read'}/> {scope}</label>)}</fieldset><label>Expires (optional)<input name="expires" type="datetime-local"/></label><p className="muted">Selected permissions apply to workspace work, including tasks assigned to other members. Assignment does not start a droid.</p><button disabled={busy}>Create credential</button></form></details>}
    {credentials.filter(c => c.profile_id === selected?.id).map(c => <div className="team-invitation" key={c.id}><span>{c.name} · {c.revoked_at ? 'Revoked' : 'Active'}{c.expires_at ? ' · Expires '+new Date(c.expires_at).toLocaleString() : ''}</span>{!c.revoked_at && <button disabled={busy} onClick={() => void work(() => local ? window.tasks.request('/v1/projects/keys/'+c.id,'DELETE') : request('POST',{action:'revoke',id:c.id}))}>Revoke</button>}</div>)}
    </>}</aside>}
    </div></section>;
}
