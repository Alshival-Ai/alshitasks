// Stable release downloads and scheduling. Installation stays in platform adapters.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { EventEmitter } = require("node:events");
const REPO = "Alshival-Ai/alshitasks";
const WEEK = 7 * 86400000;
function version(value) {
  const match = /^v?(\d+)\.(\d+)\.(\d+)$/.exec(value || "");
  if (!match) throw new Error("Invalid stable release version.");
  return match.slice(1).map(Number);
}
function newer(a, b) {
  const x = version(a),
    y = version(b);
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i];
  return false;
}
function select(release, platform, arch) {
  if (arch !== "x64" || !["win32", "linux"].includes(platform))
    throw new Error("Updates are unavailable for this installation.");
  if (release.draft || release.prerelease)
    throw new Error("Expected a stable release.");
  version(release.tag_name);
  const v = release.tag_name.replace(/^v/, "");
  const name =
    platform === "win32"
      ? `alshitasks_${v}_x64_setup.exe`
      : `alshitasks_${v}_amd64.deb`;
  const asset = (n) => {
    const rows = release.assets?.filter((a) => a.name === n) || [];
    if (
      rows.length !== 1 ||
      rows[0].browser_download_url !==
        `https://github.com/${REPO}/releases/download/${release.tag_name}/${n}`
    )
      throw new Error(
        "The release is incomplete or has an unexpected download address.",
      );
    return rows[0].browser_download_url;
  };
  return { version: v, name, url: asset(name), sums: asset("SHA256SUMS") };
}
function checksum(text, name) {
  const rows = text
    .split(/\r?\n/)
    .map((line) => line.trim().split(/\s+/))
    .filter((p) => p.length === 2 && p[1].replace(/^\*/, "") === name);
  if (rows.length !== 1 || !/^[a-f\d]{64}$/i.test(rows[0][0]))
    throw new Error("The release checksum is missing or invalid.");
  return rows[0][0].toLowerCase();
}
async function digest(file) {
  const hash = crypto.createHash("sha256");
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest("hex");
}
function atomic(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const tmp = file + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(data), { mode: 0o600 });
  fs.renameSync(tmp, file);
}
async function response(url, timeout = 30000) {
  const result = await fetch(url, {
    headers: {
      "User-Agent": "Alshival-Desktop-Updater",
      Accept: "application/vnd.github+json",
    },
    signal: AbortSignal.timeout(timeout),
  });
  if (!result.ok) {
    const error = new Error(
      result.status === 403 || result.status === 429
        ? "Update checks are temporarily rate limited. Try again later."
        : `Update server returned ${result.status}.`,
    );
    const retry = result.headers.get("retry-after");
    const reset = result.headers.get("x-ratelimit-reset");
    error.retryAt = retry
      ? Number.isFinite(Number(retry))
        ? Date.now() + Number(retry) * 1000
        : Date.parse(retry)
      : reset
        ? Number(reset) * 1000
        : 0;
    throw error;
  }
  if (new URL(result.url).protocol !== "https:")
    throw new Error("An insecure download was refused.");
  return result;
}
async function text(url) {
  const r = await response(url);
  const chunks = [];
  let size = 0;
  for await (const chunk of r.body) {
    size += chunk.length;
    if (size > 1024 * 1024) throw new Error("Release metadata is too large.");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}
async function download(url, file, progress) {
  const r = await response(url, 30 * 60000),
    total = Number(r.headers.get("content-length"));
  let received = 0,
    last = 0;
  const handle = await fs.promises.open(file, "w", 0o600);
  try {
    for await (const chunk of r.body) {
      received += chunk.length;
      if (received > 1024 * 1024 * 1024)
        throw new Error("The update exceeds the download size limit.");
      await handle.writeFile(chunk);
      if (Date.now() - last > 250) {
        last = Date.now();
        progress(
          total ? Math.min(99, Math.floor((received * 100) / total)) : null,
        );
      }
    }
    await handle.sync();
  } finally {
    await handle.close();
  }
}
class Updates extends EventEmitter {
  constructor(options) {
    super();
    this.options = options;
    this.now = options.now || Date.now;
    this.file = path.join(options.directory, "state.json");
    try {
      this.saved = JSON.parse(fs.readFileSync(this.file, "utf8"));
    } catch {
      this.saved = {};
    }
    this.state = {
      phase: "idle",
      currentVersion: options.version,
      availableVersion: null,
      progress: null,
      error: "",
      enabled: this.saved.enabled !== false,
      lastChecked: this.saved.lastChecked || null,
      nextCheck: this.saved.nextCheck || null,
      managed: !!options.managed,
      supported: options.supported,
    };
    if (!options.supported) this.state.phase = "unsupported";
    this.pending = null;
    this.operation = null;
    this.timer = null;
  }
  snapshot() {
    return { ...this.state };
  }
  publish(change) {
    Object.assign(this.state, change);
    this.emit("change", this.snapshot());
  }
  persist() {
    atomic(this.file, this.saved);
  }
  async start() {
    if (this.saved.pending && this.state.supported && !this.state.managed) {
      try {
        const p = this.saved.pending;
        if (!newer(p.version, this.options.version))
          throw new Error("Already installed");
        const expected = select(
          {
            tag_name: "v" + p.version,
            assets: [p.name, "SHA256SUMS"].map((name) => ({
              name,
              browser_download_url: `https://github.com/${REPO}/releases/download/v${p.version}/${name}`,
            })),
          },
          this.options.platform,
          this.options.arch,
        );
        if (
          p.name !== expected.name ||
          !/^[a-f\d]{64}$/.test(p.sha256) ||
          (await digest(path.join(this.options.directory, p.name))) !== p.sha256
        )
          throw new Error("Invalid cache");
        this.pending = { ...expected, sha256: p.sha256 };
        this.publish({
          phase: "ready",
          availableVersion: p.version,
          progress: 100,
        });
      } catch {
        delete this.saved.pending;
        this.persist();
      }
    }
    if (this.state.managed) await this.managedStatus();
    this.timer = setInterval(() => void this.tick(), 60000);
    this.timer.unref?.();
    this.launchTimer = setTimeout(() => void this.tick(), 15000);
    this.launchTimer.unref?.();
  }
  stop() {
    clearInterval(this.timer);
    clearTimeout(this.launchTimer);
  }
  async managedStatus() {
    const status = await this.options.managed.status();
    this.publish({ ...status, managed: true });
    return this.snapshot();
  }
  async tick() {
    if (this.operation) return;
    if (this.state.managed) {
      try {
        await this.managedStatus();
      } catch {}
      return;
    }
    if (
      this.state.supported &&
      this.state.enabled &&
      !this.operation &&
      this.now() >= (this.saved.nextCheck || 0)
    )
      await this.check(false);
  }
  setEnabled(enabled) {
    if (typeof enabled !== "boolean")
      throw new Error("Invalid update preference.");
    if (this.state.managed)
      throw new Error("Automatic updates are managed on this computer.");
    this.saved.enabled = enabled;
    this.persist();
    this.publish({ enabled });
    return this.snapshot();
  }
  check(manual = true) {
    if (this.operation) return this.operation;
    if (this.state.phase === "installing")
      return Promise.resolve(this.snapshot());
    if (!this.state.supported) return Promise.resolve(this.snapshot());
    this.operation = this.performCheck(manual).finally(() => {
      this.operation = null;
    });
    return this.operation;
  }
  async performCheck(manual) {
    this.publish({ phase: "checking", error: "", progress: null });
    try {
      if (this.state.managed) {
        await this.options.managed.check();
        return await this.managedStatus();
      }
      const release = this.options.release
        ? await this.options.release()
        : JSON.parse(
            await text(`https://api.github.com/repos/${REPO}/releases/latest`),
          );
      const selected = select(
        release,
        this.options.platform,
        this.options.arch,
      );
      const checked = this.now();
      this.saved.lastChecked = checked;
      this.saved.nextCheck = checked + WEEK;
      this.saved.failures = 0;
      this.publish({ lastChecked: checked, nextCheck: checked + WEEK });
      if (!newer(selected.version, this.options.version)) {
        this.pending = null;
        delete this.saved.pending;
        this.persist();
        this.publish({ phase: "current", availableVersion: null });
        return this.snapshot();
      }
      const sums = await (this.options.text || text)(selected.sums);
      const expected = checksum(sums, selected.name);
      const destination = path.join(this.options.directory, selected.name);
      this.publish({
        phase: "downloading",
        availableVersion: selected.version,
      });
      fs.mkdirSync(this.options.directory, { recursive: true, mode: 0o700 });
      let valid = false;
      try {
        valid = (await digest(destination)) === expected;
      } catch {}
      if (!valid) {
        const temporary = destination + ".part";
        try {
          await (this.options.download || download)(
            selected.url,
            temporary,
            (progress) => this.publish({ progress }),
          );
          if ((await digest(temporary)) !== expected)
            throw new Error(
              "The update checksum did not match. Please retry the download.",
            );
          fs.renameSync(temporary, destination);
        } finally {
          fs.rmSync(temporary, { force: true });
        }
      }
      this.pending = { ...selected, sha256: expected };
      this.saved.pending = {
        version: selected.version,
        name: selected.name,
        sha256: expected,
      };
      this.persist();
      this.publish({ phase: "ready", progress: 100, error: "" });
      // Keep only the currently verified package in the updater-owned cache.
      for (const name of fs.readdirSync(this.options.directory))
        if (
          /^alshitasks_[\d.]+_(amd64\.deb|x64_setup\.exe)(\.part)?$/.test(
            name,
          ) &&
          name !== selected.name
        )
          fs.rmSync(path.join(this.options.directory, name), { force: true });
    } catch (error) {
      this.saved.failures = (this.saved.failures || 0) + 1;
      const delays = [3600000, 21600000, 86400000];
      this.saved.nextCheck = Math.max(
        this.now() + delays[Math.min(2, this.saved.failures - 1)],
        error.retryAt || 0,
      );
      this.persist();
      this.publish({
        phase: this.pending ? "ready" : "error",
        error:
          error.message ||
          "Could not check for updates. Try again when connected.",
        nextCheck: this.saved.nextCheck,
      });
    }
    return this.snapshot();
  }
  install(relaunch) {
    if (typeof relaunch !== "boolean")
      return Promise.reject(new Error("Invalid restart request."));
    if (this.operation || this.state.phase !== "ready")
      return Promise.resolve(false);
    this.operation = (async () => {
      this.publish({ phase: "installing", error: "" });
      try {
        if (!this.state.managed) {
          if (
            !this.pending ||
            (await digest(
              path.join(this.options.directory, this.pending.name),
            )) !== this.pending.sha256
          )
            throw new Error(
              "The downloaded update changed. Check for updates again.",
            );
        }
        const result = await this.options.install(this.pending, relaunch);
        if (!result) {
          this.publish({ phase: "ready" });
          return false;
        }
        return true;
      } catch (error) {
        this.publish({
          phase: "ready",
          error: error.message || "Could not install the update.",
        });
        return false;
      }
    })().finally(() => {
      this.operation = null;
    });
    return this.operation;
  }
}
module.exports = { Updates, select, newer, checksum, digest, WEEK };
