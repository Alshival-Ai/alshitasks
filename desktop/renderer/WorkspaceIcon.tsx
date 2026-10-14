import { useEffect, useRef, useState } from "react";
import pack from "./workspace-icons.json";
import colors from "./workspace-colors.json";
import "./workspace-icons.css";
const sprite = new URL("./workspace-icons.svg?no-inline", import.meta.url).href;
export function WorkspaceIcon({
  icon,
  color,
  name,
}: {
  icon?: string;
  color?: string;
  name: string;
}) {
  const selected = pack.some((choice) => choice.id === icon) ? icon : "layers";
  return (
    <svg
      className="workspace-icon"
      style={{ color: /^#[0-9a-f]{6}$/i.test(color || "") ? color : "#2563eb" }}
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <use href={`${sprite}#${selected}`} />
    </svg>
  );
}
type Selection = {
  icon: string;
  icon_color: string;
  revision: number;
  can_manage: boolean;
  can_sync: boolean;
  sync_state: "local" | "pending" | "synced" | "conflict";
  remote?: { icon: string; icon_color: string };
  error?: string;
  default: { icon: string; icon_color: string };
};
export function WorkspaceIconSettings({
  workspace,
  name,
}: {
  workspace: string;
  name: string;
}) {
  const [saved, setSaved] = useState<Selection>();
  const [choice, setChoice] = useState("");
  const [color, setColor] = useState("#2563eb");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const dirty = useRef(false);
  const endpoint =
    "/v1/workspaces/icon?workspace_id=" + encodeURIComponent(workspace);
  useEffect(() => {
    let active = true;
    setSaved(undefined);
    setError("");
    setMessage("");
    dirty.current = false;
    const refresh = () =>
      window.tasks
        .request(endpoint)
        .then((value) => {
          if (active) {
            if (!dirty.current) {
              setSaved(value);
              setChoice(value.icon);
              setColor(value.icon_color);
            }
          }
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    void refresh();
    const timer = setInterval(refresh, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [endpoint]);
  async function resolve(action: string) {
    if (!saved) return;
    setBusy(true);
    setError("");
    try {
      const value = await window.tasks.request(endpoint, "POST", {
        action,
        revision: saved.revision,
      });
      dirty.current = false;
      setSaved(value);
      setChoice(value.icon);
      setColor(value.icon_color);
      window.dispatchEvent(new Event("workspace-icon-changed"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="workspace-icon-settings" aria-label="Workspace icon">
      <h3>Workspace icon</h3>
      <p>Choose an icon and color for your workspace.</p>
      {error && <p role="alert">{error}</p>}
      {!saved && !error && <p role="status">Loading icon settings…</p>}
      {saved && (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            setMessage("");
            try {
              const value = await window.tasks.request(endpoint, "POST", {
                icon: choice,
                icon_color: color,
                revision: saved.revision,
              });
              setSaved(value);
              dirty.current = false;
              setMessage("Workspace icon saved.");
              window.dispatchEvent(new Event("workspace-icon-changed"));
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="workspace-icon-preview" aria-label="Icon preview">
            <WorkspaceIcon icon={choice} color={color} name={name} />
            <span>{name}</span>
          </div>
          <fieldset
            className="workspace-icon-picker"
            disabled={!saved.can_manage || busy}
          >
            <legend>Icon pack</legend>
            {pack.map((item) => (
              <label key={item.id} title={item.name}>
                <input
                  type="radio"
                  name="workspace-icon"
                  value={item.id}
                  checked={choice === item.id}
                  onChange={() => {
                    dirty.current = true;
                    setChoice(item.id);
                  }}
                />
                <WorkspaceIcon icon={item.id} color={color} name={name} />
                <small>{item.name}</small>
              </label>
            ))}
          </fieldset>
          <fieldset
            className="workspace-color-picker"
            disabled={!saved.can_manage || busy}
          >
            <legend>Icon color</legend>
            <div className="workspace-color-swatches">
              {colors.map((value) => (
                <button
                  type="button"
                  key={value}
                  style={{ backgroundColor: value }}
                  aria-label={`Use color ${value}`}
                  aria-pressed={color === value}
                  onClick={() => {
                    dirty.current = true;
                    setColor(value);
                  }}
                />
              ))}
            </div>
            <label>
              Custom color{" "}
              <input
                type="color"
                value={color}
                onChange={(e) => {
                  dirty.current = true;
                  setColor(e.target.value);
                }}
              />
            </label>
          </fieldset>
          {saved.can_manage ? (
            <div className="workspace-icon-actions">
              <button
                className="button btn matrix-btn"
                disabled={
                  busy || (choice === saved.icon && color === saved.icon_color)
                }
              >
                Save workspace icon
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  dirty.current = true;
                  setChoice(saved.default.icon);
                  setColor(saved.default.icon_color);
                }}
              >
                Reset to default
              </button>
            </div>
          ) : (
            <p>Connect with workspace management access to change this icon.</p>
          )}
          {message && <p role="status">{message}</p>}
          <div className="workspace-icon-sync" role="status">
            {saved.sync_state === "local" && <p>Saved on this device.</p>}
            {saved.sync_state === "pending" && (
              <p>Saved locally. Waiting to sync with the portal.</p>
            )}
            {saved.sync_state === "synced" && <p>Synced with the portal.</p>}
            {saved.sync_state === "conflict" && (
              <p>
                The portal icon also changed. Your choice is saved locally.
                Choose which icon to keep.
              </p>
            )}
            {saved.error && <p>{saved.error}</p>}
          </div>
          {(saved.sync_state === "conflict" ||
            (saved.sync_state === "local" && saved.remote)) && (
            <div className="workspace-icon-actions">
              {saved.remote && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => resolve("use_portal")}
                >
                  Use portal icon
                </button>
              )}
              {saved.sync_state === "conflict" && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => resolve("keep_local")}
                >
                  Keep on this device
                </button>
              )}
              {saved.can_sync && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => resolve("sync")}
                >
                  {saved.sync_state === "conflict"
                    ? "Sync my choice"
                    : "Sync to workspace"}
                </button>
              )}
            </div>
          )}
        </form>
      )}
    </section>
  );
}
