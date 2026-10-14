import { useEffect, useState } from "react";
import type { Item } from "./types";
import { selectedWorkspace } from "./api";
const empty = {
  name: "",
  kind: "https",
  url: "",
  host: "",
  port: "",
  database: "",
  username: "",
  credential: "",
  expected_status: "",
  interval: 120,
  timeout: 8,
  expected_seconds: 240,
  tls: true,
  required: true,
};
export function ResourceMonitoring({ item }: { item: Item }) {
  const [data, setData] = useState<any>({
      checks: [],
      incidents: [],
      history: [],
      resources: [],
    }),
    [draft, setDraft] = useState(empty),
    [editing, setEditing] = useState<any>(),
    [source, setSource] = useState("local"),
    [connected, setConnected] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [secret, setSecret] = useState(""),
    [heartbeatToken, setHeartbeatToken] = useState(""),
    [maintenanceMinutes, setMaintenanceMinutes] = useState(60);
  const workspace = selectedWorkspace();
  const query = new URLSearchParams({
    resource_id: item.id,
    workspace_id: workspace,
  });
  const load = async () =>
    setData(
      await window.tasks.request(
        (source === "local" ? "/v1/monitoring?" : "/v1/portal/monitoring?") +
          query,
      ),
    );
  useEffect(() => {
    let active = true;
    const refresh = () =>
      window.tasks
        .request(
          (source === "local" ? "/v1/monitoring?" : "/v1/portal/monitoring?") +
            query,
        )
        .then((v) => {
          if (active) setData(v);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    setData({ checks: [], incidents: [], history: [], resources: [] });
    setError("");
    void refresh();
    const timer = setInterval(refresh, 10000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [source, item.id, workspace]);
  useEffect(() => {
    void window.tasks
      .request("/v1/logs/setup?" + query)
      .then((v) => setConnected(v.connected))
      .catch((e) => setError(e.message));
  }, [item.id, workspace]);
  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const action = (check: any, action: string) =>
    run(() =>
      window.tasks.request("/v1/monitoring/actions?" + query, "POST", {
        id: check.id,
        revision: check.revision,
        action,
        minutes: maintenanceMinutes,
      }),
    );
  const health =
    data.resources?.find((r: any) => r.resource_id === item.id)?.health ??
    "unknown";
  return (
    <section className="connected-resource-settings resource-monitoring">
      <h3>Monitoring</h3>
      <label>
        Source{" "}
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="local">On this device</option>
          {connected && <option value="portal">Portal</option>}
        </select>
      </label>
      <p>
        {source === "local"
          ? "Checks run on this computer. Settings and credentials stay here. Local alerts appear in your inbox and desktop toasts only."
          : "Portal checks run separately. The portal controls email/SMS delivery according to your account settings."}
      </p>
      <button disabled={busy} onClick={() => void run(load)}>
        Refresh monitoring
      </button>
      {error && <p role="alert">{error}</p>}
      {source === "portal" && data.cache && <p role="status">{data.cache.error || (data.cache.state === "loading" ? "Loading portal monitoring?" : data.cache.stale ? "Showing cached monitoring; refresh pending." : "Portal cache is up to date.")}</p>}
      {source === "portal" ? (
        <>
          <button
            onClick={() =>
              void window.tasks.external(
                new URL(
                  data.resources?.[0]?.destination || "/",
                  "https://alshival.ai",
                ).href,
              )
            }
          >
            Manage checks in portal
          </button>
          {(data.resources ?? []).map((r: any) => (
            <article key={r.id}>
              <h4>{r.name}</h4>
              <p>Health: {r.health || r.status || "Unknown"}</p>
              {(r.monitors ?? []).map((m: any) => (
                <div key={m.id}>
                  <h5>{m.name}</h5>
                  <p>
                    {m.kind} · {m.enabled ? m.health : "Paused"} ·{" "}
                    {m.required ? "Required" : "Optional"}
                  </p>
                  {m.maintenance_until && (
                    <p>
                      Maintenance until{" "}
                      {new Date(m.maintenance_until).toLocaleString()}
                    </p>
                  )}
                </div>
              ))}
              {(r.incidents ?? []).map((i: any) => (
                <p key={i.id}>
                  {i.kind} incident · {i.opened_at} ·{" "}
                  {i.closed_at ? "Closed" : "Open"} · {i.observations}{" "}
                  observations
                </p>
              ))}
              <p>
                Last checked:{" "}
                {r.checked_at
                  ? new Date(r.checked_at).toLocaleString()
                  : "Never"}
              </p>
            </article>
          ))}
        </>
      ) : (
        <>
          <p role="status">
            Resource health: <strong>{health}</strong>
          </p>
          <label>
            Maintenance duration (minutes)
            <input
              type="number"
              min={1}
              max={43200}
              value={maintenanceMinutes}
              onChange={(e) => setMaintenanceMinutes(Number(e.target.value))}
            />
          </label>
          {(data.checks ?? []).map((c: any) => (
            <article key={c.id}>
              <h4>{c.name}</h4>
              <p>
                {c.config.kind.toUpperCase()} ·{" "}
                {c.enabled ? c.health : "Paused"} ·{" "}
                {c.required ? "Required" : "Optional"}
              </p>
              <p>{c.detail || "Waiting for the first observation"}</p>
              <small>
                Last checked:{" "}
                {c.last_at
                  ? new Date(c.last_at * 1000).toLocaleString()
                  : "Never"}
              </small>
              {c.maintenance_until > Date.now() / 1000 && (
                <p>
                  Maintenance until{" "}
                  {new Date(c.maintenance_until * 1000).toLocaleString()}
                </p>
              )}
              <div className="connected-actions">
                <button
                  disabled={busy}
                  onClick={() => {
                    setEditing(c);
                    setDraft({
                      ...empty,
                      ...c.config,
                      name: c.name,
                      required: !!c.required,
                      port: String(c.config.port ?? ""),
                      expected_status: String(c.config.expected_status ?? ""),
                    });
                  }}
                >
                  Edit check
                </button>
                <button
                  disabled={busy || !c.enabled}
                  onClick={() => void action(c, "run")}
                >
                  Run now
                </button>
                <button
                  disabled={busy}
                  onClick={() => void action(c, c.enabled ? "pause" : "resume")}
                >
                  {c.enabled ? "Pause" : "Resume"}
                </button>
                <button
                  disabled={busy}
                  onClick={() => void action(c, "maintenance")}
                >
                  Start maintenance
                </button>
                <button
                  disabled={busy || c.maintenance_until <= Date.now() / 1000}
                  onClick={() => void action(c, "end_maintenance")}
                >
                  End maintenance
                </button>
                {c.config.kind === "heartbeat" && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        const value = await window.tasks.request(
                          "/v1/projects/keys",
                          "POST",
                          {
                            name: "Heartbeat: " + c.name,
                            resource_id: item.id,
                            scopes: ["monitor:heartbeat"],
                          },
                        );
                        setHeartbeatToken(
                          JSON.stringify(
                            {
                              token: value.token,
                              endpoint: "/v1/heartbeat",
                              body: { resource_id: item.id, check_id: c.id },
                            },
                            null,
                            2,
                          ),
                        );
                      })
                    }
                  >
                    Create heartbeat sender token
                  </button>
                )}
              </div>
            </article>
          ))}
          {heartbeatToken && (
            <div>
              <p>
                Copy once, then dismiss. Use the loopback API or your configured
                tunnel/proxy. Revoke the token in Settings.
              </p>
              <textarea
                aria-label="Heartbeat sender configuration"
                readOnly
                rows={8}
                value={heartbeatToken}
                onFocus={(e) => e.target.select()}
              />
              <button onClick={() => setHeartbeatToken("")}>
                Dismiss sender token
              </button>
            </div>
          )}
          <details open={!!editing}>
            <summary>{editing ? "Edit check" : "Add health check"}</summary>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  let credential = draft.credential;
                  if (secret) {
                    credential = (
                      await window.tasks.request("/v1/secret", "POST", {
                        value: secret,
                      })
                    ).id;
                    setSecret("");
                  }
                  const config: any = {
                    kind: draft.kind,
                    interval: Number(draft.interval),
                    timeout: Number(draft.timeout),
                  };
                  if (draft.kind === "https") {
                    config.url = draft.url;
                    if (draft.expected_status)
                      config.expected_status = Number(draft.expected_status);
                  } else if (draft.kind === "heartbeat")
                    config.expected_seconds = Number(draft.expected_seconds);
                  else {
                    config.host = draft.host;
                    if (draft.port) config.port = Number(draft.port);
                    if (draft.database) config.database = draft.database;
                    config.tls = draft.tls;
                    if (draft.username) config.username = draft.username;
                    if (credential) config.credential = credential;
                  }
                  await window.tasks.request(
                    "/v1/monitoring/checks?" + query,
                    "POST",
                    {
                      resource_id: item.id,
                      name: draft.name,
                      required: draft.required,
                      config,
                      ...(editing
                        ? { id: editing.id, revision: editing.revision }
                        : {}),
                    },
                  );
                  setEditing(undefined);
                  setDraft(empty);
                });
              }}
            >
              <label>
                Check name
                <input
                  required
                  maxLength={100}
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                />
              </label>
              <label>
                Check type
                <select
                  aria-label="Check type"
                  value={draft.kind}
                  onChange={(e) =>
                    setDraft({ ...draft, kind: e.target.value, port: "" })
                  }
                >
                  {[
                    "https",
                    "tcp",
                    "icmp",
                    "postgresql",
                    "mysql",
                    "mariadb",
                    "redis",
                    "mongodb",
                    "heartbeat",
                  ].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
              {draft.kind === "https" ? (
                <>
                  <label>
                    HTTPS URL
                    <input
                      required
                      type="url"
                      value={draft.url}
                      onChange={(e) =>
                        setDraft({ ...draft, url: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    Expected HTTP status (optional)
                    <input
                      type="number"
                      min={100}
                      max={599}
                      value={draft.expected_status}
                      onChange={(e) =>
                        setDraft({ ...draft, expected_status: e.target.value })
                      }
                    />
                  </label>
                </>
              ) : draft.kind === "heartbeat" ? (
                <label>
                  Expected heartbeat within seconds
                  <input
                    type="number"
                    min={120}
                    max={86400}
                    value={draft.expected_seconds}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        expected_seconds: Number(e.target.value),
                      })
                    }
                  />
                </label>
              ) : (
                <>
                  <label>
                    Host
                    <input
                      required
                      value={draft.host}
                      onChange={(e) =>
                        setDraft({ ...draft, host: e.target.value })
                      }
                    />
                  </label>
                  {draft.kind !== "icmp" && (
                    <label>
                      Port (blank uses default)
                      <input
                        type="number"
                        min={1}
                        max={65535}
                        value={draft.port}
                        onChange={(e) =>
                          setDraft({ ...draft, port: e.target.value })
                        }
                      />
                    </label>
                  )}
                  {!["tcp", "icmp"].includes(draft.kind) && (
                    <>
                      <label>
                        Database
                        <input
                          value={draft.database}
                          onChange={(e) =>
                            setDraft({ ...draft, database: e.target.value })
                          }
                        />
                      </label>
                      <label>
                        Username
                        <input
                          value={draft.username}
                          onChange={(e) =>
                            setDraft({ ...draft, username: e.target.value })
                          }
                        />
                      </label>
                      <label>
                        New password (stored in the login keyring)
                        <input
                          type="password"
                          autoComplete="new-password"
                          value={secret}
                          onChange={(e) => setSecret(e.target.value)}
                        />
                      </label>
                      <label>
                        Existing credential ID
                        <input
                          value={draft.credential}
                          onChange={(e) =>
                            setDraft({ ...draft, credential: e.target.value })
                          }
                        />
                      </label>
                      <label>
                        <input
                          type="checkbox"
                          checked={draft.tls}
                          onChange={(e) =>
                            setDraft({ ...draft, tls: e.target.checked })
                          }
                        />
                        Verify TLS
                      </label>
                    </>
                  )}
                </>
              )}
              <label>
                Interval seconds
                <input
                  type="number"
                  min={30}
                  max={86400}
                  value={draft.interval}
                  onChange={(e) =>
                    setDraft({ ...draft, interval: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                Timeout seconds
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={draft.timeout}
                  onChange={(e) =>
                    setDraft({ ...draft, timeout: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={draft.required}
                  onChange={(e) =>
                    setDraft({ ...draft, required: e.target.checked })
                  }
                />
                Required for resource health
              </label>
              <button disabled={busy}>Save check</button>
              {editing && (
                <button
                  type="button"
                  onClick={() => {
                    setEditing(undefined);
                    setDraft(empty);
                    setSecret("");
                  }}
                >
                  Cancel edit
                </button>
              )}
            </form>
          </details>
          <h4>Incidents</h4>
          {!(data.incidents ?? []).length && <p>No incidents.</p>}
          {(data.incidents ?? []).map((i: any) => (
            <p key={i.id}>
              {i.health} · {i.opened_at} · {i.closed_at ? "Closed" : "Open"} ·{" "}
              {i.observations} observations{" "}
              {i.suppressed ? "· Notifications suppressed" : ""}
            </p>
          ))}
          <h4>Health history</h4>
          {(data.history ?? []).map((h: any) => (
            <p key={h.id}>
              {h.created_at} · {h.previous} → {h.health}{" "}
              {h.suppressed ? "· Suppressed" : ""}
            </p>
          ))}
        </>
      )}
    </section>
  );
}
