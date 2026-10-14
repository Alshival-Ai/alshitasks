import { WorkspaceIcon, WorkspaceIconSettings } from "./WorkspaceIcon";
import { TaxonomySettings } from "./project-board/Taxonomy";
import { WorkspaceBoards } from "./WorkspaceBoards";
import { useEffect, useRef, useState, type ReactNode } from "react";
import "./project-board/portal/workspace-directory.css";
export type Workspace = {
  id: string;
  name: string;
  icon?: string;
  icon_color?: string;
  local?: boolean;
  state?: string;
  kind?: string;
  sync?: string;
  pending?: number;
  attention?: number;
  incoming?: boolean;
  last_sync?: string;
  retry_at?: number;
  error?: string;
  live?: {
    state?: string;
    heartbeat?: number;
    last_notification?: number;
    next_retry?: number;
  };
  unread?: number;
  settings_url?: string;
  neural_labs_url?: string;
  portal_origin?: string;
  scopes?: string[];
};
export function syncLabel(w?: Workspace) {
  if (!w) return "Waiting to sync";
  if (w.local) return "On this computer";
  if (w.state === "revoked") return "Sign-in required";
  if (w.state !== "connected") return "Offline copy";
  if (w.sync === "offline") return "Offline—changes saved locally";
  if (w.attention) return "Needs review";
  if (w.sync === "rate-limited") return "Sync delayed—retrying automatically";
  if (w.sync === "attention") return "Attention required";
  if (w.pending || w.incoming || w.sync === "syncing") return "Syncing";
  if (w.live?.state === "reconnecting") return "Reconnecting";
  return w.sync === "up-to-date" ? "Up to date" : "Waiting to sync";
}

export function workspaceSettings(workspace: Workspace) {
  if (
    !workspace.settings_url ||
    !workspace.portal_origin ||
    workspace.state !== "connected"
  )
    return;
  const origin = new URL(workspace.portal_origin);
  const target = new URL(workspace.settings_url, origin);
  if (target.origin === origin.origin && target.protocol === "https:")
    void window.tasks.external(target.href);
}
export function WorkspaceCog({ workspace }: { workspace: Workspace }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        className="wd-cog"
        title="Workspace settings"
        aria-label={`Settings for ${workspace.name}`}
        onClick={() => {
          setOpen(true);
          dialog.current?.showModal();
        }}
      >
        ⚙
      </button>
      <dialog
        ref={dialog}
        className="taxonomy-dialog"
        onClose={() => setOpen(false)}
      >
        <header>
          <h2>{workspace.name} settings</h2>
          <button
            className="button btn-frost"
            onClick={() => dialog.current?.close()}
          >
            Close
          </button>
        </header>
        {open && (
          <WorkspaceIconSettings
            workspace={workspace.id}
            name={workspace.name}
          />
        )}
        <p>Types and labels are shared by every board in this workspace.</p>
        {open && <TaxonomySettings workspace={workspace.id} />}
        {workspace.settings_url && (
          <p>
            <button
              className="button btn-frost"
              onClick={() => workspaceSettings(workspace)}
            >
              Other portal settings
            </button>
          </p>
        )}
      </dialog>
    </>
  );
}
export function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (e: Event) => {
      if (e.target instanceof Node && !ref.current?.contains(e.target)) close();
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        close();
        ref.current?.querySelector<HTMLButtonElement>("button")?.focus();
      }
    };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("focusin", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open, close]);
  return ref;
}
function filtered(rows: Workspace[], query: string) {
  return rows
    .filter((w) =>
      w.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
    )
    .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}
