import { useEffect, useRef, useState } from "react";
import type { Item, ItemData } from "./types";
import "./card-lifecycle.css";

export const isRemovedCard = (item: Item) =>
  item.data.archived ||
  item.data.deleted ||
  (item.kind === "resource" && item.data.resource?.status === "retired");

export function CardLifecycleButton({
  item,
  onAction,
}: {
  item: Item;
  onAction: (item: Item) => void;
}) {
  const restore = isRemovedCard(item);
  const label = `${restore ? "Restore" : "Delete"} ${item.kind}: ${item.data.title}`;
  return (
    <button
      type="button"
      className="card-lifecycle-button"
      aria-label={label}
      title={label}
      onClick={() => onAction(item)}
    >
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {restore ? (
          <>
            <path d="M3 10a9 9 0 1 1 1 8" />
            <path d="M3 4v6h6" />
          </>
        ) : (
          <>
            <path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" />
          </>
        )}
      </svg>
    </button>
  );
}

export function CardLifecycleDialog({
  item,
  commit,
  close,
}: {
  item: Item;
  commit: (item: Item, data: Partial<ItemData>) => Promise<void>;
  close: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null),
    inFlight = useRef(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const restore = isRemovedCard(item),
    verb = restore ? "Restore" : "Delete";
  useEffect(() => {
    const origin = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => {
      if (origin?.isConnected) origin.focus();
      else
        document
          .querySelector<HTMLButtonElement>(".card-deleted-toggle")
          ?.focus();
    };
  }, []);
  const dismiss = () => {
    if (!inFlight.current) close();
  };
  const submit = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await commit(
        item,
        item.kind === "resource"
          ? {
              archived: !restore,
              resource: {
                kind: "other",
                provider: "",
                environment: "",
                public_url: "",
                external_id: null,
                ...item.data.resource,
                status: restore ? "active" : "retired",
              },
            }
          : { archived: !restore },
      );
      close();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  return (
    <dialog
      ref={dialog}
      className="card-lifecycle-dialog"
      aria-labelledby="card-lifecycle-title"
      aria-describedby="card-lifecycle-description"
      aria-busy={busy}
      onCancel={(e) => {
        e.preventDefault();
        dismiss();
      }}
    >
      <h2 id="card-lifecycle-title">
        {verb} {item.kind}?
      </h2>
      <p className="card-lifecycle-name">{item.data.title}</p>
      <p id="card-lifecycle-description">
        {restore
          ? `This ${item.kind} will return to the board.${item.kind === "resource" ? " Its status will be Active." : ""}`
          : `This ${item.kind} will move to Deleted items. You can restore it at any time.${item.kind === "resource" ? " Linked notes and resource settings will be kept." : ""}`}
      </p>
      {error && (
        <p role="alert" className="card-lifecycle-error">
          {error}
        </p>
      )}
      <footer>
        <button type="button" autoFocus disabled={busy} onClick={dismiss}>
          Cancel
        </button>
        <button
          type="button"
          className={restore ? "" : "card-lifecycle-confirm"}
          disabled={busy}
          onClick={() => void submit()}
        >
          {busy
            ? restore
              ? "Restoring…"
              : "Deleting…"
            : `${verb} ${item.kind}`}
        </button>
      </footer>
    </dialog>
  );
}
