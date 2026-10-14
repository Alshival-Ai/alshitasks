import {
  WorkspaceDirectory,
  WorkspaceList,
  WorkspaceSwitcher,
  HeaderMenu,
  syncLabel,
} from "./Workspaces";
import "./theme.js";
import { Notifications } from "./Notifications";
import { WindowChrome } from "./WindowChrome";
import { useEffect, useState, useRef, useCallback } from "react";
import { createRoot } from "react-dom/client";
import { ProjectsApp } from "./ProjectsApp";
import type { Item } from "./project-board/types";
import "./shell.css";
import "./project-board/portal/workspace-directory.css";
import { readDeviceState, writeDeviceState } from "./deviceState";
import { LOCAL_WORKSPACE, selectWorkspace } from "./project-board/api";
const api = window.tasks.request;
const sidebarIcons = new URL("./sidebar-icons.svg?no-inline", import.meta.url)
  .href;
import "./portal-controls.css";
import "./project-board/portal/portal-actions.css";
import "./sidebar.css";
import "./sidebar.js";
function NavIcon({ name }: { name: string }) {
  return (
    <svg
      className="nav-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <use href={`${sidebarIcons}#${name}`} />
    </svg>
  );
}
function App() {
  const [expanded, setExpanded] = useState(
    () =>
      window.innerWidth > 900 &&
      readDeviceState("alshitasks", "sidebar") !== false,
  );
  useEffect(() => {
    document.body.classList.add(
      "alshival-app",
      "app-portal",
      "native-workspaces",
    );
    document.body.classList.toggle("app-menu-open", expanded);
  }, [expanded]);
  const navigationGuard = useRef<(() => boolean) | null>(null);
  const [workspace, setWorkspace] = useState(LOCAL_WORKSPACE);

  const [accountInfo, setAccountInfo] = useState<any>({});
  const [narrow, setNarrow] = useState(() => window.innerWidth <= 900);
  const sidebarRef = useRef<HTMLElement>(null);
  const sidebarAccount = accountInfo.account?.id || "local";
  const toggleSidebar = () => {
    const next = !expanded;
    setExpanded(next);
    if (!narrow) writeDeviceState(sidebarAccount, "sidebar", next);
  };
  useEffect(() => {
    const saved =
      readDeviceState(sidebarAccount, "sidebar") ??
      readDeviceState("alshitasks", "sidebar");
    setExpanded(window.innerWidth > 900 && saved !== false);
  }, [sidebarAccount]);
  useEffect(() => {
    const media = matchMedia("(max-width:900px)");
    const change = () => {
      setNarrow(media.matches);
      setExpanded(
        !media.matches && readDeviceState(sidebarAccount, "sidebar") !== false,
      );
    };
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, [sidebarAccount]);
  useEffect(() => {
    if (!narrow || !expanded) return;
    const previous = document.activeElement as HTMLElement;
    const sidebar = sidebarRef.current!;
    sidebar.querySelector<HTMLButtonElement>("button")?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setExpanded(false);
      }
      if (event.key !== "Tab") return;
      const controls = [
        ...sidebar.querySelectorAll<HTMLElement>(
          "a[href],button,input,summary",
        ),
      ].filter((e) => e.offsetParent !== null && !e.hasAttribute("disabled"));
      if (event.shiftKey && document.activeElement === controls[0]) {
        event.preventDefault();
        controls.at(-1)?.focus();
      } else if (
        !event.shiftKey &&
        document.activeElement === controls.at(-1)
      ) {
        event.preventDefault();
        controls[0]?.focus();
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [narrow, expanded]);

  const [localBoards, setLocalBoards] = useState<Item[]>([]);
  const [publications, setPublications] = useState<any[]>([]);
  const [publishBoard, setPublishBoard] = useState(LOCAL_WORKSPACE);
  const [publishWorkspace, setPublishWorkspace] = useState("");
  const [publishMembers, setPublishMembers] = useState<any[]>([]);
  const [memberMapping, setMemberMapping] = useState<Record<string, string>>(
    {},
  );
  const [agentConfig, setAgentConfig] = useState("");
  const [grants, setGrants] = useState<any[]>([]);
  const [recoveryWorkspace, setRecoveryWorkspace] = useState("");
  const [boardNavigation, setBoardNavigation] = useState<Item[]>([]);
  const [activeBoard, setActiveBoard] = useState("");
  const [boardRequest, setBoardRequest] = useState<{
    id: string;
    serial: number;
  }>();
  const selectBoard = (id: string, board: string) => {
    if (navigationGuard.current && !navigationGuard.current()) return;
    setRecoveryWorkspace("");
    setBoardRequest({ id: board, serial: Date.now() });
    if (id !== workspace) setBoardNavigation([]);
    selectWorkspace(id);
    setWorkspace(id);
    setTab("Projects");
    if (window.innerWidth <= 900) setExpanded(false);
    writeDeviceState(accountInfo.account?.id || "local", "workspace", id);
  };
  const changeWorkspace = (id: string, recovery = false) => {
    if (navigationGuard.current && !navigationGuard.current()) return false;
    setRecoveryWorkspace(recovery ? id : "");
    setBoardRequest(undefined);
    if (id !== workspace) setBoardNavigation([]);
    setTab("Projects");
    if (window.innerWidth <= 900) setExpanded(false);
    selectWorkspace(id);
    setWorkspace(id);
    if (!recovery)
      writeDeviceState(accountInfo.account?.id || "local", "workspace", id);
    return true;
  };
  const [tab, setTab] = useState("Projects"),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [items, setItems] = useState<Item[]>([]),
    [loginStartup, setLoginStartup] = useState(false),
    [startupSaving, setStartupSaving] = useState(false),
    [settings, setSettings] = useState<any>({}),
    [workspaces, setWorkspaces] = useState<any[]>([]),
    [conflicts, setConflicts] = useState<any[]>([]),
    [drafts, setDrafts] = useState<any[]>([]),
    [attachments, setAttachments] = useState<any[]>([]),
    [fileItem, setFileItem] = useState("");
  useEffect(() => {
    if (tab !== "Settings") return;
    const load = () =>
      void window.tasks
        .getStartup?.()
        .then((value) => setLoginStartup(value.enabled))
        .catch((e) => setError(e.message));
    load();
    window.addEventListener("focus", load);
    return () => window.removeEventListener("focus", load);
  }, [tab]);
  const changeLoginStartup = async (enabled: boolean) => {
    setStartupSaving(true);
    setError("");
    try {
      setLoginStartup((await window.tasks.setStartup(enabled)).enabled);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStartupSaving(false);
    }
  };
  const [notificationTarget, setNotificationTarget] = useState<{
    id: string;
    serial: number;
  }>();
  const openNotificationResource = useCallback(
    (id: string, targetWorkspace: string) => {
      if (navigationGuard.current && !navigationGuard.current()) return;
      selectWorkspace(targetWorkspace);
      setWorkspace(targetWorkspace);
      setTab("Projects");
      setNotificationTarget({ id, serial: Date.now() });
    },
    [],
  );
  const work = async (fn: () => Promise<any>) => {
    setError("");
    try {
      const v = await fn();
      setMessage(v?.message ?? "Saved");
      await refresh();
      return v;
    } catch (e) {
      setError((e as Error).message);
    }
  };
  async function refresh() {
    const all: Item[] = [];
    const visibleWorkspaces = (await api("/v1/workspaces")).workspaces;
    for (const visibleWorkspace of visibleWorkspaces.filter(
      (w: any) => w.local,
    )) {
      let next = "";
      do {
        const page = await api(
          "/v1/projects/items?workspace_id=" +
            visibleWorkspace.id +
            "&after=" +
            encodeURIComponent(next),
        );
        all.push(...page.items);
        next = page.next ?? "";
      } while (next);
    }
    setItems(all);
    setSettings(await api("/v1/settings"));
    const info = await api("/v1/portal/account");
    setLocalBoards(
      (await api("/v1/projects/boards?workspace_id=" + LOCAL_WORKSPACE)).boards,
    );
    setPublications((await api("/v1/portal/publish")).publications);
    setAccountInfo(info);
    setWorkspaces(visibleWorkspaces);
    setGrants((await api("/v1/projects/keys")).keys);
    setConflicts((await api("/v1/conflicts")).conflicts);
    setDrafts((await api("/v1/drafts")).drafts);
  }
  useEffect(() => {
    void refresh().catch((e) => setError(e.message));
    const update = () => {
      if (!document.hidden) void refresh().catch(() => {});
    };
    const unsubscribe = window.tasks.onChanged(update);
    window.addEventListener("workspace-icon-changed", update);
    const t = setInterval(update, 30000);
    return () => {
      clearInterval(t);
      unsubscribe();
      window.removeEventListener("workspace-icon-changed", update);
    };
  }, []);
  const initializedAccount = useRef<string | null>(null);
  const [startup, setStartupValue] = useState("directory");
  const availableWorkspaces = workspaces.filter(
    (w) => w.local || !["revoked", "disconnected"].includes(w.state),
  );
  const showTab = (next: string) => {
    if (navigationGuard.current && !navigationGuard.current()) return false;
    setTab(next);
    setMessage("");
    return true;
  };
  const setStartup = (value: string) => {
    setStartupValue(value);
    writeDeviceState(accountInfo.account?.id || "local", "startup", value);
  };
  useEffect(() => {
    if (!workspaces.length) return;
    const account = accountInfo.account?.id || "local";
    if (initializedAccount.current === account) return;
    initializedAccount.current = account;
    const pref =
      readDeviceState(account, "startup") === "last" ? "last" : "directory";
    setStartupValue(pref);
    const saved = readDeviceState(account, "workspace");
    const target =
      availableWorkspaces.find(
        (w) => w.id === saved || w.connection_id === saved,
      ) || availableWorkspaces[0];
    if (target) {
      selectWorkspace(target.id);
      setWorkspace(target.id);
    }
    setTab(
      pref === "directory" && availableWorkspaces.length > 1
        ? "Workspaces"
        : "Projects",
    );
  }, [accountInfo.account?.id, workspaces]);
  useEffect(() => {
    setMemberMapping({});
    if (!publishWorkspace) {
      setPublishMembers([]);
      return;
    }
    let active = true;
    void api("/v1/projects/members?workspace_id=" + publishWorkspace)
      .then((x) => {
        if (active) setPublishMembers(x.members);
      })
      .catch((e) => setError(e.message));
    return () => {
      active = false;
    };
  }, [publishWorkspace]);
  useEffect(() => {
    if (fileItem)
      void api("/v1/attachments?item_id=" + fileItem)
        .then((x) => setAttachments(x.attachments))
        .catch((e) => setError(e.message));
  }, [fileItem]);
  useEffect(() => {
    if (
      workspaces.length &&
      !(
        recoveryWorkspace === workspace &&
        workspaces.some((w) => w.id === workspace)
      ) &&
      !availableWorkspaces.some((w) => w.id === workspace)
    ) {
      const target = availableWorkspaces[0];
      if (target) {
        selectWorkspace(target.id);
        setWorkspace(target.id);
        setTab("Workspaces");
      }
    }
  }, [workspaces, workspace, recoveryWorkspace]);
  const publishContacts = [
    ...new Set(
      items
        .filter((i) =>
          publishBoard === LOCAL_WORKSPACE
            ? !i.data.board_id && i.kind !== "board"
            : i.data.board_id === publishBoard,
        )
        .flatMap((i) => [i.data.assignee, i.data.reviewer])
        .filter(Boolean),
    ),
  ] as string[];
  const data = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    return Object.fromEntries(new FormData(e.currentTarget));
  };
  const selectedWorkspace = workspaces.find((w) => w.id === workspace);
  const boards = boardNavigation.filter((b) => !b.data.archived);
  const multipleBoards =
    boards.length + (workspace === LOCAL_WORKSPACE ? 1 : 0) > 1;
  const boardLinks = (
    <>
      {workspace === LOCAL_WORKSPACE && (
        <button
          aria-label="Workspace board"
          aria-current={!activeBoard ? "page" : undefined}
          onClick={() => selectBoard(workspace, "")}
        >
          <NavIcon name="board" />
          <span className="nav-label">Workspace board</span>
        </button>
      )}
      {boards.map((b) => (
        <button
          key={b.id}
          title={b.data.title}
          aria-label={b.data.title}
          aria-current={activeBoard === b.id ? "page" : undefined}
          onClick={() => selectBoard(workspace, b.id)}
        >
          <NavIcon name="board" />
          <span className="nav-label">{b.data.title}</span>
        </button>
      ))}
    </>
  );
  const openLabs = () => {
    if (
      !selectedWorkspace?.neural_labs_url ||
      !selectedWorkspace.portal_origin ||
      selectedWorkspace.state !== "connected"
    )
      return;
    const origin = new URL(selectedWorkspace.portal_origin),
      target = new URL(selectedWorkspace.neural_labs_url, origin);
    if (target.origin === origin.origin && target.protocol === "https:")
      void window.tasks.external(target.href);
  };
  return (
    <>
      <aside
        ref={sidebarRef}
        id="app-sidebar"
        className="app-sidebar"
        aria-label="Alshival navigation"
        inert={narrow && !expanded}
        role={narrow && expanded ? "dialog" : undefined}
        aria-modal={narrow && expanded ? true : undefined}
      >
        <div className="app-sidebar-controls">
          <button
            type="button"
            data-app-toggle
            aria-controls="app-menu"
            aria-expanded={expanded}
            aria-label={expanded ? "Collapse menu" : "Expand menu"}
            title={expanded ? "Collapse menu" : "Expand menu"}
            onClick={toggleSidebar}
          >
            <NavIcon name="toggle" />
          </button>
        </div>
        <div id="app-menu">
          <div className="wd-navigation">
            <button
              className="wd-directory-link"
              onClick={() => showTab("Workspaces")}
              title="Workspaces"
              aria-label="Workspaces"
              aria-current={tab === "Workspaces" ? "page" : undefined}
            >
              <NavIcon name="workspaces" />
              <span className="nav-label">Workspaces</span>
            </button>
            <WorkspaceList
              rows={availableWorkspaces}
              current={tab === "Projects" ? workspace : ""}
              select={changeWorkspace}
            >
              {tab === "Projects" && multipleBoards && (
                <>
                  <nav
                    className="wd-board-children"
                    aria-label="Current workspace boards"
                  >
                    {boardLinks}
                  </nav>
                  <button
                    className="wd-board-picker"
                    data-sidebar-boards="sidebar-boards"
                    aria-label="Choose board"
                    title="Choose board"
                  >
                    <NavIcon name="board" />
                  </button>
                </>
              )}
            </WorkspaceList>
          </div>
          {tab === "Projects" && multipleBoards && (
            <div
              id="sidebar-boards"
              className="wd-board-popover"
              popover="auto"
              aria-label="Choose board"
            >
              <p>Choose board</p>
              {boardLinks}
            </div>
          )}
          {tab === "Projects" &&
            selectedWorkspace?.state === "connected" &&
            selectedWorkspace?.neural_labs_url && (
              <div className="wd-sidebar-footer">
                <button
                  className="wd-directory-link"
                  onClick={openLabs}
                  aria-label="Neural Labs (opens in browser)"
                  title="Neural Labs · opens in browser"
                >
                  <NavIcon name="labs" />
                  <span className="nav-label">Neural Labs</span>
                </button>
              </div>
            )}
        </div>
      </aside>
      {expanded && (
        <button
          className="native-navigation-backdrop"
          aria-label="Close navigation"
          onClick={() => setExpanded(false)}
        />
      )}
      <main className="app-content" inert={narrow && expanded}>
        <header className="app-header">
          <button
            className="app-mobile-toggle"
            aria-label="Open navigation"
            aria-controls="app-sidebar"
            aria-expanded={expanded}
            onClick={toggleSidebar}
          >
            <NavIcon name="toggle" />
          </button>
          <a
            className="app-wordmark"
            href="#projects"
            onClick={(e) => {
              e.preventDefault();
              showTab("Workspaces");
            }}
          >
            Alshival.Ai
          </a>
          <WorkspaceSwitcher
            rows={availableWorkspaces}
            current={tab === "Projects" ? workspace : ""}
            select={changeWorkspace}
            directory={() => showTab("Workspaces")}
          />
          <HeaderMenu label={accountInfo.account ? "Account" : "Settings"}>
            <button onClick={() => showTab("Settings")}>Device settings</button>
            {accountInfo.account && (
              <button
                onClick={() =>
                  void window.tasks.external(
                    new URL("/account/settings/", accountInfo.account.origin)
                      .href,
                  )
                }
              >
                Account settings
              </button>
            )}
            <button onClick={() => showTab("Workspaces")}>
              Startup preferences
            </button>
            {!accountInfo.account &&
              (accountInfo.managed ? (
                <button
                  onClick={() => {
                    showTab("Settings");
                    void work(() => api("/v1/portal/retry", "POST", {}));
                  }}
                >
                  Retry account connection
                </button>
              ) : (
                <button onClick={() => void work(() => window.tasks.connect())}>
                  Sign in with Alshival.Ai
                </button>
              ))}
          </HeaderMenu>
          <span className="local-label">
            {settings.paused
              ? "Background paused"
              : workspace === LOCAL_WORKSPACE
                ? "On this computer"
                : syncLabel(workspaces.find((w) => w.id === workspace))}
          </span>
          <WindowChrome
            notifications={
              <Notifications openResource={openNotificationResource} />
            }
          />
        </header>
        {error && (
          <p role="alert" className="notice error">
            {error}
          </p>
        )}
        {message && (
          <p role="status" className="notice">
            {message}
          </p>
        )}
        <div
          className={
            tab === "Projects"
              ? "projects-frame"
              : tab === "Workspaces"
                ? "workspace-frame"
                : "page"
          }
        >
          {tab === "Settings" &&
            workspaces.some(
              (w) => !w.local && ["revoked", "disconnected"].includes(w.state),
            ) && (
              <section className="panel">
                <h2>Retained workspace copies</h2>
                <p>
                  Access has ended or the account is disconnected. Local copies
                  and drafts remain available for recovery.
                </p>
                {workspaces
                  .filter(
                    (w) =>
                      !w.local && ["revoked", "disconnected"].includes(w.state),
                  )
                  .map((w) => (
                    <button
                      key={w.id}
                      onClick={() => changeWorkspace(w.id, true)}
                    >
                      Open retained copy: {w.name}
                    </button>
                  ))}
              </section>
            )}
          {!!recoveryWorkspace && tab === "Projects" && (
            <p role="status">
              Retained local copy. Workspace access is{" "}
              {workspaces.find((w) => w.id === workspace)?.state};
              synchronization is disabled.
            </p>
          )}
          {tab === "Workspaces" && (
            <WorkspaceDirectory
              rows={availableWorkspaces}
              current={workspace}
              select={changeWorkspace}
              startup={startup}
              setStartup={setStartup}
              selectBoard={selectBoard}
            />
          )}
          {tab === "Projects" && (
            <ProjectsApp
              key={workspace}
              workspaceId={workspace}
              boardRequest={boardRequest}
              onBoardCatalog={setBoardNavigation}
              onActiveBoard={setActiveBoard}
              workspaceName={
                workspaces.find((w) => w.id === workspace)?.name ||
                "Local workspace"
              }
              workspaceSettings={workspaces.find((w) => w.id === workspace)}
              notificationTarget={notificationTarget}
              navigationGuard={navigationGuard}
              storageNamespace={
                "alshitasks." +
                (accountInfo.account?.id || "local") +
                "." +
                (selectedWorkspace?.connection_id || workspace)
              }
            />
          )}
          {tab === "Settings" && (
            <>
              <h1>Settings</h1>
              <section>
                <h2>Startup</h2>
                <label>
                  <input
                    type="checkbox"
                    checked={loginStartup}
                    disabled={startupSaving}
                    onChange={(e) => void changeLoginStartup(e.target.checked)}
                  />
                  Start at login minimized
                </label>
                <p>
                  Start in the system tray without opening a window. Applies to
                  your account on this computer.
                </p>
              </section>
              <section>
                <h2>Notifications</h2>
                <label>
                  <input
                    type="checkbox"
                    checked={settings.notification_toasts !== false}
                    onChange={(e) =>
                      void work(() =>
                        api("/v1/settings", "PATCH", {
                          notification_toasts: e.target.checked,
                        }),
                      )
                    }
                  />
                  Show desktop toast notifications
                </label>
                <p>
                  Your inbox remains available when toasts are disabled. Portal
                  email and SMS preferences are managed in your Alshival.Ai
                  account.
                </p>
              </section>
              <section aria-labelledby="connections-heading">
                <h2 id="connections-heading">Alshival.Ai account</h2>
                {accountInfo.managed && (
                  <p>Connected through your Ubuntu account</p>
                )}
                {!accountInfo.account && accountInfo.managed ? (
                  <>
                    <p>
                      Waiting for your Ubuntu account connection. Your local
                      boards are available.
                    </p>
                    <button
                      onClick={() =>
                        void work(() => api("/v1/portal/retry", "POST", {}))
                      }
                    >
                      Retry connection
                    </button>
                  </>
                ) : !accountInfo.account ? (
                  <>
                    <p>
                      Work locally without an account, or sign in to sync your
                      Alshival.Ai workspaces.
                    </p>
                    <button
                      onClick={() => void work(() => window.tasks.connect())}
                    >
                      Sign in with Alshival.Ai
                    </button>
                  </>
                ) : (
                  <>
                    <p>
                      {accountInfo.account.name} ·{" "}
                      {accountInfo.status?.state || "Connecting"}
                    </p>
                    <button
                      onClick={() =>
                        void work(() => api("/v1/portal/retry", "POST", {}))
                      }
                    >
                      Sync now
                    </button>
                    {!accountInfo.managed && accountInfo.status?.state === "sign-in-required" && (
                      <button
                        onClick={() => void work(() => window.tasks.connect())}
                      >
                        Reconnect account
                      </button>
                    )}
                    {!accountInfo.managed && (
                      <button
                        onClick={() =>
                          void work(() => api("/v1/portal/signout", "POST", {}))
                        }
                      >
                        Sign out
                      </button>
                    )}
                    <p>
                      All authorized workspaces sync automatically. Local boards
                      stay on this computer until you publish them.
                    </p>
                  </>
                )}
                {accountInfo.status?.error && (
                  <p role="status">{accountInfo.status.error}</p>
                )}
                {accountInfo.account && (
                  <details>
                    <summary>Sync details</summary>
                    {workspaces
                      .filter((w) => !w.local)
                      .map((w) => (
                        <p key={w.id}>
                          <strong>{w.name}</strong>: {syncLabel(w)}
                          <br />
                          Last synced:{" "}
                          {w.last_sync
                            ? new Date(w.last_sync).toLocaleString()
                            : "Not yet"}
                          <br />
                          Delivery:{" "}
                          {w.live?.state === "live"
                            ? "Live updates"
                            : "Periodic reconciliation"}
                          {w.live?.last_notification && (
                            <>
                              {" "}
                              · Last notification:{" "}
                              {new Date(
                                w.live.last_notification * 1000,
                              ).toLocaleString()}
                            </>
                          )}
                          {(w.retry_at || w.live?.next_retry) && (
                            <>
                              {" "}
                              · Retry:{" "}
                              {new Date(
                                (w.retry_at || w.live?.next_retry || 0) * 1000,
                              ).toLocaleTimeString()}
                            </>
                          )}
                          {w.error && (
                            <>
                              <br />
                              {w.error}
                            </>
                          )}
                        </p>
                      ))}
                  </details>
                )}
                {accountInfo.account && (
                  <section aria-labelledby="publish-heading">
                    <h3 id="publish-heading">Publish local boards</h3>
                    <p>
                      Each selected local board becomes a separate synced board.
                      Its contents and attachments will be shared with the
                      destination workspace’s members.
                    </p>
                    {accountInfo.offer_publish && (
                      <button
                        onClick={() =>
                          void work(() =>
                            api("/v1/portal/publish-offer", "POST", {}),
                          )
                        }
                      >
                        Skip for now
                      </button>
                    )}
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        void work(() =>
                          api("/v1/portal/publish", "POST", {
                            board_id: publishBoard,
                            workspace_id: publishWorkspace,
                            members: Object.fromEntries(
                              Object.entries(memberMapping).map(([k, v]) => [
                                k,
                                v === "unassigned" ? null : v,
                              ]),
                            ),
                          }),
                        );
                      }}
                    >
                      <label>
                        Local board
                        <select
                          value={publishBoard}
                          onChange={(e) => {
                            setPublishBoard(e.target.value);
                            setMemberMapping({});
                          }}
                        >
                          <option value={LOCAL_WORKSPACE}>
                            Local workspace board
                          </option>
                          {localBoards
                            .filter((b) => !b.data.deleted && !b.data.archived)
                            .map((b) => (
                              <option key={b.id} value={b.id}>
                                {b.data.title}
                              </option>
                            ))}
                        </select>
                      </label>
                      <label>
                        Destination workspace
                        <select
                          required
                          value={publishWorkspace}
                          onChange={(e) => setPublishWorkspace(e.target.value)}
                        >
                          <option value="">Choose workspace</option>
                          {workspaces
                            .filter(
                              (w) =>
                                !w.local &&
                                w.state === "connected" &&
                                w.scopes.some((s: string) =>
                                  ["board:write", "project:write"].includes(s),
                                ),
                            )
                            .map((w) => (
                              <option key={w.id} value={w.id}>
                                {w.name}
                              </option>
                            ))}
                        </select>
                      </label>
                      {publishContacts.map((id) => (
                        <label key={id}>
                          Map{" "}
                          {id === "local"
                            ? "Me"
                            : settings.local_members?.find(
                                (m: any) => m.id === id,
                              )?.name || id}
                          <select
                            required
                            value={memberMapping[id] || ""}
                            onChange={(e) =>
                              setMemberMapping({
                                ...memberMapping,
                                [id]: e.target.value,
                              })
                            }
                          >
                            <option value="">Choose member</option>
                            <option value="unassigned">Leave unassigned</option>
                            {publishMembers.map((m) => (
                              <option key={m.id} value={m.id}>
                                {m.display_name || m.name}
                              </option>
                            ))}
                          </select>
                        </label>
                      ))}
                      <button disabled={!publishWorkspace}>
                        Publish board
                      </button>
                    </form>
                  </section>
                )}
                {publications.map((j) => (
                  <p key={j.id}>
                    Board publication: {j.state}
                    {j.error && <> · {j.error}</>}
                    {["queued", "rejected"].includes(j.state) && (
                      <button
                        onClick={() =>
                          void work(() =>
                            api("/v1/portal/publish", "POST", { cancel: j.id }),
                          )
                        }
                      >
                        Cancel publication
                      </button>
                    )}
                    {j.state === "rejected" && (
                      <button
                        onClick={() =>
                          void work(() =>
                            api("/v1/portal/publish", "POST", { retry: j.id }),
                          )
                        }
                      >
                        Retry publication
                      </button>
                    )}
                  </p>
                ))}
                <h3>Local drafts</h3>
                {drafts.map((d) => (
                  <section key={d.id}>
                    <strong>{d.title ?? "Draft"}</strong>
                    <p>{d.reason}</p>
                    <button
                      onClick={() =>
                        void work(() =>
                          api("/v1/drafts/resubmit", "POST", { id: d.id }),
                        )
                      }
                    >
                      Review current permission and retry
                    </button>
                  </section>
                ))}
                <h3>Conflicting edits</h3>
                {conflicts.map((c) => (
                  <ConflictReview
                    key={c.id}
                    conflict={c}
                    submit={(fields) =>
                      work(() =>
                        api("/v1/conflicts/resolve", "POST", {
                          id: c.id,
                          choice: "fields",
                          fields,
                          revision: c.local_revision,
                        }),
                      )
                    }
                  />
                ))}
                <p>
                  Signing out stops synchronization. Retained offline copies are
                  marked disconnected and cannot be remotely guaranteed erased.
                </p>
              </section>
            </>
          )}
          {tab === "Settings" && (
            <>
              <section>
                <h2>Integrations · Agent access</h2>
                <p>
                  The bundled MCP server works locally without an Alshival.Ai
                  account.
                </p>
                <pre>{JSON.stringify(settings.mcp_config, null, 2)}</pre>
                <button
                  onClick={() =>
                    void work(async () => {
                      const result = await api("/v1/agent-grants", "POST", {
                        name: "Desktop agent",
                        scopes: [
                          "project:read",
                          "project:write",
                          "logs:read",
                          "monitor:read",
                          "notifications:read",
                        ],
                      });
                      setAgentConfig(JSON.stringify(result.config, null, 2));
                    })
                  }
                >
                  Create read/write agent grant
                </button>
                {agentConfig && (
                  <textarea
                    aria-label="Agent MCP configuration"
                    readOnly
                    value={agentConfig}
                    rows={10}
                  />
                )}
                {grants
                  .filter((g) => !g.revoked_at)
                  .map((g) => (
                    <p key={g.id}>
                      {g.name}{" "}
                      <button
                        onClick={() =>
                          void work(() =>
                            api("/v1/projects/keys/" + g.id, "DELETE"),
                          )
                        }
                      >
                        Revoke {g.name}
                      </button>
                    </p>
                  ))}
              </section>
              <section>
                <h2>Local assignees</h2>
                <form
                  onSubmit={(e) => {
                    const f = data(e);
                    void work(() =>
                      api("/v1/settings", "PATCH", {
                        local_members: [
                          ...(settings.local_members || []),
                          {
                            id: "contact:" + crypto.randomUUID(),
                            name: f.name,
                          },
                        ],
                      }),
                    );
                  }}
                >
                  <label>
                    Name
                    <input name="name" required maxLength={120} />
                  </label>
                  <button>Add local contact</button>
                </form>
                {(settings.local_members || []).map((m: any) => (
                  <p key={m.id}>
                    {m.name}{" "}
                    <button
                      onClick={() =>
                        void work(() =>
                          api("/v1/settings", "PATCH", {
                            local_members: settings.local_members.filter(
                              (other: any) => other.id !== m.id,
                            ),
                          }),
                        )
                      }
                    >
                      Remove contact
                    </button>
                  </p>
                ))}
              </section>
              <section>
                <h2>Background service</h2>
                <ServiceStatus />
                <p>
                  Closing the window leaves the service running after setup.
                </p>
                <button
                  onClick={() =>
                    void work(() =>
                      api("/v1/settings", "PATCH", {
                        paused: !settings.paused,
                      }),
                    )
                  }
                >
                  {settings.paused ? "Resume" : "Pause"} background work
                </button>
                <button
                  onClick={() => void work(() => window.tasks.service("stop"))}
                >
                  Stop service
                </button>
                <button
                  onClick={() => void work(() => window.tasks.service("start"))}
                >
                  Start service
                </button>
              </section>
              <section>
                <h2>Backup and restore</h2>
                <button onClick={() => void work(() => window.tasks.backup())}>
                  Export complete backup
                </button>
                <button onClick={() => void work(() => window.tasks.restore())}>
                  Restore backup
                </button>
                <p>
                  Backups include task data, attachments and logs. Secrets are
                  held separately in your operating system's protected
                  credential storage.
                </p>
              </section>
              <section>
                <h2>Attachments</h2>
                <label>
                  Record
                  <select
                    value={fileItem}
                    onChange={(e) => setFileItem(e.target.value)}
                  >
                    <option value="">Choose a record</option>
                    {items
                      .filter((i) => !i.data.deleted)
                      .map((i) => (
                        <option key={i.id} value={i.id}>
                          {i.data.title}
                        </option>
                      ))}
                  </select>
                </label>
                <button
                  disabled={!fileItem}
                  onClick={() =>
                    void work(async () => {
                      await window.tasks.importAttachment(fileItem);
                      setAttachments(
                        (await api("/v1/attachments?item_id=" + fileItem))
                          .attachments,
                      );
                    })
                  }
                >
                  Import file
                </button>
                {attachments.map((a) => (
                  <p key={a.id}>
                    {a.name} · {a.size} bytes{" "}
                    <button
                      onClick={() =>
                        void work(() => window.tasks.saveAttachment(a.id))
                      }
                    >
                      Save copy
                    </button>
                  </p>
                ))}
              </section>
              <section>
                <h2>Local API and retention</h2>
                <p>
                  Logs stay in local SQLite. Oldest events expire at the
                  retention or storage limit.
                </p>
                <p>
                  {settings.usage?.events ?? 0} events ·{" "}
                  {((settings.usage?.bytes ?? 0) / 1024 ** 2).toFixed(1)} MiB of
                  event data
                </p>
                <form
                  onSubmit={(e) => {
                    const f = data(e);
                    void work(async () => {
                      await api("/v1/settings", "PATCH", {
                        http_port: f.port ? Number(f.port) : null,
                        retention: {
                          days: Number(f.days),
                          max_bytes: Number(f.mb) * 1024 ** 2,
                        },
                      });
                      await window.tasks.service("restart");
                    });
                  }}
                >
                  <label>
                    Loopback HTTP port (blank disables)
                    <input
                      name="port"
                      type="number"
                      min="1024"
                      max="65535"
                      defaultValue={settings.http_port ?? ""}
                    />
                  </label>
                  <label>
                    Retention days
                    <input
                      name="days"
                      type="number"
                      defaultValue={settings.retention?.days ?? 7}
                      min="1"
                      max="365"
                    />
                  </label>
                  <label>
                    Storage limit MiB
                    <input
                      name="mb"
                      type="number"
                      defaultValue={
                        (settings.retention?.max_bytes ?? 1024 ** 3) / 1024 ** 2
                      }
                      min="1"
                      max="10240"
                    />
                  </label>
                  <button>Save and restart service</button>
                </form>
                <code>alshitasks mcp</code>
                <p>
                  Local MCP is read-only by default. Create an explicit scoped
                  keyring grant with <code>alshitasks grant</code>.
                </p>
              </section>
              <section>
                <h2>Store a credential</h2>
                <form
                  onSubmit={(e) => {
                    const f = data(e);
                    const form = e.currentTarget;
                    void work(async () => {
                      const v = await api("/v1/secret", "POST", {
                        value: f.value,
                      });
                      form.reset();
                      return { message: "Credential ID: " + v.id };
                    });
                  }}
                >
                  <label>
                    Secret
                    <input
                      name="value"
                      type="password"
                      autoComplete="off"
                      required
                    />
                  </label>
                  <button>Save credential securely</button>
                </form>
              </section>
            </>
          )}
        </div>
      </main>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);

