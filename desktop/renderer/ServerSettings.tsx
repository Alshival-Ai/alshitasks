import { useEffect, useState } from "react";
export function ServerSettings() {
  const [grants, setGrants] = useState<any[]>([]), [config, setConfig] = useState(""), [error, setError] = useState("");
  const load = async () => setGrants((await window.tasks.request("/v1/projects/keys")).keys);
  useEffect(() => { void load().catch(e => setError(String(e))); }, []);
  return <section><h2>Remote MCP access</h2><p>Create a separate token for each agent. Copy it now; it will only be shown once.</p>
    <form onSubmit={event => {
      event.preventDefault(); const values = new FormData(event.currentTarget);
      const scopes = ["project:read", "logs:read", "monitor:read", "notifications:read"];
      if (values.get("write")) scopes.push("project:write");
      void window.tasks.request("/v1/agent-grants", "POST", {
        name: values.get("name"), scopes,
        expires_at: values.get("days") ? Date.now()/1000 + Number(values.get("days"))*86400 : null,
      }).then(result => { setConfig(JSON.stringify(result.config, null, 2)); setError(""); return load(); }).catch(e => setError(String(e)));
    }}>
      <label>Token name<input name="name" required maxLength={120} defaultValue="Remote agent" /></label>
      <label><input name="write" type="checkbox" />Allow workspace edits</label>
      <label>Expires after days (blank means until revoked)<input name="days" type="number" min={1} max={3650} /></label>
      <button>Create access token</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {config && <><textarea aria-label="Remote MCP configuration" readOnly rows={10} value={config} /><button onClick={() => setConfig("")}>Hide token</button></>}
    {grants.filter(g => !g.revoked_at).map(g => <p key={g.id}>{g.name} <button onClick={() => void window.tasks.request("/v1/projects/keys/" + g.id, "DELETE").then(load).catch(e => setError(String(e)))}>Revoke {g.name}</button></p>)}
    <p>Workspace exports exclude server login and vault secrets. Stop the server and use the CLI to restore a backup.</p>
  </section>;
}
