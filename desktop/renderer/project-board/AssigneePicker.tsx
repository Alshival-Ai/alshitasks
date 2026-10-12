import { useId, useLayoutEffect, useRef, useState } from "react";
import type { Change, Item, Member } from "./types";

/** Portal quick-assignment UI backed by the standalone app's revisioned mutations. */
export function AssigneePicker({
  item,
  members,
  change,
  report,
}: {
  item: Item;
  members: Member[];
  change: Change;
  report: (message: string) => void;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const close = () => {
    setOpen(false);
    trigger.current?.focus({ preventScroll: true });
  };
  useLayoutEffect(() => {
    const panel = menu.current;
    if (!open || !panel) return;
    panel.showPopover();
    const position = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const viewport = window.visualViewport;
      const left = viewport?.offsetLeft || 0,
        top = viewport?.offsetTop || 0;
      const right = left + (viewport?.width || innerWidth),
        bottom = top + (viewport?.height || innerHeight);
      const below = rect.bottom + 6,
        above = rect.top - panel.offsetHeight - 6;
      const y =
        below + panel.offsetHeight <= bottom - 12 || above < top + 12
          ? below
          : above;
      panel.style.left = `${Math.max(left + 12, Math.min(rect.left, right - panel.offsetWidth - 12))}px`;
      panel.style.top = `${Math.max(top + 12, Math.min(y, bottom - panel.offsetHeight - 12))}px`;
    };
    position();
    input.current?.focus();
    const observer = new ResizeObserver(position);
    observer.observe(panel);
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
      panel.hidePopover();
    };
  }, [open]);
  const assign = async (assignee: string | null) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await change(item, { assignee });
      close();
    } catch (e) {
      const message = (e as Error).message;
      setError(message);
      report(message);
    } finally {
      setBusy(false);
    }
  };
  const people = [{ id: "", display_name: "Unassigned" }, ...members];
  const matches = people.filter((p) =>
    p.display_name.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="sp-assignee"
        aria-label={`Assign ${item.data.title}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          setQuery("");
          setError("");
          setOpen(!open);
        }}
      >
        {item.data.assignee
          ? members.find((p) => p.id === item.data.assignee)?.display_name ||
            "Assigned member"
          : "Unassigned"}
      </button>
      <div
        ref={menu}
        id={id}
        popover="auto"
        role="dialog"
        aria-label="Assign task"
        className="sp-quick-popover"
        aria-busy={busy}
        onClick={e => e.stopPropagation()}
        onPointerDown={e => e.stopPropagation()}
        onToggle={(e) => {
          if (e.newState === "closed") setOpen(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            close();
          }
        }}
      >
        <p className="sp-quick-title">Assign task</p>
        <input
          ref={input}
          type="search"
          placeholder="Find a person…"
          aria-label="Find a person"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="sp-quick-people">
          {matches.map((p) => (
            <button
              key={p.id}
              type="button"
              className="sp-quick-option"
              aria-pressed={p.id === (item.data.assignee || "")}
              disabled={busy}
              onClick={() => void assign(p.id || null)}
            >
              {p.display_name}
            </button>
          ))}
          {!matches.length && <p className="sp-muted">No matching people.</p>}
        </div>
        {error && <p role="alert">{error}</p>}
      </div>
    </>
  );
}
