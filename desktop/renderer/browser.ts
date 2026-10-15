import { version } from "../../package.json";
/** Browser implementation of the same interface exposed by Electron preload. */
let csrf = "";
let authenticating: Promise<void> | undefined;
export const browserMode = !window.tasks;

export async function signIn(): Promise<void> {
  if (authenticating) return authenticating;
  authenticating = new Promise<void>((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "server-login";
    overlay.innerHTML = `<form><h1>Alshitask Server</h1><p>Sign in to your workspace.</p><label>Owner password<input name="password" type="password" autocomplete="current-password" required autofocus></label><button>Sign in</button><p role="alert"></p></form>`;
    document.body.append(overlay);
    const form = overlay.querySelector("form")!;
    form.onsubmit = async (event) => {
      event.preventDefault();
      const button = form.querySelector("button")!;
      button.disabled = true;
      try {
        const response = await fetch("/auth/login", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({password: new FormData(form).get("password")}) });
        const value = await response.json();
        if (!response.ok) throw new Error(value.error?.message || "Unable to sign in");
        csrf = value.csrf;
        form.reset();
        overlay.remove();
        resolve();
      } catch (error) { form.querySelector('[role="alert"]')!.textContent = String(error); }
      finally { button.disabled = false; }
    };
    form.querySelector("input")!.focus();
  });
  try { await authenticating; } finally { authenticating = undefined; }
}

async function request(path: string, method = "GET", body?: unknown): Promise<any> {
  if (!path.startsWith("/v1/") && !path.startsWith("/browser/") && !path.startsWith("/auth/")) throw new Error("Invalid browser API path");
  const response = await fetch(path, {method, headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: method === "GET" ? undefined : JSON.stringify(body ?? {})});
  const value = await response.json();
  if (!response.ok) {
    if (response.status === 401) {
      // Portal reauthorization is separate from the owner's server login.
      const session = await fetch("/auth/session");
      if (session.status === 401) void signIn();
    }
    throw new Error(value.error?.message || "Request failed");
  }
  if (method !== "GET") listeners.forEach(fn => fn());
  return value;
}

const listeners = new Set<() => void>();
let revision: number | undefined;
let polling = false;
function pick(max: number): Promise<File | undefined> {
  return new Promise((resolve, reject) => {
    const input = document.createElement("input"); input.type = "file";
    input.oncancel = () => resolve(undefined);
    input.onchange = () => {
      const file = input.files?.[0];
      if (file && file.size > max) reject(new Error("File exceeds upload limit")); else resolve(file);
    };
    input.click();
  });
}
async function encode(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = reject; reader.readAsDataURL(file);
  });
}
function download(blob: Blob, name: string) {
  const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
}
const unsupported = async () => { throw new Error("Manage this operation using the server CLI"); };

export async function initializeBrowser() {
  const response = await fetch("/auth/session");
  if (response.ok) csrf = (await response.json()).csrf;
  else if (response.status === 401) await signIn();
  else throw new Error("Server unavailable; reload to retry");
  window.tasks = {
    platform: "browser", request,
    getUpdates: async () => ({phase: "unsupported", currentVersion: version, availableVersion: null,
      progress: null, error: "", enabled: false, lastChecked: null, nextCheck: null, managed: true, supported: false}),
    checkUpdates: unsupported, setAutomaticUpdates: unsupported, installUpdate: unsupported,
    onUpdates: () => () => {}, onUpdatesOpen: () => () => {},
    getStartup: async () => ({enabled: false}), setStartup: unsupported,
    windowAction: async () => false, theme: async () => {}, onWindowState: () => () => {},
    initialNotification: async () => null, onNotification: () => () => {},
    onChanged: (callback) => { listeners.add(callback); return () => { listeners.delete(callback); }; },
    connect: async () => {
      const value = await request("/v1/portal/connect", "POST");
      const url = new URL(value.url);
      if (url.origin !== "https://alshival.ai" || url.pathname !== "/oauth/authorize/") throw new Error("Invalid portal sign-in URL");
      window.location.assign(url.href);
    },
    importAttachment: async (item, replaces) => {
      const file = await pick(20 * 1024**2); if (!file) return;
      return request("/v1/attachments", "POST", {item_id: item, replaces, name: file.name, content: await encode(file)});
    },
    importSshKey: async (resource, settings) => {
      const file = await pick(128 * 1024); if (!file) return;
      return request("/browser/ssh-key", "POST", {...settings, resource_id: resource, content: await encode(file)});
    },
    saveAttachment: async (id) => {
      const file = await request("/v1/attachments/" + encodeURIComponent(id));
      const bytes = Uint8Array.from(atob(file.content), c => c.charCodeAt(0));
      download(new Blob([bytes]), file.name);
    },
    backup: async () => {
      const response = await fetch("/browser/backup", {method: "POST", headers: {"X-CSRF-Token": csrf}});
      if (!response.ok) throw new Error("Backup failed; sign in and retry");
      download(await response.blob(), "alshitasks-backup.zip");
    },
    restore: unsupported, service: unsupported,
    external: async (value) => {
      const url = new URL(value);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Unsupported link");
      window.open(url.href, "_blank", "noopener,noreferrer");
    },
  };
  setInterval(async () => {
    if (polling || document.hidden || authenticating) return;
    polling = true;
    try {
      const value = await request("/v1/revision");
      if (revision !== value.revision) { revision = value.revision; listeners.forEach(fn => fn()); }
    } catch { /* Existing UI retains drafts while the connection recovers. */ }
    finally { polling = false; }
  }, 2000);
}
