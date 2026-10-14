import { useEffect, useLayoutEffect, useRef, useState } from "react";
import "./notifications.css";
type Notice = {
  id: string;
  title: string;
  body: string;
  event_type: string;
  source: string;
  workspace_id: string | null;
  resource_id: string | null;
  destination: string;
  created_at: string;
  read: boolean;
  archived: boolean;
};
export function Notifications({
  openResource,
}: {
  openResource: (id: string, workspace: string) => void;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const [position, setPosition] = useState({ top: 64, right: 16 });
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  const [open, setOpen] = useState(false),
    [full, setFull] = useState(false),
    [items, setItems] = useState<Notice[]>([]),
    [unread, setUnread] = useState(0),
    [pending, setPending] = useState(0),
    [filter, setFilter] = useState("all"),
    [source, setSource] = useState("all"),
    [query, setQuery] = useState(""),
    [page, setPage] = useState(1),
    [next, setNext] = useState<number | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [portal, setPortal] = useState<any>({});
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const box = trigger.current!.getBoundingClientRect();
      const width = Math.min(full ? 800 : 440, window.innerWidth - 32);
      setPosition({
        top: Math.min(box.bottom + 8, Math.max(16, window.innerHeight - 160)),
        right: Math.min(
          Math.max(16, window.innerWidth - box.right),
          Math.max(16, window.innerWidth - width - 16),
        ),
      });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, full]);
  useEffect(() => {
    if (open)
      panel.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, [open]);
  async function load() {
    const q = new URLSearchParams({
      filter,
      source,
      query,
      page: String(page),
      limit: full ? "25" : "8",
    });
    const result = await window.tasks.request("/v1/notifications?" + q);
    setItems(result.items);
    setNext(result.next_page);
    setPortal(result.portal);
    setPending(result.pending_actions || 0);
    setUnread(result.unread_count);
  }
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const q = new URLSearchParams({
          filter,
          source,
          query,
          page: String(page),
          limit: full ? "25" : "8",
        });
        const result = await window.tasks.request("/v1/notifications?" + q);
        if (active) {
          setItems(result.items);
          setNext(result.next_page);
          setPortal(result.portal);
          setPending(result.pending_actions || 0);
          setUnread(result.unread_count);
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    };
    void refresh();
    const timer = setInterval(refresh, 10000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [filter, source, query, page, full]);
  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const mutate = (action: string, id?: string) =>
    window.tasks.request("/v1/notifications/actions", "POST", { action, id });
  async function openNotice(n: Notice) {
    await mutate("read", n.id);
    if (n.resource_id && n.workspace_id) {
      openResource(n.resource_id, n.workspace_id);
      setOpen(false);
    } else if (n.source === "portal" && n.destination) {
      const url = new URL(n.destination, "https://alshival.ai");
      if (url.origin !== "https://alshival.ai" || url.username || url.password)
        throw new Error("Unsupported notification destination");
      await window.tasks.external(url.href);
    }
  }
  useEffect(() => {
    const show = async (id: string) => {
      setOpen(true);
      setFull(true);
      setQuery("");
      setError("");
      try {
        let result = await window.tasks.request(
          "/v1/notifications?notification_id=" + encodeURIComponent(id),
        );
        if (!result.items.length)
          result = await window.tasks.request(
            "/v1/notifications?filter=archived&notification_id=" +
              encodeURIComponent(id),
          );
        const n = result.items[0];
        if (!n) throw new Error("Notification is no longer available.");
        await openNotice(n);
        await load();
      } catch (e) {
        setError((e as Error).message);
      }
    };
    const unsubscribe = window.tasks.onNotification((id) => void show(id));
    void window.tasks.initialNotification().then((id) => {
      if (id) void show(id);
    });
    return unsubscribe;
  }, [openResource]);
  return (
    <div className="desktop-notifications">
      <button
        ref={trigger}
        type="button"
        aria-label={`Notifications, ${unread} unread`}
        aria-expanded={open}
        aria-controls="desktop-notification-panel"
        aria-haspopup="dialog"
        title="Notifications"
        onClick={() => {
          setOpen(!open);
          if (!open)
            void run(async () => {
              await window.tasks.request(
                "/v1/notifications/refresh",
                "POST",
                {},
              );
            });
        }}
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          aria-hidden="true"
        >
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" />
        </svg>{" "}
        {unread > 0 && (
          <span className="notification-badge" aria-hidden="true">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <section
          ref={panel}
          id="desktop-notification-panel"
          style={{
            ...position,
            maxHeight: `calc(100dvh - ${position.top + 16}px)`,
          }}
          role="dialog"
          aria-label="Notifications"
          className={full ? "notification-panel is-full" : "notification-panel"}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              close();
            }
          }}
        >
          <header>
            <h2>Notifications</h2>
            <button aria-label="Close notifications" onClick={close}>
              ×
            </button>
          </header>
          <div className="notification-filters">
            <label>
              Show
              <select
                value={filter}
                onChange={(e) => {
                  setFilter(e.target.value);
                  setPage(1);
                }}
              >
                <option value="all">All</option>
                <option value="unread">Unread</option>
                <option value="archived">Archived</option>
              </select>
            </label>
            <label>
              Source
              <select
                value={source}
                onChange={(e) => {
                  setSource(e.target.value);
                  setPage(1);
                }}
              >
                <option value="all">All sources</option>
                <option value="local">On this device</option>
                <option value="portal">Portal</option>
              </select>
            </label>
            <label>
              Search
              <input
                value={query}
                maxLength={500}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setPage(1);
                }}
              />
            </label>
          </div>
          <button
            disabled={busy || !unread}
            onClick={() => void run(() => mutate("read_all"))}
          >
            Mark all read
          </button>
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await window.tasks.request(
                  "/v1/notifications/refresh",
                  "POST",
                  {},
                );
              })
            }
          >
            Refresh
          </button>
          {portal?.stale && (
            <p role="status">
              {pending > 0 && (
                <p role="status">
                  {pending} notification changes saved on this device;
                  synchronization pending.
                </p>
              )}
              {portal.error} Saved portal notifications may be out of date.
            </p>
          )}
          {error && <p role="alert">{error}</p>}
          {!items.length && <p>No notifications to show.</p>}
          {items.map((n) => (
            <article key={n.id} className={n.read ? "" : "is-unread"}>
              <small>
                {n.source === "local" ? "On this device" : "Portal"} ·{" "}
                {new Date(n.created_at).toLocaleString()}
              </small>
              <button
                className="notification-title"
                disabled={busy || (n.source === "portal" && portal?.stale)}
                onClick={() => void run(() => openNotice(n))}
              >
                {n.title}
              </button>
              <p>{n.body}</p>
              <div>
                <button
                  disabled={busy || (n.source === "portal" && portal?.stale)}
                  onClick={() =>
                    void run(() => mutate(n.read ? "unread" : "read", n.id))
                  }
                >
                  {n.read ? "Mark unread" : "Mark read"}
                </button>
                <button
                  disabled={busy || (n.source === "portal" && portal?.stale)}
                  onClick={() =>
                    void run(() =>
                      mutate(n.archived ? "restore" : "archive", n.id),
                    )
                  }
                >
                  {n.archived ? "Restore" : "Archive"}
                </button>
              </div>
            </article>
          ))}
          <footer>
            <button
              disabled={page <= 1 || busy}
              onClick={() => setPage(page - 1)}
            >
              Previous
            </button>
            <span>Page {page}</span>
            <button disabled={!next || busy} onClick={() => setPage(next!)}>
              Next
            </button>
            {!full && (
              <button
                onClick={() => {
                  setFull(true);
                  setPage(1);
                }}
              >
                View all notifications
              </button>
            )}
          </footer>
        </section>
      )}
    </div>
  );
}
