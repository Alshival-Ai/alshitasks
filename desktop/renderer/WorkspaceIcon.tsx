import { useEffect, useState } from "react";
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
  const endpoint =
    "/v1/workspaces/icon?workspace_id=" + encodeURIComponent(workspace);
  useEffect(() => {
    let active = true;
    setSaved(undefined);
    setError("");
    setMessage("");
    window.tasks
      .request(endpoint)
      .then((value) => {
        if (active) {
          setSaved(value);
          setChoice(value.icon);
          setColor(value.icon_color);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [endpoint]);
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
                  onChange={() => setChoice(item.id)}
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
                  onClick={() => setColor(value)}
                />
              ))}
            </div>
            <label>
              Custom color{" "}
              <input
                type="color"
                value={color}
                onChange={(e) => setColor(e.target.value)}
              />
            </label>
          </fieldset>
          {saved.can_manage ? (
            <>
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
                  setChoice(saved.default.icon);
                  setColor(saved.default.icon_color);
                }}
              >
                Reset to default
              </button>
            </>
          ) : (
            <p>Connect with workspace management access to change this icon.</p>
          )}
          {message && <p role="status">{message}</p>}
        </form>
      )}
    </section>
  );
}
