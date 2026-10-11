export async function projectApi<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  return window.tasks.request(`/v1/projects${path}`, method, body) as Promise<T>;
}
