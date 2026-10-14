import { useEffect, useState } from "react";
import type { Item } from "./types";
export function LocalSSH({ item }: { item: Item }) {
  const [saved, setSaved] = useState<any>(),
    [host, setHost] = useState(""),
    [username, setUsername] = useState(""),
    [port, setPort] = useState(22),
    [alias, setAlias] = useState("alt-" + item.id.slice(0, 8)),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  const load = async () => {
    const data = await window.tasks.request("/v1/ssh");
    const row = data.aliases.find((r: any) => r.resource_id === item.id);
    setSaved(row);
    if (row) {
      setHost(row.host);
      setUsername(row.username);
      setPort(row.port);
      setAlias(row.alias);
    }
  };
  useEffect(() => {
    void load().catch((e) => setMessage(e.message));
  }, [item.id]);
  const run = async (fn: () => Promise<any>) => {
    setBusy(true);
    setMessage("");
    try {
      const result = await fn();
      await load();
      if (result) setMessage("Local SSH configuration updated.");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const settings = { host, username, port, alias };
  return (
    <section className="connected-resource-settings">
      <h3>SSH on this computer</h3>
      <p>Private keys and SSH settings stay on this computer.</p>
      <label>
        Host
        <input
          value={host}
          onChange={(e) => setHost(e.target.value)}
          placeholder="server.example.com"
        />
      </label>
      <label>
        Username
        <input value={username} onChange={(e) => setUsername(e.target.value)} />
      </label>
      <label>
        Port
        <input
          type="number"
          min={1}
          max={65535}
          value={port}
          onChange={(e) => setPort(Number(e.target.value))}
        />
      </label>
      <label>
        SSH alias
        <input value={alias} onChange={(e) => setAlias(e.target.value)} />
      </label>
      <button
        disabled={busy || !host || !username}
        onClick={() =>
          void run(() => window.tasks.importSshKey(item.id, settings))
        }
      >
        Choose private key & configure SSH
      </button>
      <button
        disabled={busy || !host || !username}
        onClick={() =>
          void run(() =>
            window.tasks.request("/v1/ssh", "POST", {
              ...settings,
              resource_id: item.id,
              agent: true,
            }),
          )
        }
      >
        Use SSH agent
      </button>
      {saved && (
        <>
          <code>ssh {saved.alias}</code>
          <button className="desktop-only"
            disabled={busy}
            onClick={() =>
              void run(() =>
                window.tasks.request("/v1/ssh/open", "POST", {
                  resource_id: item.id,
                }),
              )
            }
          >
            Open terminal
          </button>
          <button
            disabled={busy}
            onClick={() =>
              void run(() =>
                window.tasks.request("/v1/ssh", "DELETE", {
                  resource_id: item.id,
                }),
              )
            }
          >
            Remove local configuration
          </button>
        </>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
