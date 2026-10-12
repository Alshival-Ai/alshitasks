import { TypeIcon, TaskLabels, type Taxonomy } from "./Taxonomy";
import { AssigneePicker } from "./AssigneePicker";
import { useEffect, useRef, useState } from "react";
import { taskDragging } from "./taskDragging";
import { StatusPicker } from "./StatusPicker";
import {
  type Item,
  type Status,
  type Member,
  type Change,
  lifecycle,
} from "./types";
import "./portal/responsive-board.js";
export function TaskColumns({
  taxonomy,
  tasks,
  statuses,
  members,
  namespace,
  board,
  open,
  change,
  report,
  expanded,
  manager,
}: {
  taxonomy: Taxonomy;
  tasks: Item[];
  statuses: Status[];
  members: Member[];
  namespace?: string;
  board: string;
  open: (item: Item) => void;
  change: Change;
  report: (text: string) => void;
  expanded: boolean;
  manager: boolean;
}) {
  const root = useRef<HTMLDivElement>(null),
    adapter = useRef<ReturnType<Window["AlshivalResponsiveBoard"]>>(null);
  const pending = useRef(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const current = useRef({ tasks, statuses, change, report });
  current.current = { tasks, statuses, change, report };
  const move = async (item: Item, status: string) => {
    if (pending.current || status === item.data.status_id) return;
    pending.current = true;
    setBusy(true);
    setMessage("Saving…");
    try {
      await current.current.change(item, { status_id: status });
      setMessage(
        `Moved to ${current.current.statuses.find((s) => s.id === status)?.name ?? "status"}.`,
      );
    } catch (error) {
      setMessage("");
      current.current.report((error as Error).message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  useEffect(() => {
    if (!root.current) return;
    const life = lifecycle();
    let gestures: ReturnType<typeof taskDragging> | undefined;
    adapter.current = window.AlshivalResponsiveBoard(root.current, life, {
      cancel: () => gestures?.cancel(),
      availableWidth: () => root.current?.clientWidth ?? window.innerWidth,
      report: setMessage,
    });
    gestures = taskDragging(
      root.current,
      life,
      adapter.current,
      () => ({ ...current.current, busy: pending.current }),
      (item, status) => void move(item, status),
      setMessage,
    );
    return () => life.close();
  }, [board, namespace]);
  useEffect(() => adapter.current?.refresh(), [tasks, statuses, expanded]);
  // Older local/API tasks may only have a legacy state. Keep them visible and movable.
  const statusFor = (item: Item) =>
    item.data.status_id ??
    statuses.find((status) => status.legacy_state === item.data.state)?.id ??
    statuses.find((status) => status.is_default)?.id;
  return (
    <div
      ref={root}
      className="shared-project project-columns-adapter"
      data-shared-project
      data-board-user={namespace}
      data-board-url={board || "workspace"}
      data-board-expanded={expanded ? "" : undefined}
    >
      <div data-shared-project-content>
        <div className="sp-board-navigation">
          <div
            className="sp-layout-switch"
            role="group"
            aria-label="Board layout"
          >
            <button
              type="button"
              className="button btn-frost"
              data-board-layout-choice="swipe"
            >
              Swipe
            </button>
            <button
              type="button"
              className="button btn-frost"
              data-board-layout-choice="stacked"
            >
              Stacked
            </button>
          </div>
          <p className="sp-gesture-hint">
            Swipe to browse · Hold a task, then drop on a status
          </p>
          <div
            className="sp-column-navigation"
            role="group"
            aria-label="Navigate columns"
          >
            <button
              type="button"
              className="button btn-frost"
              data-board-step="-1"
              aria-label="Previous column"
            >
              ←
            </button>
            <button
              type="button"
              className="button btn-frost"
              data-board-step="1"
              aria-label="Next column"
            >
              →
            </button>
          </div>
        </div>
        <nav className="sp-mobile-filter" aria-label="Task status">
          {statuses.map((status) => (
            <button
              type="button"
              key={status.id}
              data-shared-status={status.id}
              onClick={() => adapter.current?.select(status.id)}
            >
              {status.name}
              <span>
                {tasks.filter((task) => statusFor(task) === status.id).length}
              </span>
            </button>
          ))}
        </nav>
        <div className="sp-columns">
          {statuses.map((status) => (
            <section
              className="sp-column"
              key={status.id}
              data-sp-state={status.id}
              data-status-label={status.name}
              data-status-color={status.color}
            >
              <header>
                <h3>
                  <span className="sp-status-dot" aria-hidden="true" />
                  {status.name}
                </h3>
                <span>
                  {tasks.filter((item) => statusFor(item) === status.id).length}
                </span>
              </header>
              <div className="sp-stack">
                {tasks
                  .filter((item) => statusFor(item) === status.id)
                  .map((item) => (
                    <article
                      key={item.id}
                      className="sp-card project-task"
                      data-status-color={status.color}
                      data-project-card={item.id}
                      aria-busy={busy}
                    >
                      <p className="sp-card-kind">
                        {item.kind === "ticket"
                          ? "Service ticket"
                          : item.number != null
                            ? `Task #${item.number}`
                            : "Task"}
                      </p>
                      {manager && (
                        <details className="sp-task-menu">
                          <summary
                            aria-label={`Actions for ${item.data.title}`}
                          >
                            •••
                          </summary>
                          <button
                            type="button"
                            className="button btn-frost"
                            onClick={() =>
                              void change(item, {
                                archived: !item.data.archived,
                              }).catch((error) => report(error.message))
                            }
                          >
                            {item.data.archived
                              ? "Restore from archive"
                              : "Archive"}
                          </button>
                          <button
                            type="button"
                            className="button btn-frost"
                            onClick={() =>
                              void change(item, { deleted: true }).catch(
                                (error) => report(error.message),
                              )
                            }
                          >
                            Move to Trash
                          </button>
                        </details>
                      )}
                      <TypeIcon
                        type={taxonomy.types.find(
                          (t) => t.id === item.data.type_id,
                        )}
                      />
                      <h4>
                        <button
                          type="button"
                          className="project-text-button"
                          onClick={() => open(item)}
                        >
                          {item.data.title}
                        </button>
                      </h4>
                      <div className="sp-card-meta">
                        <AssigneePicker
                          item={item}
                          members={members}
                          change={change}
                          report={report}
                        />
                        {item.data.due_on && (
                          <time dateTime={item.data.due_on}>
                            {item.data.due_on}
                          </time>
                        )}
                      </div>
                      <span
                        className={`sp-pill${["urgent", "high"].includes(item.data.priority) ? " sp-attention" : ""}`}
                      >
                        {item.data.priority}
                      </span>
                      <StatusPicker
                        item={item}
                        statuses={statuses}
                        statusId={statusFor(item)}
                        busy={busy}
                        move={move}
                      />
                      <TaskLabels
                        ids={item.data.label_ids}
                        catalog={taxonomy}
                      />
                    </article>
                  ))}
                {!tasks.some((item) => statusFor(item) === status.id) && (
                  <p className="sp-empty-small">Nothing here yet.</p>
                )}
              </div>
            </section>
          ))}
        </div>
      </div>
      <p className="sp-board-feedback" role="status" aria-live="polite">
        {message}
      </p>
    </div>
  );
}
