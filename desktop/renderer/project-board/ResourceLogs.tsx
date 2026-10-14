import { useEffect, useRef, useState } from "react";
import type { Item } from "./types";
import { selectedWorkspace } from "./api";

type Event = {
  id?: string;
  timestamp: string;
  severity: string;
  message: string;
  attributes?: unknown;
  source?: string;
  trace_id?: string;
  span_id?: string;
};
type Setup = {
  connected: boolean;
  workspace_id: string;
  resource_id: string;
  logs_path: string;
  otlp_path: string;
  http_port: number | null;
  keys: { id: string; name: string; revoked_at: string | null }[];
};
export function ResourceLogs({ item }: { item: Item }) {
  const workspace = selectedWorkspace();
  const [setup, setSetup] = useState<Setup>(),
    [source, setSource] = useState("local"),
    [trace, setTrace] = useState(""),
    [port, setPort] = useState(8765),
    [destination, setDestination] = useState(""),
    [cloudToken, setCloudToken] = useState(""),
    [targets, setTargets] = useState<any[]>([]),
    [severity, setSeverity] = useState(""),
    [text, setText] = useState(""),
    [since, setSince] = useState(""),
    [until, setUntil] = useState(""),
    [events, setEvents] = useState<Event[]>([]),
    [cursor, setCursor] = useState<string | null>(null),
    [cache, setCache] = useState<any>(),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [name, setName] = useState("Log sender"),
    [token, setToken] = useState("");
  const generation = useRef(0);
  const params = () =>
    new URLSearchParams({ resource_id: item.id, workspace_id: workspace });
  const refreshSetup = async () => {
    setSetup(await window.tasks.request("/v1/logs/setup?" + params()));
    setTargets(
      (await window.tasks.request("/v1/forwarding?" + params())).targets,
    );
  };
  useEffect(() => {
    setSetup(undefined);
    setToken("");
    setCloudToken("");
    setDestination("");
    setTargets([]);
    let active = true;
    void Promise.all([
      window.tasks.request("/v1/logs/setup?" + params()),
      window.tasks.request("/v1/forwarding?" + params()),
    ])
      .then(([value, forwarding]) => {
        if (active) {
          setSetup(value);
          setTargets(forwarding.targets);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
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
  }, [source, severity, text, since, until, trace, workspace, item.id]);
  async function load(more = false) {
    const current = ++generation.current;
    setBusy(true);
    setError("");
    try {
      if (!setup) throw new Error("Log setup is still loading.");
      const q = source === "local" ? new URLSearchParams() : params();
      if (severity) q.set("severity", severity);
      if (text) q.set("q", text);
      if (since) q.set("start", new Date(since).toISOString());
      if (until) q.set("end", new Date(until).toISOString());
      if (trace) q.set("trace_id", trace);
      if (more && cursor) q.set("cursor", cursor);
      const result = await window.tasks.request(
        (source === "local" ? setup.logs_path + "?" : "/v1/portal/logs?") + q,
      );
      if (current !== generation.current) return;
      setEvents((old) =>
        more
          ? [...old, ...(result.items ?? result.events ?? [])]
          : (result.items ?? result.events ?? []),
      );
      setCursor(result.next_cursor ?? null);
      setCache(result.cache);
    } catch (e) {
      if (current === generation.current) setError((e as Error).message);
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }
  useEffect(() => {
    if (source !== "portal" || !setup) return;
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [source, setup, severity, text, since, until, trace, workspace, item.id]);
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
  const endpoint = `http://127.0.0.1:${setup?.http_port ?? port}`;
  const example = `read -rs -p 'Sender token: ' ALSHIVAL_LOG_TOKEN; echo\ncurl --fail-with-body '${endpoint}${setup?.logs_path ?? ""}' \\\n  -H "Authorization: Bearer $ALSHIVAL_LOG_TOKEN" \\\n  -H 'Content-Type: application/json' \\\n  --data '${JSON.stringify({ events: [{ severity: "INFO", message: "Worker started", source: "my-app", attributes: {} }] })}'\nunset ALSHIVAL_LOG_TOKEN`;
  const otlpExample = `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=${endpoint}${setup?.otlp_path ?? ""}\nOTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/protobuf\nOTEL_EXPORTER_OTLP_LOGS_HEADERS=Authorization=Bearer%20<sender-token>`;
  const createToken = async (scope: string) => {
    const result = await window.tasks.request(
      "/v1/projects/keys?" + params(),
      "POST",
      {
        name: name.trim(),
        scopes: [scope],
        resource_id: item.id,
      },
    );
    setToken(result.token);
  };
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
          : "Shows cached portal logs and refreshes them in the background."}
      </p>
      {source === "portal" && cache && <p role="status">{cache.error || (cache.state === "loading" ? "Loading portal logs?" : cache.stale ? "Showing cached logs; refresh pending." : "Portal cache is up to date.")}</p>}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void load();
        }}
      >
        <label>
          Severity{" "}
          <select
            aria-label="Severity"
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
          >
            <option value="">All</option>
            {["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
        <label>
          Message contains{" "}
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={500}
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
        <label>
          Trace ID{" "}
          <input
            value={trace}
            onChange={(e) => setTrace(e.target.value)}
            maxLength={32}
            placeholder="32 lowercase hex characters"
          />
        </label>
        <button disabled={busy || !setup}>Refresh logs</button>
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
          {event.source && <small>Source: {event.source}</small>}
          {event.trace_id && (
            <p>
              Trace: <code>{event.trace_id}</code>
              {event.span_id && (
                <>
                  {" "}
                  · Span: <code>{event.span_id}</code>
                </>
              )}
            </p>
          )}
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
              ? `Listening address: ${endpoint}${setup.logs_path}`
              : "Enable the loopback HTTP API in Settings → Local API and retention, then restart the service."}
          </p>
          {!setup?.http_port && (
            <div>
              <label>
                Local HTTP port{" "}
                <input
                  type="number"
                  min={1024}
                  max={65535}
                  value={port}
                  onChange={(e) => setPort(Number(e.target.value))}
                />
              </label>
              <button
                disabled={
                  busy || !Number.isInteger(port) || port < 1024 || port > 65535
                }
                onClick={() =>
                  void run(async () => {
                    await window.tasks.request("/v1/settings", "PATCH", {
                      http_port: port,
                    });
                    await window.tasks.service("restart");
                  })
                }
              >
                Enable localhost listener
              </button>
            </div>
          )}
          <p>
            Workspace: <code>{setup?.workspace_id}</code> · Resource:{" "}
            <code>{setup?.resource_id}</code>
          </p>
          <p>
            In containers, localhost means the container itself. Configure a
            tunnel or host access explicitly. For other machines, use an SSH
            tunnel or an HTTPS reverse proxy. Senders must buffer and retry
            while this computer is asleep or unavailable.
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
            onClick={() => void run(() => createToken("logs:write"))}
          >
            Create sender token
          </button>
          <button
            disabled={busy || !name.trim()}
            onClick={() => void run(() => createToken("logs:read"))}
          >
            Create reader token
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
              aria-label="Sender example"
              rows={10}
              readOnly
              value={example}
              onFocus={(e) => e.target.select()}
            />
          </label>
          <label>
            OpenTelemetry exporter configuration
            <textarea
              aria-label="OpenTelemetry exporter configuration"
              rows={4}
              readOnly
              value={otlpExample}
              onFocus={(e) => e.target.select()}
            />
          </label>
          <p>
            Switch to the cloud by changing the endpoint and token. Successful
            requests confirm persistence. Retry uncertain acknowledgments with
            backoff; cloud-compatible retries may produce duplicates. Keep
            tokens out of source control.
          </p>
        </details>
      )}
      <details>
        <summary>Optional cloud forwarding</summary>
        <p>
          Off until configured. Keep a local copy and forward new logs to your
          cloud resource. This does not upload existing history. Delivery is
          at-least-once.
        </p>
        {targets
          .filter((t) => t.enabled)
          .map((t) => (
            <div key={t.id}>
              <p>
                {t.destination} · {t.queued} queued · {t.status?.message}
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    let credential;
                    if (cloudToken)
                      credential = (
                        await window.tasks.request("/v1/secret", "POST", {
                          value: cloudToken,
                        })
                      ).id;
                    await window.tasks.request(
                      "/v1/forwarding?" + params(),
                      "PATCH",
                      { id: t.id, credential },
                    );
                    setCloudToken("");
                  })
                }
              >
                Retry forwarding
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await window.tasks.request(
                      "/v1/forwarding?" + params(),
                      "DELETE",
                      { id: t.id },
                    );
                  })
                }
              >
                Disable forwarding
              </button>
            </div>
          ))}
        <label>
          Cloud log endpoint{" "}
          <input
            value={destination}
            placeholder={
              setup
                ? "https://api.alshival.ai" + setup.logs_path
                : "https://api.alshival.ai/…"
            }
            onChange={(e) => setDestination(e.target.value)}
          />
        </label>
        <label>
          Cloud sender token{" "}
          <input
            type="password"
            autoComplete="off"
            value={cloudToken}
            onChange={(e) => setCloudToken(e.target.value)}
          />
        </label>
        <button
          disabled={
            busy || !cloudToken || !setup || (!setup.connected && !destination)
          }
          onClick={() =>
            void run(async () => {
              const credential = (
                await window.tasks.request("/v1/secret", "POST", {
                  value: cloudToken,
                })
              ).id;
              await window.tasks.request("/v1/forwarding?" + params(), "POST", {
                resource_id: item.id,
                destination:
                  destination || "https://api.alshival.ai" + setup!.logs_path,
                credential,
              });
              setCloudToken("");
            })
          }
        >
          Enable cloud forwarding
        </button>
      </details>
    </section>
  );
}
