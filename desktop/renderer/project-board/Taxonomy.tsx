import { useEffect, useRef, useState } from "react";
import "./portal/task-taxonomy.js";
import "./portal/task-taxonomy.css";
const icons = new URL("./portal/task-types.svg?no-inline", import.meta.url)
  .href;
import { workspaceApi } from "./api";
export type TaskType = {
  id: string;
  name: string;
  icon: string;
  position: number;
  is_default: boolean;
  archived: boolean;
  revision: number;
};
export type TaskLabel = {
  id: string;
  name: string;
  archived: boolean;
  revision: number;
};
export type Taxonomy = {
  types: TaskType[];
  labels: TaskLabel[];
  revision: string;
  can_manage?: boolean;
};
export const emptyTaxonomy: Taxonomy = { types: [], labels: [], revision: "" };
declare global {
  interface Window {
    AlshivalTaskTaxonomy: (
      root: HTMLElement,
      request: (method: string, body?: unknown) => Promise<unknown>,
    ) => () => void;
  }
}
export function TypeIcon({ type }: { type?: TaskType }) {
  return type ? (
    <svg className="task-type-icon" role="img" aria-label={type.name}>
      <title>{type.name}</title>
      <use href={`${icons}#${type.icon}`} />
    </svg>
  ) : null;
}
export function TaskLabels({
  ids,
  catalog,
}: {
  ids?: string[];
  catalog: Taxonomy;
}) {
  return ids?.length ? (
    <p className="task-labels">
      {ids.map((id) => (
        <span key={id}>
          {catalog.labels.find((label) => label.id === id)?.name ||
            "Unknown label"}
        </span>
      ))}
    </p>
  ) : null;
}
export function TaxonomyFields({
  catalog,
  type,
  labels = [],
}: {
  catalog: Taxonomy;
  type?: string | null;
  labels?: string[];
}) {
  const [query, setQuery] = useState("");
  // Preserve an in-progress selection; the service checks current archive state on save.
  const [choices] = useState(catalog);
  return (
    <>
      <label>
        Type
        <select
          aria-label="Type"
          name="type_id"
          defaultValue={type || choices.types.find((t) => t.is_default)?.id}
        >
          {choices.types
            .filter((t) => !t.archived || t.id === type)
            .map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
                {t.archived ? " (archived)" : ""}
              </option>
            ))}
        </select>
      </label>
      <fieldset className="taxonomy-label-picker">
        <legend>Labels</legend>
        <input
          type="search"
          aria-label="Search labels"
          placeholder="Search labels"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div>
          {choices.labels
            .filter((l) => !l.archived || labels.includes(l.id))
            .map((l) => (
              <label
                key={l.id}
                hidden={!l.name.toLowerCase().includes(query.toLowerCase())}
              >
                <input
                  type="checkbox"
                  name="label_ids"
                  value={l.id}
                  defaultChecked={labels.includes(l.id)}
                />
                {l.name}
                {l.archived ? " (archived)" : ""}
              </label>
            ))}
        </div>
        {!choices.labels.length && (
          <small>No labels configured in workspace settings.</small>
        )}
      </fieldset>
    </>
  );
}
export function TaxonomySettings({ workspace }: { workspace: string }) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!root.current) return;
    const api = workspaceApi(workspace);
    return window.AlshivalTaskTaxonomy(root.current, (method, body) =>
      api(
        method === "REVIEW" ? "/taxonomy/review" : "/taxonomy",
        method === "REVIEW" ? "POST" : method,
        body,
      ),
    );
  }, [workspace]);
  return <div ref={root} />;
}

export function TaxonomyFilter({
  title,
  options,
  selected,
  change,
}: {
  title: string;
  options: { id: string; name: string }[];
  selected: string[];
  change: (ids: string[]) => void;
}) {
  return (
    <details className="taxonomy-filter">
      <summary>
        {title}
        {selected.length ? ` (${selected.length})` : ""}
      </summary>
      <div>
        {options.map((option) => (
          <label key={option.id}>
            <input
              type="checkbox"
              checked={selected.includes(option.id)}
              onChange={(event) =>
                change(
                  event.target.checked
                    ? [...selected, option.id]
                    : selected.filter((id) => id !== option.id),
                )
              }
            />
            {option.name}
          </label>
        ))}
        {!options.length && <small>No labels configured</small>}
        <button
          className="button btn-frost"
          type="button"
          onClick={() => change([])}
        >
          Clear
        </button>
      </div>
    </details>
  );
}
