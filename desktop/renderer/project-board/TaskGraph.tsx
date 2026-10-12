import { useId, useMemo, useRef, useState } from "react";
import { type Edge, type Item, type Status, labels } from "./types";
import "./task-graph.css";

type Point = { x: number; y: number };
const W = 1100,
  H = 650,
  R = 34;
const number = (task: Item) =>
  task.number != null ? `Task #${task.number}` : "Task";

// Settle once per topology change. No animation loop, remote service or network dependency.
function layout(ids: string[], edges: Edge[]): Map<string, Point> {
  const sorted = [...ids].sort();
  const points = new Map(
    sorted.map((id, i) => {
      const angle = i * 2.399963229728653,
        radius = 90 * Math.sqrt(i);
      return [id, { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius }];
    }),
  );
  const links = edges.filter(
    (e) => points.has(e.source_id) && points.has(e.target_id),
  );
  for (let step = 0; step < 100; step++) {
    const forces = new Map(sorted.map((id) => [id, { x: 0, y: 0 }]));
    // Spatial bins keep repulsion local even on a large board.
    const cells = new Map<string, string[]>();
    for (const id of sorted) {
      const p = points.get(id)!,
        key = `${Math.floor(p.x / 260)},${Math.floor(p.y / 260)}`;
      cells.set(key, [...(cells.get(key) ?? []), id]);
    }
    for (const id of sorted) {
      const p = points.get(id)!,
        f = forces.get(id)!;
      const cx = Math.floor(p.x / 260),
        cy = Math.floor(p.y / 260);
      for (let x = cx - 1; x <= cx + 1; x++)
        for (let y = cy - 1; y <= cy + 1; y++) {
          for (const other of cells.get(`${x},${y}`) ?? []) {
            if (id === other) continue;
            const q = points.get(other)!,
              dx = p.x - q.x,
              dy = p.y - q.y;
            const d = Math.max(1, Math.hypot(dx, dy)),
              strength = Math.min(30, 18000 / (d * d));
            f.x += (dx / d) * strength;
            f.y += (dy / d) * strength;
          }
        }
      f.x -= p.x * 0.003;
      f.y -= p.y * 0.003;
    }
    for (const edge of links) {
      const a = points.get(edge.source_id)!,
        b = points.get(edge.target_id)!;
      const dx = b.x - a.x,
        dy = b.y - a.y,
        d = Math.max(1, Math.hypot(dx, dy));
      const strength = (d - 205) * 0.035;
      const f = forces.get(edge.source_id)!,
        g = forces.get(edge.target_id)!;
      f.x += (dx / d) * strength;
      f.y += (dy / d) * strength;
      g.x -= (dx / d) * strength;
      g.y -= (dy / d) * strength;
    }
    for (const id of sorted) {
      const p = points.get(id)!,
        f = forces.get(id)!,
        cooling = 1 - step / 125;
      p.x += Math.max(-15, Math.min(15, f.x)) * cooling;
      p.y += Math.max(-15, Math.min(15, f.y)) * cooling;
    }
  }
  return points;
}

