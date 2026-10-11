import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ProjectsApp } from "./ProjectsApp";
import type { Item } from "./project-board/types";
import "./shell.css";
import icon from "../assets/alshival-128x128.png";
const api = window.tasks.request;
function App() {
  const [tab, setTab] = useState("Projects"),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [items, setItems] = useState<Item[]>([]),
    [resource, setResource] = useState(""),
    [settings, setSettings] = useState<any>({}),
    [logs, setLogs] = useState<any[]>([]),
    [monitor, setMonitor] = useState<any>({ monitors: [], incidents: [] }),
    [connections, setConnections] = useState<any[]>([]),
    [workspaces, setWorkspaces] = useState<any[]>([]),
    [selectedConnection, setConnection] = useState(""),
    [conflicts, setConflicts] = useState<any[]>([]),
    [targets, setTargets] = useState<any[]>([]),
    [drafts, setDrafts] = useState<any[]>([]),
    [attachments, setAttachments] = useState<any[]>([]),
    [fileItem, setFileItem] = useState("");
  const work = async (fn: () => Promise<any>) => {
    setError("");
    try {
      const v = await fn();
      setMessage(v?.message ?? "Saved");
      return v;
    } catch (e) {
      setError((e as Error).message);
    }
  };
  async function refresh() {
    const all: Item[] = [];
    let next = "";
    do {
      const page = await api(
        "/v1/projects/items?after=" + encodeURIComponent(next),
      );
      all.push(...page.items);
      next = page.next ?? "";
    } while (next);
    setItems(all);
    setSettings(await api("/v1/settings"));
    setMonitor(await api("/v1/monitoring"));
    setConnections((await api("/v1/portal/connections")).connections);
    setConflicts((await api("/v1/conflicts")).conflicts);
    setDrafts((await api("/v1/drafts")).drafts);
    setTargets((await api("/v1/forwarding")).targets);
  }
  useEffect(() => {
    void refresh().catch((e) => setError(e.message));
    const t = setInterval(() => void refresh().catch(() => {}), 5000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (fileItem)
      void api("/v1/attachments?item_id=" + fileItem)
        .then((x) => setAttachments(x.attachments))
        .catch((e) => setError(e.message));
  }, [fileItem]);
  const resources = items.filter(
    (i) => i.kind === "resource" && !i.data.deleted,
  );
  const data = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    return Object.fromEntries(new FormData(e.currentTarget));
  };
  return (
    <main>
      <header className="shell-header">
        <img className="brand-icon" src={icon} alt="" />
        <strong>Alshival.Ai</strong>
        <nav aria-label="Application">
          {["Projects", "Resources", "Logs", "Connections", "Settings"].map(
            (x) => (
              <button
                aria-current={tab === x ? "page" : undefined}
                onClick={() => {
                  setTab(x);
                  setMessage("");
                }}
                key={x}
              >
                {x}
              </button>
            ),
          )}
        </nav>
        <span className="local-label">
          {settings.paused
            ? "Background paused"
            : connections.some((c) =>
                  ["disconnected", "revoked"].includes(c.state),
                )
              ? "Local · disconnected cache retained"
              : "Local workspace"}
        </span>
      </header>
      {error && (
        <p role="alert" className="notice error">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="notice">
          {message}
        </p>
      )}
      <div className={tab === "Projects" ? "projects-frame" : "page"}>
        {tab === "Projects" && (
          <ProjectsApp storageNamespace="alshitasks.local" />
        )}
        {tab === "Resources" && (
          <>
            <h1>Resources</h1>
            <p>Monitoring and SSH settings stay on this computer.</p>
            <label>
              Resource
              <select
                value={resource}
                onChange={(e) => setResource(e.target.value)}
              >
                <option value="">Choose a resource</option>
                {resources.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.data.title}
                  </option>
                ))}
              </select>
            </label>
            {resource && (
              <div className="grid">
                <section>
                  <h2>Health checks</h2>
                  <form
                    onSubmit={(e) => {
                      const f = data(e);
                      void work(() =>
                        api("/v1/monitoring", "POST", {
                          resource_id: resource,
                          config: {
                            kind: f.kind,
                            ...(f.url ? { url: f.url } : {}),
                            ...(f.host ? { host: f.host } : {}),
                            ...(f.port ? { port: Number(f.port) } : {}),
                            ...(f.database ? { database: f.database } : {}),
                            ...(f.username ? { username: f.username } : {}),
                            ...(f.credential
                              ? { credential: f.credential }
                              : {}),
                            interval: 120,
                          },
                        }),
                      );
                    }}
                  >
                    <label>
                      Adapter
                      <select name="kind">
                        {[
                          "https",
                          "tcp",
                          "heartbeat",
                          "postgresql",
                          "mysql",
                          "mariadb",
                          "redis",
                          "mongodb",
                        ].map((k) => (
                          <option key={k}>{k}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      HTTPS URL
                      <input name="url" type="url" />
                    </label>
                    <label>
                      Host
                      <input name="host" />
                    </label>
                    <label>
                      Port
                      <input name="port" type="number" min="1" max="65535" />
                    </label>
                    <label>
                      Database
                      <input name="database" />
                    </label>
                    <label>
                      Username
                      <input name="username" />
                    </label>
                    <label>
                      Credential ID
                      <input name="credential" />
                    </label>
                    <button>Save monitoring</button>
                  </form>
                </section>
                <section>
                  <h2>SSH connection</h2>
                  <form
                    onSubmit={(e) => {
                      const f = data(e);
                      void work(() =>
                        api("/v1/ssh", "POST", {
                          resource_id: resource,
                          alias: f.alias,
                          host: f.host,
                          username: f.username,
                          port: Number(f.port) || 22,
                          identity_file: f.identity_file || null,
                          agent: Boolean(f.agent),
                        }),
                      );
                    }}
                  >
                    <label>
                      Alias
                      <input
                        name="alias"
                        placeholder="alt-my-server"
                        required
                      />
                    </label>
                    <label>
                      Host
                      <input name="host" required />
                    </label>
                    <label>
                      Username
                      <input name="username" required />
                    </label>
                    <label>
                      Port
                      <input name="port" type="number" defaultValue="22" />
                    </label>
                    <label>
                      Existing private key path
                      <input name="identity_file" />
                    </label>
                    <label className="check">
                      <input type="checkbox" name="agent" />
                      Use SSH agent
                    </label>
                    <button>Save alias</button>
                  </form>
                  <button
                    onClick={() =>
                      void work(() =>
                        api("/v1/ssh/open", "POST", { resource_id: resource }),
                      )
                    }
                  >
                    Open terminal
                  </button>
                  <button
                    onClick={() =>
                      void work(() =>
                        api("/v1/ssh", "DELETE", { resource_id: resource }),
                      )
                    }
                  >
                    Revoke alias
                  </button>
                </section>
              </div>
            )}
            <h2>Confirmed health</h2>
            <ul>
              {monitor.monitors.map((m: any) => (
                <li key={m.resource_id}>
                  {resources.find((r) => r.id === m.resource_id)?.data.title ??
                    m.resource_id}
                  : <strong>{m.health}</strong> ·{" "}
                  {m.detail ?? "Awaiting two checks"}
                </li>
              ))}
            </ul>
            <h2>Incident history</h2>
            {monitor.incidents.map((i: any) => (
              <p key={i.id}>
                {i.created_at} · {i.health} · {i.detail}
              </p>
            ))}
          </>
        )}
        {tab === "Logs" && (
          <>
            <h1>Resource logs</h1>
            <form
              className="filters"
              onSubmit={(e) => {
                const f = data(e);
                void work(async () =>
                  setLogs(
                    (
                      await api(
                        (f.source === "portal"
                          ? "/v1/portal/logs?"
                          : "/v1/logs?") +
                          new URLSearchParams(
                            Object.fromEntries(
                              Object.entries(f).filter(
                                ([key]) => key !== "source",
                              ),
                            ) as Record<string, string>,
                          ),
                      )
                    ).events,
                  ),
                );
              }}
            >
              <label>
                Resource
                <select name="resource_id">
                  <option value="">All resources</option>
                  {resources.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.data.title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Source
                <select name="source">
                  <option value="local">This computer</option>
                  <option value="portal">Connected workspace</option>
                </select>
              </label>
              <label>
                Severity
                <select name="severity">
                  <option value="">All levels</option>
                  {["debug", "info", "warning", "error", "critical"].map(
                    (l) => (
                      <option key={l}>{l}</option>
                    ),
                  )}
                </select>
              </label>
              <label>
                Text
                <input name="text" />
              </label>
              <label>
                Since
                <input name="since" type="datetime-local" />
              </label>
              <label>
                Until
                <input name="until" type="datetime-local" />
              </label>
              <button>Search</button>
            </form>
            <p>
              {settings.usage?.events ?? 0} events ·{" "}
              {Math.round((settings.usage?.bytes ?? 0) / 1024)} KiB stored ·
              retention {settings.retention?.days ?? 7} days
            </p>
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Level</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((l) => (
                  <tr key={l.resource + l.id}>
                    <td>{l.timestamp}</td>
                    <td>{l.severity}</td>
                    <td>
                      {l.message}
                      <details>
                        <summary>Attributes</summary>
                        <pre>{JSON.stringify(l.attributes, null, 2)}</pre>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h2>Outbound forwarding</h2>
            <form
              onSubmit={(e) => {
                const f = data(e);
                void work(() => api("/v1/forwarding", "POST", f));
              }}
            >
              <label>
                Resource
                <select name="resource_id" required>
                  {resources.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.data.title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Destination
                <input
                  name="destination"
                  type="url"
                  placeholder="https://collector.example/v1/logs"
                  required
                />
              </label>
              <label>
                Credential ID
                <input name="credential" required />
              </label>
              <button>Add forwarding</button>
            </form>
            {targets.map((t) => (
              <p key={t.id}>
                {t.destination} · {t.enabled ? "Enabled" : "Disabled"}{" "}
                <button
                  onClick={() =>
                    void work(() =>
                      api("/v1/forwarding", "DELETE", { id: t.id }),
                    )
                  }
                >
                  Disable
                </button>
              </p>
            ))}
            <p>
              Failures retry in a bounded queue. Local ingestion continues
              during destination outages.
            </p>
          </>
        )}
        {tab === "Connections" && (
          <>
            <h1>Portal connections</h1>
            <p>
              Local boards stay local. Select a workspace to download; existing
              local boards are never uploaded automatically.
            </p>
            <form
              onSubmit={(e) => {
                const f = data(e);
                void work(() => window.tasks.connect(String(f.origin)));
              }}
            >
              <label>
                Portal URL
                <input
                  name="origin"
                  type="url"
                  defaultValue={settings.portal_url || "https://alshival.ai"}
                  required
                />
              </label>
              <button>Connect in browser</button>
            </form>
            {connections.map((c) => (
              <section key={c.id}>
                <h2>{c.origin}</h2>
                <p>
                  {c.state} · {c.workspace || "Choose a workspace"}
                </p>
                <button
                  onClick={() =>
                    void work(async () => {
                      setConnection(c.id);
                      setWorkspaces(
                        (await api("/v1/portal/workspaces?connection=" + c.id))
                          .workspaces,
                      );
                    })
                  }
                >
                  Choose workspace
                </button>
                <button
                  onClick={() =>
                    void work(() =>
                      api("/v1/portal/disconnect", "POST", {
                        connection: c.id,
                      }),
                    )
                  }
                >
                  Disconnect
                </button>
              </section>
            ))}
            {workspaces.map((w) => (
              <p key={w.id}>
                {w.name}
                <button
                  onClick={() =>
                    void work(() =>
                      api("/v1/portal/select", "POST", {
                        connection: selectedConnection,
                        workspace: w.id,
                      }),
                    )
                  }
                >
                  Cache workspace
                </button>
              </p>
            ))}
            <h2>Local drafts</h2>
            {drafts.map((d) => (
              <section key={d.id}>
                <strong>{d.title ?? "Draft"}</strong>
                <p>{d.reason}</p>
                <button
                  onClick={() =>
                    void work(() =>
                      api("/v1/drafts/resubmit", "POST", { id: d.id }),
                    )
                  }
                >
                  Review current permission and retry
                </button>
              </section>
            ))}
            <h2>Conflicting edits</h2>
            {conflicts.map((c) => (
              <section key={c.id}>
                <h3>{JSON.parse(c.local).data?.title ?? "Conflict"}</h3>
                <div className="grid">
                  <pre>{JSON.stringify(JSON.parse(c.local).data, null, 2)}</pre>
                  <pre>
                    {JSON.stringify(JSON.parse(c.remote).data, null, 2)}
                  </pre>
                </div>
                <button
                  onClick={() =>
                    void work(() =>
                      api("/v1/conflicts/resolve", "POST", {
                        id: c.id,
                        choice: "local",
                      }),
                    )
                  }
                >
                  Keep local draft
                </button>
                <button
                  onClick={() =>
                    void work(() =>
                      api("/v1/conflicts/resolve", "POST", {
                        id: c.id,
                        choice: "remote",
                      }),
                    )
                  }
                >
                  Use shared version
                </button>
              </section>
            ))}
            <p>
              Disconnect stops remote activity. Retained offline copies are
              marked disconnected and cannot be remotely guaranteed erased.
            </p>
          </>
        )}
        {tab === "Settings" && (
          <>
            <h1>Settings</h1>
            <h2>Local assignees</h2>
            <form
              onSubmit={(e) => {
                const f = data(e);
                void work(() =>
                  api("/v1/settings", "PATCH", {
                    local_members: [
                      ...(settings.local_members || []),
                      { id: "contact:" + crypto.randomUUID(), name: f.name },
                    ],
                  }),
                );
              }}
            >
              <label>
                Name
                <input name="name" required maxLength={120} />
              </label>
              <button>Add local contact</button>
            </form>
            {(settings.local_members || []).map((m: any) => (
              <p key={m.id}>
                {m.name}{" "}
                <button
                  onClick={() =>
                    void work(() =>
                      api("/v1/settings", "PATCH", {
                        local_members: settings.local_members.filter(
                          (other: any) => other.id !== m.id,
                        ),
                      }),
                    )
                  }
                >
                  Remove contact
                </button>
              </p>
            ))}

            <section>
              <h2>Background service</h2>
              <p>Closing the window leaves the service running after setup.</p>
              <button
                onClick={() =>
                  void work(() =>
                    api("/v1/settings", "PATCH", { paused: !settings.paused }),
                  )
                }
              >
                {settings.paused ? "Resume" : "Pause"} background work
              </button>
              <button
                onClick={() => void work(() => window.tasks.service("stop"))}
              >
                Stop service
              </button>
              <button
                onClick={() => void work(() => window.tasks.service("start"))}
              >
                Start service
              </button>
            </section>
            <section>
              <h2>Backup and restore</h2>
              <button onClick={() => void work(() => window.tasks.backup())}>
                Export complete backup
              </button>
              <button onClick={() => void work(() => window.tasks.restore())}>
                Restore backup
              </button>
              <p>
                Backups include task data, attachments and logs. Secrets are
                held separately in your login keyring.
              </p>
            </section>
            <section>
              <h2>Attachments</h2>
              <label>
                Record
                <select
                  value={fileItem}
                  onChange={(e) => setFileItem(e.target.value)}
                >
                  <option value="">Choose a record</option>
                  {items
                    .filter((i) => !i.data.deleted)
                    .map((i) => (
                      <option key={i.id} value={i.id}>
                        {i.data.title}
                      </option>
                    ))}
                </select>
              </label>
              <button
                disabled={!fileItem}
                onClick={() =>
                  void work(async () => {
                    await window.tasks.importAttachment(fileItem);
                    setAttachments(
                      (await api("/v1/attachments?item_id=" + fileItem))
                        .attachments,
                    );
                  })
                }
              >
                Import file
              </button>
              {attachments.map((a) => (
                <p key={a.id}>
                  {a.name} · {a.size} bytes{" "}
                  <button
                    onClick={() =>
                      void work(() => window.tasks.saveAttachment(a.id))
                    }
                  >
                    Save copy
                  </button>
                </p>
              ))}
            </section>
            <section>
              <h2>Local API and retention</h2>
              <form
                onSubmit={(e) => {
                  const f = data(e);
                  void work(() =>
                    api("/v1/settings", "PATCH", {
                      http_port: f.port ? Number(f.port) : null,
                      retention: {
                        days: Number(f.days),
                        max_bytes: Number(f.mb) * 1024 ** 2,
                      },
                    }),
                  );
                }}
              >
                <label>
                  Loopback HTTP port (blank disables; restart required)
                  <input
                    name="port"
                    type="number"
                    min="1024"
                    max="65535"
                    defaultValue={settings.http_port ?? ""}
                  />
                </label>
                <label>
                  Retention days
                  <input
                    name="days"
                    type="number"
                    defaultValue={settings.retention?.days ?? 7}
                    min="1"
                    max="365"
                  />
                </label>
                <label>
                  Storage limit MiB
                  <input
                    name="mb"
                    type="number"
                    defaultValue={
                      (settings.retention?.max_bytes ?? 1024 ** 3) / 1024 ** 2
                    }
                    min="1"
                    max="10240"
                  />
                </label>
                <button>Save</button>
              </form>
              <code>alshitasks mcp</code>
              <p>
                Local MCP is read-only by default. Create an explicit scoped
                keyring grant with <code>alshitasks grant</code>.
              </p>
            </section>
            <section>
              <h2>Store a credential</h2>
              <form
                onSubmit={(e) => {
                  const f = data(e);
                  const form = e.currentTarget;
                  void work(async () => {
                    const v = await api("/v1/secret", "POST", {
                      value: f.value,
                    });
                    form.reset();
                    return { message: "Credential ID: " + v.id };
                  });
                }}
              >
                <label>
                  Secret
                  <input
                    name="value"
                    type="password"
                    autoComplete="off"
                    required
                  />
                </label>
                <button>Save to login keyring</button>
              </form>
            </section>
          </>
        )}
      </div>
      <footer>
        Alshival.Ai · local first ·{" "}
        <button
          onClick={() =>
            void work(() =>
              window.tasks.external(
                "https://github.com/Alshival-Ai/alshitasks",
              ),
            )
          }
        >
          Source
        </button>
      </footer>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
