import { useEffect, useRef, useState } from "react";
import type { Item } from "./types";
import { selectedWorkspace } from "./api";

type Event = {
  id?: string;
  timestamp: string;
  severity: string;
  message: string;
  attributes?: unknown;
};
type Setup = {
  connected: boolean;
  http_port: number | null;
  keys: { id: string; name: string; revoked_at: string | null }[];
};
export function ResourceLogs({ item }: { item: Item }) {
  const workspace = selectedWorkspace();
  const [setup, setSetup] = useState<Setup>(),
    [source, setSource] = useState("local"),
    [severity, setSeverity] = useState(""),
    [text, setText] = useState(""),
    [since, setSince] = useState(""),
    [until, setUntil] = useState(""),
    [events, setEvents] = useState<Event[]>([]),
    [cursor, setCursor] = useState<string | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [name, setName] = useState("Log sender"),
    [token, setToken] = useState("");
  const generation = useRef(0);
  const params = () =>
    new URLSearchParams({ resource_id: item.id, workspace_id: workspace });
  const refreshSetup = async () =>
    setSetup(await window.tasks.request("/v1/logs/setup?" + params()));
  useEffect(() => {
    void refreshSetup().catch((e) => setError(e.message));
  }, [item.id, workspace]);
  useEffect(() => {
    generation.current++;
    setEvents([]);
    setCursor(null);
    setError("");
    setBusy(false);
    return () => {
      generation.current++;
    };
  }, [source, severity, text, since, until, workspace]);
  async function load(more = false) {
    const current = ++generation.current;
    setBusy(true);
    setError("");
    try {
      const q = params();
      if (severity) q.set("severity", severity);
      if (text) q.set("text", text);
      if (since) q.set("since", new Date(since).toISOString());
      if (until) q.set("until", new Date(until).toISOString());
      if (more && cursor) q.set("cursor", cursor);
      const result = await window.tasks.request(
        (source === "local" ? "/v1/logs?" : "/v1/portal/logs?") + q,
      );
      if (current !== generation.current) return;
      setEvents((old) =>
        more ? [...old, ...(result.events ?? [])] : (result.events ?? []),
      );
      setCursor(source === "local" ? result.next_cursor : null);
    } catch (e) {
      if (current === generation.current) setError((e as Error).message);
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }
  async function run(fn: () => Promise<void>) {
    setError("");
    setBusy(true);
    try {
      await fn();
      await refreshSetup();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const example = `read -rs -p 'Sender token: ' ALSHIVAL_LOG_TOKEN; echo\ncurl --fail-with-body http://127.0.0.1:${setup?.http_port ?? "PORT"}/v1/logs \\\n  -H "Authorization: Bearer $ALSHIVAL_LOG_TOKEN" \\\n  -H 'Content-Type: application/json' \\\n  --data '${JSON.stringify({ resource_id: item.id, events: [{ id: "example-event-1", timestamp: new Date().toISOString(), severity: "info", message: "Worker started", attributes: {} }] })}'\nunset ALSHIVAL_LOG_TOKEN`;
  return (
    <section className="connected-resource-settings resource-logs">
      <h3>Logs</h3>
      <label>
        Source{" "}
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="local">On this device</option>
          {setup?.connected && <option value="portal">Portal</option>}
        </select>
      </label>
      <p>
        {source === "local"
          ? "Stored on this computer. Logs are not included in project synchronization."
          : "Queries your portal account. An active connection and internet access are required."}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void load();
        }}
      >
        <label>
          Severity{" "}
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
          >
            <option value="">All</option>
            {["debug", "info", "warning", "error", "critical"].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
        <label>
          Message contains{" "}
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={16000}
          />
        </label>
        <label>
          From (local time){" "}
          <input
            type="datetime-local"
            value={since}
            onChange={(e) => setSince(e.target.value)}
          />
        </label>
        <label>
          Until (local time){" "}
          <input
            type="datetime-local"
            value={until}
            onChange={(e) => setUntil(e.target.value)}
          />
        </label>
        <button disabled={busy}>Refresh logs</button>
      </form>
      {error && <p role="alert">{error}</p>}
      <div aria-live="polite">
        {busy ? "Loading…" : `${events.length} events loaded`}
      </div>
      {events.map((event, index) => (
        <article key={`${event.id}-${index}`}>
          <small>
            {event.timestamp} · {event.severity}
          </small>
          <pre>{event.message}</pre>
          {Boolean(event.attributes) && (
            <details>
              <summary>Attributes</summary>
              <pre>{JSON.stringify(event.attributes, null, 2)}</pre>
            </details>
          )}
        </article>
      ))}
      {cursor && (
        <button disabled={busy} onClick={() => void load(true)}>
          Load more
        </button>
      )}
      {source === "local" && (
        <details>
          <summary>Send logs here</summary>
          <p>
            {setup?.http_port
              ? `Listening address: http://127.0.0.1:${setup.http_port}/v1/logs`
              : "Enable the loopback HTTP API in Settings → Local API and retention, then restart the service."}
          </p>
          <p>
            For other machines, use an SSH tunnel or an HTTPS reverse proxy.
            Senders must buffer and retry while this computer is asleep or
            unavailable.
          </p>
          <label>
            Sender name{" "}
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={120}
            />
          </label>
          <button
            disabled={busy || !name.trim()}
            onClick={() =>
              void run(async () => {
                const result = await window.tasks.request(
                  "/v1/projects/keys?" + params(),
                  "POST",
                  {
                    name: name.trim(),
                    scopes: ["logs:write"],
                    resource_id: item.id,
                  },
                );
                setToken(result.token);
              })
            }
          >
            Create sender token
          </button>
          {token && (
            <div>
              <p>
                Copy this token now. It will not be shown again after dismissal.
              </p>
              <input
                aria-label="New sender token"
                readOnly
                value={token}
                onFocus={(e) => e.target.select()}
              />
              <button onClick={() => setToken("")}>Dismiss token</button>
            </div>
          )}
          {setup?.keys
            .filter((k) => !k.revoked_at)
            .map((k) => (
              <p key={k.id}>
                {k.name}{" "}
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await window.tasks.request(
                        "/v1/projects/keys/" + k.id,
                        "DELETE",
                      );
                      setToken("");
                    })
                  }
                >
                  Revoke {k.name}
                </button>
              </p>
            ))}
          <label>
            Sender example (select to copy)
            <textarea
              rows={10}
              readOnly
              value={example}
              onFocus={(e) => e.target.select()}
            />
          </label>
          <p>
            Reuse event IDs when retrying; use new IDs for new events. A
            successful request confirms local persistence. Keep tokens out of
            source control.
          </p>
        </details>
      )}
    </section>
  );
}
