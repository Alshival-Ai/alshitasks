import { useEffect, useState } from "react";
export function Attachments({ item }: { item: string }) {
  const [files, setFiles] = useState<any[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const refresh = async () =>
    setFiles(
      (await window.tasks.request("/v1/attachments?item_id=" + item))
        .attachments,
    );
  useEffect(() => {
    void refresh().catch((e) => setError(e.message));
    return window.tasks.onChanged(
      () => void refresh().catch((e) => setError(e.message)),
    );
  }, [item]);
  const run = async (fn: () => Promise<any>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <details className="connected-relations">
      <summary>Attachments ({files.length})</summary>
      <button
        disabled={busy}
        onClick={() => void run(() => window.tasks.importAttachment(item))}
      >
        Attach file
      </button>
      {files.map((file) => (
        <div key={file.id}>
          <button
            onClick={() => void run(() => window.tasks.saveAttachment(file.id))}
          >
            {file.name}
          </button>
          <small>
            {" "}
            {file.sync?.state || "On this computer"}
            {file.sync?.error ? ` · ${file.sync.error}` : ""}
          </small>
          {file.sync?.state === "conflict" && (
            <button
              disabled={busy}
              onClick={() =>
                void run(() =>
                  window.tasks.request(
                    "/v1/attachments/" + file.id + "/review",
                    "POST",
                  ),
                )
              }
            >
              {file.sync.operation === "delete"
                ? "Keep changed portal file"
                : "Keep both files"}
            </button>
          )}
          <button
            disabled={
              busy ||
              (file.sync?.state &&
                !["sent", "synced"].includes(file.sync.state))
            }
            onClick={() =>
              void run(() => window.tasks.importAttachment(item, file.id))
            }
          >
            Replace
          </button>
          <button
            disabled={busy}
            onClick={() => {
              if (window.confirm(`Remove ${file.name}?`))
                void run(() =>
                  window.tasks.request("/v1/attachments/" + file.id, "DELETE"),
                );
            }}
          >
            Remove
          </button>
        </div>
      ))}
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
