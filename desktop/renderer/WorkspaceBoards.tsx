import { useEffect, useMemo, useState } from "react";
import { workspaceApi } from "./project-board/api";
import type { Item } from "./project-board/types";
import type { Workspace } from "./Workspaces";

export function WorkspaceBoards({
  workspace,
  select,
}: {
  workspace: Workspace;
  select: (workspace: string, board: string) => void;
}) {
  const api = useMemo(() => workspaceApi(workspace.id), [workspace.id]);
  const [open, setOpen] = useState(false),
    [manage, setManage] = useState(false);
  const [boards, setBoards] = useState<Item[]>([]),
    [canManage, setCanManage] = useState(false);
  const [loaded, setLoaded] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const refresh = async () => {
    const [catalog, access] = await Promise.all([
      api<{ boards: Item[] }>("/boards"),
      api<{ can_manage: boolean }>("/statuses"),
    ]);
    setBoards(catalog.boards);
    setCanManage(
      access.can_manage &&
        !!(
          workspace.local ||
          workspace.scopes?.some((s) =>
            ["project:write", "board:write"].includes(s),
          )
        ),
    );
    setLoaded(true);
    setError("");
  };
  useEffect(() => {
    if (!open) return;
    let alive = true;
    Promise.all([
      api<{ boards: Item[] }>("/boards"),
      api<{ can_manage: boolean }>("/statuses"),
    ])
      .then(([catalog, access]) => {
        if (alive) {
          setBoards(catalog.boards);
          setCanManage(
            access.can_manage &&
              !!(
                workspace.local ||
                workspace.scopes?.some((s) =>
                  ["project:write", "board:write"].includes(s),
                )
              ),
          );
          setLoaded(true);
          setError("");
        }
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [open, api]);
  const save = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const update = (board: Item, data: Record<string, unknown>) =>
    api(`/items/${board.id}`, "PATCH", {
      revision: board.revision,
      idempotency_key: crypto.randomUUID(),
      data,
    });
  return (
    <details
      className="wd-boards"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>Boards</summary>
      {error && (
        <p role="alert">
          {error}{" "}
          <button
            onClick={() => void refresh().catch((e) => setError(e.message))}
          >
            Retry
          </button>
        </p>
      )}
      {!loaded && !error && <p role="status">Loading boards…</p>}
      {loaded && (
        <>
          <nav aria-label={`Boards in ${workspace.name}`}>
            {workspace.local && (
              <button onClick={() => select(workspace.id, "")}>
                Workspace board
              </button>
            )}
            {boards
              .filter((b) => !b.data.archived)
              .map((b) => (
                <button key={b.id} onClick={() => select(workspace.id, b.id)}>
                  {b.data.title}
                </button>
              ))}
          </nav>
          {canManage && (
            <button
              className="button btn-frost"
              aria-expanded={manage}
              onClick={() => setManage(!manage)}
            >
              Manage boards
            </button>
          )}
          {canManage && manage && (
            <section
              className="wd-board-manager"
              aria-label={`Manage boards in ${workspace.name}`}
            >
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  const form = event.currentTarget;
                  const name = String(
                    new FormData(form).get("name") || "",
                  ).trim();
                  void save(async () => {
                    await api("/items", "POST", {
                      kind: "board",
                      data: { title: name },
                      idempotency_key: crypto.randomUUID(),
                    });
                    form.reset();
                  });
                }}
              >
                <label>
                  Board name
                  <input name="name" maxLength={120} required disabled={busy} />
                </label>
                <button className="button btn matrix-btn" disabled={busy}>
                  Create board
                </button>
              </form>
              {boards.map((board) => (
                <form
                  key={`${board.id}:${board.revision}`}
                  onSubmit={(event) => {
                    event.preventDefault();
                    const name = String(
                      new FormData(event.currentTarget).get("name") || "",
                    ).trim();
                    void save(() => update(board, { title: name }));
                  }}
                >
                  <label>
                    {board.data.archived ? "Archived board" : "Board name"}
                    <input
                      name="name"
                      aria-label={`Name for ${board.data.title}`}
                      defaultValue={board.data.title}
                      maxLength={120}
                      required
                      disabled={busy}
                    />
                  </label>
                  <button className="button btn-frost" disabled={busy}>
                    Rename
                  </button>
                  <button
                    className="button btn-frost"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void save(() =>
                        update(board, { archived: !board.data.archived }),
                      )
                    }
                  >
                    {board.data.archived ? "Restore board" : "Archive board"}
                  </button>
                </form>
              ))}
            </section>
          )}
        </>
      )}
    </details>
  );
}
