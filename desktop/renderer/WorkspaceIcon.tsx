import { useEffect, useState } from "react";
import pack from "./workspace-icons.json";
import "./workspace-icons.css";
const sprite = new URL("./workspace-icons.svg?no-inline", import.meta.url).href;
export function WorkspaceIcon({ icon, name }: { icon?: string; name: string }) {
  return pack.some((choice) => choice.id === icon) ? (
    <svg className="workspace-icon" viewBox="0 0 24 24" aria-hidden="true">
      <use href={`${sprite}#${icon}`} />
    </svg>
  ) : (
    <>{name.slice(0, 1)}</>
  );
}
type Selection = { icon: string; revision: number; can_manage: boolean };
export function WorkspaceIconSettings({
  workspace,
  name,
}: {
  workspace: string;
  name: string;
}) {
  const [saved, setSaved] = useState<Selection>();
  const [choice, setChoice] = useState("");
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
      <p>
        Shown in navigation and the workspace directory. Icons adapt to light
        and dark themes.
      </p>
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
          <fieldset
            className="workspace-icon-picker"
            disabled={!saved.can_manage || busy}
          >
            <legend>Icon pack</legend>
            {[{ id: "", name: "Initial" }, ...pack].map((item) => (
              <label key={item.id} title={item.name}>
                <input
                  type="radio"
                  name="workspace-icon"
                  value={item.id}
                  checked={choice === item.id}
                  onChange={() => setChoice(item.id)}
                />
                <WorkspaceIcon icon={item.id} name={name} />
                <small>{item.name}</small>
              </label>
            ))}
          </fieldset>
          {saved.can_manage ? (
            <button
              className="button btn matrix-btn"
              disabled={busy || choice === saved.icon}
            >
              Save workspace icon
            </button>
          ) : (
            <p>Connect with workspace management access to change this icon.</p>
          )}
          {message && <p role="status">{message}</p>}
        </form>
      )}
    </section>
  );
}
