export const LOCAL_WORKSPACE = "00000000-0000-4000-8000-000000000001";
let workspace = LOCAL_WORKSPACE;
export function selectWorkspace(id: string) {
  workspace = id;
}
export async function projectApi<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const selected = workspace;
  return window.tasks.request(
    `/v1/projects${path}${path.includes("?") ? "&" : "?"}workspace_id=${encodeURIComponent(selected)}`,
    method,
    body,
  ) as Promise<T>;
}

export function workspaceApi(selected: string) {
  return async function <T>(
    path: string,
    method = "GET",
    body?: unknown,
  ): Promise<T> {
    return window.tasks.request(
      `/v1/projects${path}${path.includes("?") ? "&" : "?"}workspace_id=${encodeURIComponent(selected)}`,
      method,
      body,
    ) as Promise<T>;
  };
}
