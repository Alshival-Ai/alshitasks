import { useId, useLayoutEffect, useRef, useState } from "react";
import type { Item, Status } from "./types";

/** A top-layer chooser stays usable in clipped or horizontally scrolled columns. */
export function StatusPicker({
  item,
  statuses,
  statusId,
  busy,
  move,
}: {
  item: Item;
  statuses: Status[];
  statusId?: string;
  busy: boolean;
  move: (item: Item, status: string) => Promise<void>;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const close = () => {
    setOpen(false);
    trigger.current?.focus({ preventScroll: true });
  };
  useLayoutEffect(() => {
    const panel = menu.current;
    if (!panel || !open) return;
    panel.showPopover();
    const position = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const below = rect.bottom + 6,
        above = rect.top - panel.offsetHeight - 6;
      panel.style.left = `${Math.max(12, Math.min(rect.left, innerWidth - panel.offsetWidth - 12))}px`;
      panel.style.top = `${Math.max(12, Math.min(below + panel.offsetHeight <= innerHeight - 12 || above < 12 ? below : above, innerHeight - panel.offsetHeight - 12))}px`;
    };
    position();
    panel.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
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
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="sp-move"
        disabled={busy || item.data.archived}
        aria-haspopup="dialog"
        aria-controls={id}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        Move to…
      </button>
      <div
        ref={menu}
        id={id}
        popover="auto"
        role="dialog"
        aria-label={`Move ${item.data.title}`}
        className="sp-quick-popover"
        onToggle={(event) => {
          if (event.newState === "closed") setOpen(false);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            close();
          }
          if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            const choices = [
              ...(menu.current?.querySelectorAll<HTMLButtonElement>(
                "button:not(:disabled)",
              ) ?? []),
            ];
            if (!choices.length) return;
            const index = choices.indexOf(
              document.activeElement as HTMLButtonElement,
            );
            const next =
              event.key === "Home"
                ? 0
                : event.key === "End"
                  ? choices.length - 1
                  : (index +
                      (event.key === "ArrowDown" ? 1 : -1) +
                      choices.length) %
                    choices.length;
            choices[next].focus();
          }
        }}
      >
        <p className="sp-quick-title">Move task</p>
        {statuses.map((status) => (
          <button
            key={status.id}
            type="button"
            className="sp-quick-option"
            disabled={busy || status.id === statusId}
            onClick={() => {
              close();
              void move(item, status.id);
            }}
          >
            {status.name}
          </button>
        ))}
      </div>
    </>
  );
}
