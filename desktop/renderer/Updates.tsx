import { useEffect, useState } from "react";
export type UpdateState = {
  phase:
    | "idle"
    | "checking"
    | "current"
    | "downloading"
    | "ready"
    | "installing"
    | "error"
    | "unsupported";
  currentVersion: string;
  availableVersion: string | null;
  progress: number | null;
  error: string;
  enabled: boolean;
  lastChecked: number | null;
  nextCheck: number | null;
  managed: boolean;
  supported: boolean;
};
export function useUpdates() {
  const [state, setState] = useState<UpdateState | null>(null);
  useEffect(() => {
    let closed = false;
    void window.tasks.getUpdates?.().then((value) => {
      if (!closed) setState(value);
    });
    const stop = window.tasks.onUpdates?.(setState);
    return () => {
      closed = true;
      stop?.();
    };
  }, []);
  return state;
}
export function UpdateSettings({ state }: { state: UpdateState | null }) {
  const [error, setError] = useState("");
  if (!state) return null;
  const work = async (action: () => Promise<unknown>) => {
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const busy = ["checking", "downloading", "installing"].includes(state.phase);
  const labels = {
    idle: "Ready to check for updates.",
    checking: "Checking for updates…",
    current: "You’re up to date.",
    downloading: `Downloading ${state.availableVersion}…`,
    ready: `Version ${state.availableVersion} is ready to install.`,
    installing: "Installing the update…",
    error: "Could not check for updates.",
    unsupported:
      "Automatic updates are available in the installed Windows and Ubuntu apps.",
  };
  return (
    <section className="update-settings" aria-labelledby="updates-title">
      <h2 id="updates-title">Updates</h2>
      <p>Version {state.currentVersion}</p>
      <p role="status">{labels[state.phase]}</p>
      {state.phase === "downloading" && (
        <progress
          aria-label="Update download"
          max={100}
          value={state.progress ?? undefined}
        />
      )}
      {state.supported && (
        <label>
          <input
            type="checkbox"
            checked={state.enabled}
            disabled={state.managed || busy}
            onChange={(e) =>
              void work(() =>
                window.tasks.setAutomaticUpdates(e.target.checked),
              )
            }
          />{" "}
          Automatically check weekly
        </label>
      )}
      {state.managed ? (
        <p>
          Updates are managed on this computer. Automatic checks run weekly.
        </p>
      ) : (
        <p>
          Updates download while the app is running, including in the tray.
          Install when you quit or restart. Ubuntu asks for administrator
          authorization.
        </p>
      )}
      {state.lastChecked && (
        <p>Last checked: {new Date(state.lastChecked).toLocaleString()}</p>
      )}
      {(error || state.error) && (
        <p className="notice error" role="alert">
          {error || state.error}
        </p>
      )}
      <div className="update-actions">
        <button
          className="button btn-frost"
          disabled={!state.supported || busy}
          onClick={() => void work(() => window.tasks.checkUpdates())}
        >
          Check for Updates
        </button>
        {state.phase === "ready" && (
          <button
            className="button matrix-btn"
            onClick={() => void work(() => window.tasks.installUpdate(true))}
          >
            Restart to update
          </button>
        )}
      </div>
    </section>
  );
}