export function WorkspaceList({
  rows,
  current,
  select,
  children,
  switcher = false,
}: {
  rows: Workspace[];
  current: string;
  select: (id: string) => void;
  children?: ReactNode;
  switcher?: boolean;
}) {
  const [query, setQuery] = useState(""),
    [limit, setLimit] = useState(50);
  const matches = filtered(rows, query);
  const visible = matches.slice(0, limit);
  const selected = matches.find((w) => w.id === current);
  if (selected && !visible.includes(selected)) visible.unshift(selected);
  return (
    <>
      <label className={switcher ? "app-workspace-search" : "wd-nav-search"}>
        <span className="app-workspace-sr-only">Find a workspace</span>
        <div className={switcher ? "app-workspace-search-field" : undefined}>
          {switcher && (
            <svg
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <circle cx="8.5" cy="8.5" r="5.25" />
              <path d="m12.5 12.5 4 4" />
            </svg>
          )}
          <input
            type="search"
            placeholder="Find a workspace"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setLimit(50);
            }}
          />
        </div>
      </label>
      <nav
        className={switcher ? "app-workspace-options" : "wd-nav-list"}
        aria-label="Workspaces"
      >
        {visible.map((w) => (
          <div className="wd-workspace-group" key={w.id}>
            <button
              className={switcher ? "app-workspace-option" : undefined}
              aria-label={w.name}
              title={w.name}
              aria-current={w.id === current ? "page" : undefined}
              onClick={() => select(w.id)}
            >
              <span
                className={switcher ? "app-workspace-avatar" : "wd-avatar"}
                aria-hidden="true"
              >
                <WorkspaceIcon
                  icon={w.icon}
                  color={w.icon_color}
                  name={w.name}
                />
              </span>
              <span
                className={switcher ? "app-workspace-option-name" : "nav-label"}
              >
                {w.name}
              </span>
              {switcher && w.id === current && (
                <svg
                  className="app-workspace-check"
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="m4 10 4 4 8-8" />
                </svg>
              )}
            </button>
            {w.id === current && children}
          </div>
        ))}
      </nav>
      {!matches.length && (
        <p className="app-workspace-empty" role="status">
          No workspaces match your search.
        </p>
      )}
      {limit < matches.length && (
        <button
          className={
            switcher
              ? "app-workspace-option app-workspace-more"
              : "button btn-frost wd-more"
          }
          onClick={() => setLimit(limit + 50)}
        >
          Load more
        </button>
      )}
    </>
  );
}
export function HeaderMenu({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false),
    ref = useDismiss(open, () => setOpen(false));
  return (
    <div className="wd-header-menu" ref={ref}>
      <button
        className="wd-menu-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {label}
      </button>
      {open && <div onClick={() => setOpen(false)}>{children}</div>}
    </div>
  );
}
export function WorkspaceSwitcher({
  rows,
  current,
  select,
  directory,
}: {
  rows: Workspace[];
  current: string;
  select: (id: string) => boolean;
  directory: () => boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, () => setOpen(false));
  return (
    <div ref={ref} className="app-workspace-switcher">
      <button
        className="workspace-toggle"
        aria-expanded={open}
        aria-controls="workspace-options"
        onClick={() => setOpen(!open)}
      >
        {rows.find((w) => w.id === current)?.name || "Workspaces"}{" "}
        <span aria-hidden="true">⌄</span>
      </button>
      {open && (
        <div id="workspace-options" className="app-workspace-panel">
          <p className="app-workspace-heading">Workspaces</p>
          <WorkspaceList
            switcher
            rows={rows}
            current={current}
            select={(id) => {
              if (select(id)) setOpen(false);
            }}
          />
          <button
            className="app-workspace-manage"
            onClick={() => {
              if (directory()) setOpen(false);
            }}
          >
            All workspaces <span aria-hidden="true">→</span>
          </button>
        </div>
      )}
    </div>
  );
}
export function WorkspaceDirectory({
  rows,
  current,
  select,
  startup,
  setStartup,
  selectBoard,
}: {
  rows: Workspace[];
  current: string;
  select: (id: string) => void;
  startup: string;
  setStartup: (value: string) => void;
  selectBoard: (workspace: string, board: string) => void;
}) {
  const [query, setQuery] = useState(""),
    [page, setPage] = useState(1);
  const matches = filtered(rows, query),
    pages = Math.max(1, Math.ceil(matches.length / 30)),
    actual = Math.min(page, pages);
  return (
    <section className="wd-directory">
      <header className="wd-heading">
        <div>
          <p className="eyebrow">Your workspaces</p>
          <h1>Workspaces</h1>
          <p>Choose where you want to work.</p>
        </div>
      </header>
      <div className="wd-controls">
        <label className="wd-search">
          <span className="sr-only">Find a workspace</span>
          <input
            type="search"
            placeholder="Find a workspace"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <label className="wd-startup">
          On startup
          <select value={startup} onChange={(e) => setStartup(e.target.value)}>
            <option value="directory">Workspaces</option>
            <option value="last">Last workspace</option>
          </select>
        </label>
      </div>
      <p className="wd-count">
        {matches.length} workspace{matches.length === 1 ? "" : "s"}
      </p>
      <div className="wd-grid">
        {matches.slice((actual - 1) * 30, actual * 30).map((w) => (
          <article key={w.id} className="wd-card">
            <div className="wd-card-top">
              <span className="wd-avatar" aria-hidden="true">
                <WorkspaceIcon
                  icon={w.icon}
                  color={w.icon_color}
                  name={w.name}
                />
              </span>
              <WorkspaceCog workspace={w} />
            </div>
            <button className="wd-card-title" onClick={() => select(w.id)}>
              <h2>{w.name}</h2>
            </button>
            <p className="wd-kind">
              {w.local
                ? "On this computer"
                : w.kind === "internal"
                  ? "Internal workspace"
                  : "Client workspace"}
            </p>
            {w.id === current && <span className="wd-badge">Current</span>}
            {typeof w.unread === "number" && w.unread > 0 && (
              <span className="wd-badge">{w.unread} unread</span>
            )}
            {!w.local && <span className="wd-badge">{syncLabel(w)}</span>}
            <WorkspaceBoards workspace={w} select={selectBoard} />
            <button
              className="wd-open button btn matrix-btn"
              onClick={() => select(w.id)}
            >
              Open workspace <span aria-hidden="true">→</span>
            </button>
          </article>
        ))}
      </div>
      {!matches.length && (
        <p className="wd-empty">No workspaces match your search.</p>
      )}
      <nav className="wd-pagination" aria-label="Workspace pages">
        <button
          className="wd-page-link"
          disabled={actual === 1}
          onClick={() => setPage(actual - 1)}
        >
          Previous
        </button>
        <span>Page {actual}</span>
        <button
          className="wd-page-link"
          disabled={actual === pages}
          onClick={() => setPage(actual + 1)}
        >
          Next
        </button>
      </nav>
    </section>
  );
}