function ConflictReview({
  conflict: c,
  submit,
}: {
  conflict: any;
  submit: (fields: Record<string, string>) => Promise<any>;
}) {
  const [choices, setChoices] = useState<Record<string, string>>({});
  const fields = Object.keys({
    ...c.base_data,
    ...c.local_data,
    ...c.remote_data,
  }).filter(
    (key) =>
      JSON.stringify(c.local_data[key]) !== JSON.stringify(c.base_data[key]) &&
      JSON.stringify(c.remote_data[key]) !== JSON.stringify(c.base_data[key]) &&
      JSON.stringify(c.local_data[key]) !== JSON.stringify(c.remote_data[key]),
  );
  useEffect(() => setChoices({}), [c.local_revision, c.remote]);
  return (
    <section>
      <h3>{c.local_data.title || "Conflicting edits"}</h3>
      <p>
        Independent edits are combined. Choose a version for each overlapping
        field.
      </p>
      {fields.map((field) => (
        <fieldset key={field}>
          <legend>{field}</legend>
          <details>
            <summary>Original value</summary>
            <pre>{JSON.stringify(c.base_data[field], null, 2)}</pre>
          </details>
          {(["local", "remote"] as const).map((side) => (
            <label key={side}>
              <input
                type="radio"
                name={`${c.id}-${field}`}
                checked={choices[field] === side}
                onChange={() => setChoices({ ...choices, [field]: side })}
              />
              {side === "local" ? "This computer" : "Portal"}
              <pre>{JSON.stringify(c[side + "_data"][field], null, 2)}</pre>
            </label>
          ))}
        </fieldset>
      ))}
      <button
        disabled={fields.some((field) => !choices[field])}
        onClick={() =>
          void submit(
            Object.fromEntries(fields.map((field) => [field, choices[field]])),
          )
        }
      >
        Save reviewed version
      </button>
    </section>
  );
}

function ServiceStatus() {
  const [status, setStatus] = useState<{
    running: boolean;
    managed: boolean;
    platform?: string;
  }>();
  const [error, setError] = useState("");
  const refresh = () =>
    window.tasks
      .service("status")
      .then(setStatus)
      .catch((e: Error) => setError(e.message));
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 10000);
    return () => clearInterval(timer);
  }, []);
  return (
    <div>
      <p role="status">
        {error ||
          (!status
            ? "Checking service…"
            : !status.running
              ? "Service stopped"
              : status.platform === "win32"
                ? "Running in your Windows session · startup at login is controlled in Account"
                : status.managed
                  ? "Running · starts at login and continues after closing this window"
                  : "Running without systemd · automatic restart and startup at login are unavailable")}
      </p>
      <p>
        Sleep, logout, or powering off can interrupt collection. Senders should
        buffer and retry.
      </p>
      <button
        onClick={() => {
          setError("");
          void window.tasks
            .service("restart")
            .then(refresh)
            .catch((e: Error) => setError(e.message));
        }}
      >
        Restart service
      </button>
    </div>
  );
}
