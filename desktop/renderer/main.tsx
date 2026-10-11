import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ProjectsApp } from "./ProjectsApp";
import type { Item } from "./project-board/types";
import "./shell.css";
import { readDeviceState, writeDeviceState } from "./deviceState";
const api = window.tasks.request;
function NavIcon({ name }: { name: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {name === "Projects" ? (
        <>
          <path d="M3 7a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
          <path d="m9 14 2 2 4-4" />
        </>
      ) : name === "Settings" ? (
        <>
          <circle cx="12" cy="12" r="7" />
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2" />
        </>
      ) : (
        <path d="m9 5 7 7-7 7" />
      )}
    </svg>
  );
}
function App() {
  const [expanded, setExpanded] = useState(
    () => readDeviceState("alshitasks", "sidebar") === true,
  );
  useEffect(() => {
    document.body.classList.add("alshival-app");
    document.body.classList.toggle("app-menu-open", expanded);
    writeDeviceState("alshitasks", "sidebar", expanded);
  }, [expanded]);
  const [tab, setTab] = useState("Projects"),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [items, setItems] = useState<Item[]>([]),
    [settings, setSettings] = useState<any>({}),
    [connections, setConnections] = useState<any[]>([]),
    [workspaces, setWorkspaces] = useState<any[]>([]),
    [selectedConnection, setConnection] = useState(""),
    [conflicts, setConflicts] = useState<any[]>([]),
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
    setConnections((await api("/v1/portal/connections")).connections);
    setConflicts((await api("/v1/conflicts")).conflicts);
    setDrafts((await api("/v1/drafts")).drafts);
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
  const data = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    return Object.fromEntries(new FormData(e.currentTarget));
  };
  return (
    <>
      <aside className="app-sidebar" aria-label="Alshival navigation">
        <div className="app-sidebar-controls">
          <button
            type="button"
            data-app-toggle
            aria-controls="app-menu"
            aria-expanded={expanded}
            aria-label={expanded ? "Collapse menu" : "Expand menu"}
            title={expanded ? "Collapse menu" : "Expand menu"}
            onClick={() => setExpanded(!expanded)}
          >
            <NavIcon name="toggle" />
          </button>
        </div>
        <div id="app-menu">
          <nav aria-label="Application">
            {["Projects", "Settings"].map((x) => (
              <a
                key={x}
                href={"#" + x.toLowerCase()}
                title={x}
                aria-label={x}
                aria-current={tab === x ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  setTab(x);
                  setMessage("");
                }}
              >
                <span className="app-selection-cube" aria-hidden="true">
                  {["front", "back", "right", "left", "top", "bottom"].map(
                    (face) => (
                      <span
                        key={face}
                        className={"app-cube-face app-cube-face--" + face}
                      />
                    ),
                  )}
                </span>
                <NavIcon name={x} />
                <span>{x}</span>
              </a>
            ))}
          </nav>
        </div>
      </aside>
      <main className="app-content">
        <header className="app-header">
          <a
            className="app-wordmark"
            href="#projects"
            onClick={(e) => {
              e.preventDefault();
              setTab("Projects");
            }}
          >
            Alshival.Ai
          </a>
          <span className="app-header-context">{tab}</span>
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
          {tab === "Settings" && (
            <>
              <h1>Settings</h1>
              <section aria-labelledby="connections-heading">
                <h2 id="connections-heading">Connections</h2>
                <p>
                  Local boards stay local. Select a workspace to download;
                  existing local boards are never uploaded automatically.
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
                      defaultValue={
                        settings.portal_url || "https://alshival.ai"
                      }
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
                            (
                              await api(
                                "/v1/portal/workspaces?connection=" + c.id,
                              )
                            ).workspaces,
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
                <h3>Local drafts</h3>
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
                <h3>Conflicting edits</h3>
                {conflicts.map((c) => (
                  <section key={c.id}>
                    <h3>{JSON.parse(c.local).data?.title ?? "Conflict"}</h3>
                    <div className="grid">
                      <pre>
                        {JSON.stringify(JSON.parse(c.local).data, null, 2)}
                      </pre>
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
              </section>
            </>
          )}
          {tab === "Settings" && (
            <>
              <section>
                <h2>Local assignees</h2>
                <form
                  onSubmit={(e) => {
                    const f = data(e);
                    void work(() =>
                      api("/v1/settings", "PATCH", {
                        local_members: [
                          ...(settings.local_members || []),
                          {
                            id: "contact:" + crypto.randomUUID(),
                            name: f.name,
                          },
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
              </section>
              <section>
                <h2>Background service</h2>
                <p>
                  Closing the window leaves the service running after setup.
                </p>
                <button
                  onClick={() =>
                    void work(() =>
                      api("/v1/settings", "PATCH", {
                        paused: !settings.paused,
                      }),
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
      </main>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