export function TaskGraph({
  tasks,
  edges,
  statuses,
  open,
}: {
  tasks: Item[];
  edges: Edge[];
  statuses: Status[];
  open: (task: Item) => void;
}) {
  const marker = useId().replace(/:/g, "");
  const svg = useRef<SVGSVGElement>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [linkedOnly, setLinkedOnly] = useState(false);
  const [showRelated, setShowRelated] = useState(true);
  const [positions, setPositions] = useState<Record<string, Point>>({});
  const [camera, setCamera] = useState<{
    x: number;
    y: number;
    scale: number;
  } | null>(null);
  const drag = useRef<{
    id?: string;
    pointer: number;
    start: Point;
    origin: Point;
    moved: boolean;
  } | null>(null);
  const suppressClick = useRef(false);
  const taskMap = new Map(tasks.map((t) => [t.id, t]));
  const links = edges.filter(
    (e) =>
      taskMap.has(e.source_id) &&
      taskMap.has(e.target_id) &&
      (showRelated || e.kind !== "related"),
  );
  const linked = new Set(links.flatMap((e) => [e.source_id, e.target_id]));
  const visible = tasks.filter((t) => !linkedOnly || linked.has(t.id));
  // Metadata edits and sync revisions must not rearrange a graph being explored.
  const topology = JSON.stringify([
    visible.map((t) => t.id).sort(),
    links.map((e) => [e.source_id, e.target_id, e.kind]).sort(),
  ]);
  const initial = useMemo(
    () =>
      layout(
        visible.map((t) => t.id),
        links,
      ),
    [topology],
  );
  const points = new Map(
    [...initial].map(([id, p]) => [id, positions[id] ?? p]),
  );
  const bounds = [...points.values()];
  const minX = Math.min(0, ...bounds.map((p) => p.x - 110)),
    maxX = Math.max(0, ...bounds.map((p) => p.x + 110));
  const minY = Math.min(0, ...bounds.map((p) => p.y - 65)),
    maxY = Math.max(0, ...bounds.map((p) => p.y + 105));
  const fit = {
    x: (minX + maxX) / 2,
    y: (minY + maxY) / 2,
    scale: Math.min(
      1.4,
      (W - 70) / Math.max(220, maxX - minX),
      (H - 60) / Math.max(180, maxY - minY),
    ),
  };
  const view = camera ?? fit;
  const focused =
    selected && taskMap.has(selected) ? taskMap.get(selected)! : null;
  const neighbors = new Set(
    links
      .filter((e) => e.source_id === selected || e.target_id === selected)
      .flatMap((e) => [e.source_id, e.target_id]),
  );
  const done = (task: Item) =>
    (statuses.find((s) => s.id === task.data.status_id)?.category ??
      task.data.state) === "done";
  const blockers = (id: string) =>
    edges.filter(
      (e) =>
        e.kind === "depends_on" &&
        e.source_id === id &&
        taskMap.has(e.target_id) &&
        !done(taskMap.get(e.target_id)!),
    );
  const screen = (event: { clientX: number; clientY: number }): Point => {
    const matrix = svg.current?.getScreenCTM();
    const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(
      matrix?.inverse(),
    );
    return { x: p.x, y: p.y };
  };
  const zoom = (factor: number) =>
    setCamera({
      ...view,
      scale: Math.max(0.1, Math.min(3, view.scale * factor)),
    });
  const matches = (task: Item) =>
    `${number(task)} ${task.data.title}`
      .toLowerCase()
      .includes(query.trim().toLowerCase());
  const relations = focused
    ? edges
        .filter((e) => e.source_id === focused.id || e.target_id === focused.id)
        .flatMap((e) => {
          const other = taskMap.get(
            e.source_id === focused.id ? e.target_id : e.source_id,
          );
          if (!other) return [];
          const label =
            e.kind === "related"
              ? "Related to"
              : e.source_id === focused.id
                ? done(other)
                  ? "Depends on · completed"
                  : done(focused)
                    ? "Depends on"
                    : "Blocked by"
                : done(focused) || done(other)
                  ? "Required by"
                  : "Blocks";
          return [{ edge: e, other, label }];
        })
    : [];
  return (
    <section className="task-network" aria-label="Task relationships">
      <div className="task-network-toolbar">
        <label className="task-network-search">
          Find a task
          <input
            type="search"
            placeholder="Task number or title"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={linkedOnly}
            onChange={(e) => {
              setLinkedOnly(e.target.checked);
              setCamera(null);
            }}
          />{" "}
          Connected tasks only
        </label>
        <label>
          <input
            type="checkbox"
            checked={showRelated}
            onChange={(e) => setShowRelated(e.target.checked)}
          />{" "}
          Related links
        </label>
        <div
          className="task-network-controls"
          role="group"
          aria-label="Graph controls"
        >
          <button
            className="button btn-frost"
            aria-label="Zoom out"
            onClick={() => zoom(1 / 1.25)}
          >
            −
          </button>
          <output aria-label="Graph zoom">
            {Math.round(view.scale * 100)}%
          </output>
          <button
            className="button btn-frost"
            aria-label="Zoom in"
            onClick={() => zoom(1.25)}
          >
            +
          </button>
          <button className="button btn-frost" onClick={() => setCamera(null)}>
            Fit graph
          </button>
          <button
            className="button btn-frost"
            onClick={() => {
              setPositions({});
              setCamera(null);
            }}
          >
            Reset layout
          </button>
        </div>
      </div>
      <div className="task-network-body">
        <div className="task-network-canvas">
          {!visible.length ? (
            <p className="task-network-empty">
              {tasks.length
                ? "No connected tasks match this view. Turn off “Connected tasks only” to see all tasks."
                : "Add tasks and relationships to build your graph."}
            </p>
          ) : (
            <svg
              ref={svg}
              viewBox={`0 0 ${W} ${H}`}
              role="group"
              aria-label="Task network graph"
              onPointerDown={(event) => {
                if (event.button !== 0 || event.target !== event.currentTarget)
                  return;
                drag.current = {
                  pointer: event.pointerId,
                  start: screen(event),
                  origin: { x: view.x, y: view.y },
                  moved: false,
                };
                event.currentTarget.setPointerCapture(event.pointerId);
              }}
              onPointerMove={(event) => {
                const d = drag.current;
                if (!d || d.pointer !== event.pointerId) return;
                const p = screen(event),
                  dx = (p.x - d.start.x) / view.scale,
                  dy = (p.y - d.start.y) / view.scale;
                if (Math.hypot(p.x - d.start.x, p.y - d.start.y) > 4)
                  d.moved = true;
                if (!d.moved) return;
                if (d.id)
                  setPositions((old) => ({
                    ...old,
                    [d.id!]: { x: d.origin.x + dx, y: d.origin.y + dy },
                  }));
                else
                  setCamera({
                    ...view,
                    x: d.origin.x - dx,
                    y: d.origin.y - dy,
                  });
              }}
              onPointerUp={(event) => {
                if (drag.current?.pointer !== event.pointerId) return;
                suppressClick.current = drag.current.moved;
                drag.current = null;
                if (svg.current?.hasPointerCapture(event.pointerId))
                  svg.current.releasePointerCapture(event.pointerId);
              }}
              onPointerCancel={() => {
                drag.current = null;
              }}
              onClick={(event) => {
                if (suppressClick.current) {
                  suppressClick.current = false;
                  return;
                }
                if (event.target === event.currentTarget) setSelected(null);
              }}
            >
              <defs>
                <marker
                  id={marker}
                  markerWidth="9"
                  markerHeight="9"
                  refX="8"
                  refY="4.5"
                  orient="auto"
                  markerUnits="userSpaceOnUse"
                >
                  <path d="M0 0 L9 4.5 L0 9 Z" fill="context-stroke" />
                </marker>
              </defs>
              <g
                transform={`translate(${W / 2} ${H / 2}) scale(${view.scale}) translate(${-view.x} ${-view.y})`}
              >
                {links.map((edge) => {
                  // Stored source depends on target; visual arrows flow prerequisite → dependent.
                  const a = points.get(
                      edge.kind === "depends_on"
                        ? edge.target_id
                        : edge.source_id,
                    ),
                    b = points.get(
                      edge.kind === "depends_on"
                        ? edge.source_id
                        : edge.target_id,
                    );
                  if (!a || !b) return null;
                  const dx = b.x - a.x,
                    dy = b.y - a.y,
                    length = Math.max(1, Math.hypot(dx, dy));
                  const ax = a.x + (dx / length) * (R + 5),
                    ay = a.y + (dy / length) * (R + 5),
                    bx = b.x - (dx / length) * (R + 9),
                    by = b.y - (dy / length) * (R + 9);
                  const active =
                    !focused ||
                    edge.source_id === focused.id ||
                    edge.target_id === focused.id;
                  const blocked =
                    edge.kind === "depends_on" &&
                    !done(taskMap.get(edge.target_id)!) &&
                    !done(taskMap.get(edge.source_id)!);
                  return (
                    <g
                      key={edge.id}
                      className={`task-network-edge ${edge.kind}${blocked ? " is-blocking" : ""}`}
                      opacity={active ? 1 : 0.13}
                    >
                      <path
                        d={`M${ax} ${ay} Q${(ax + bx) / 2 - dy * 0.12} ${(ay + by) / 2 + dx * 0.12} ${bx} ${by}`}
                        markerEnd={
                          edge.kind === "depends_on"
                            ? `url(#${marker})`
                            : undefined
                        }
                      />
                      <title>
                        {number(taskMap.get(edge.source_id)!)}{" "}
                        {edge.kind === "depends_on"
                          ? "depends on"
                          : "is related to"}{" "}
                        {number(taskMap.get(edge.target_id)!)}
                        {blocked ? " · unresolved dependency" : ""}
                      </title>
                      {focused && active && (
                        <text
                          x={(ax + bx) / 2 - dy * 0.06}
                          y={(ay + by) / 2 + dx * 0.06 - 8}
                          textAnchor="middle"
                        >
                          {edge.kind === "depends_on"
                            ? blocked
                              ? "blocks"
                              : "required by"
                            : "related"}
                        </text>
                      )}
                    </g>
                  );
                })}
                {visible.map((task) => {
                  const p = points.get(task.id)!,
                    status = statuses.find((s) => s.id === task.data.status_id),
                    blocked = blockers(task.id).length > 0 && !done(task);
                  const dim =
                    !matches(task) ||
                    (focused &&
                      task.id !== focused.id &&
                      !neighbors.has(task.id));
                  return (
                    <g
                      key={task.id}
                      className={`task-network-node${selected === task.id ? " is-selected" : ""}`}
                      transform={`translate(${p.x} ${p.y})`}
                      opacity={dim ? 0.2 : 1}
                      role="button"
                      tabIndex={0}
                      aria-label={`${number(task)}: ${task.data.title}${blocked ? ", blocked" : ""}`}
                      aria-pressed={selected === task.id}
                      onPointerDown={(event) => {
                        if (event.button !== 0) return;
                        event.stopPropagation();
                        setCamera(view);
                        suppressClick.current = false;
                        drag.current = {
                          id: task.id,
                          pointer: event.pointerId,
                          start: screen(event),
                          origin: p,
                          moved: false,
                        };
                        event.currentTarget.setPointerCapture(event.pointerId);
                      }}
                      onClick={(event) => {
                        event.stopPropagation();
                        if (suppressClick.current) {
                          suppressClick.current = false;
                          return;
                        }
                        setSelected(task.id);
                      }}
                      onDoubleClick={() => open(task)}
                      onKeyDown={(event) => {
                        if (["Enter", " "].includes(event.key)) {
                          event.preventDefault();
                          setSelected(task.id);
                        }
                        if (event.key === "Escape") setSelected(null);
                      }}
                    >
                      <circle r={R + 5} className="task-network-ring" />
                      <circle r={R} />
                      <text
                        className="task-network-number"
                        textAnchor="middle"
                        y="5"
                      >
                        {task.number != null ? `#${task.number}` : "Task"}
                      </text>
                      <circle
                        className="task-network-status"
                        cx="25"
                        cy="-25"
                        r="7"
                        style={{ fill: status?.color ?? "var(--muted)" }}
                      />
                      <text
                        className="task-network-title"
                        textAnchor="middle"
                        y="59"
                      >
                        {task.data.title.length > 29
                          ? task.data.title.slice(0, 28) + "…"
                          : task.data.title}
                      </text>
                      <text
                        className="task-network-state"
                        textAnchor="middle"
                        y="78"
                      >
                        {blocked ? "Blocked · " : ""}
                        {status?.name ??
                          labels[task.data.state] ??
                          task.data.state}
                      </text>
                      <title>
                        {number(task)}: {task.data.title}
                      </title>
                    </g>
                  );
                })}
              </g>
            </svg>
          )}
        </div>
        {focused && (
          <aside
            className="task-network-inspector"
            aria-label="Selected task relationships"
          >
            <div className="task-network-inspector-heading">
              <p className="sp-eyebrow">{number(focused)}</p>
              <button
                className="button btn-frost"
                aria-label="Clear graph selection"
                onClick={() => setSelected(null)}
              >
                ×
              </button>
            </div>
            <h3>{focused.data.title}</h3>
            <button className="button btn-frost" onClick={() => open(focused)}>
              Open task
            </button>
            <h4>Relationships</h4>
            {relations.length ? (
              <ul>
                {relations.map(({ edge, other, label }) => (
                  <li key={edge.id}>
                    <span>{label}</span>
                    <button onClick={() => setSelected(other.id)}>
                      {number(other)} · {other.data.title}
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p>
                No task relationships yet. Open this task to add a dependency or
                related task.
              </p>
            )}
          </aside>
        )}
      </div>
      <footer className="task-network-legend">
        <span>
          <b>→</b> Prerequisite → dependent
        </span>
        <span>
          <b className="task-network-blocking-key">→</b> Blocks
        </span>
        <span>┄ Related</span>
        <span>
          {visible.length} tasks · {links.length} links
        </span>
        <small>
          Drag nodes to arrange · Drag background to pan · Select a task to
          explore · Double-click to open
        </small>
      </footer>
      {query && (
        <p role="status">
          {visible.filter(matches).length} matching tasks highlighted.
        </p>
      )}
    </section>
  );
}
