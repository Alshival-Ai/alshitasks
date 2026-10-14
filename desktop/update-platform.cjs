const fs = require("node:fs");
const path = require("node:path");
const { execFile, spawn } = require("node:child_process");
const { Updates } = require("./updates.cjs");
const helper = "/usr/lib/alshitasks/desktop/update-install";
const sharedStatus = "/var/lib/alshitasks-updates/status.json";
function command(executable, args, options = {}) {
  return new Promise((resolve, reject) =>
    execFile(
      executable,
      args,
      {
        timeout: 35 * 60000,
        maxBuffer: 1024 * 1024,
        windowsHide: true,
        ...options,
      },
      (error, stdout) => {
        let result;
        try {
          result = JSON.parse(stdout);
        } catch {}
        if (error)
          reject(
            new Error(
              result?.error ||
                (error.code === 126 || error.code === 127
                  ? "Update authorization was canceled or unavailable. The download is saved for later."
                  : "The update could not be installed. Please try again."),
            ),
          );
        else resolve(result);
      },
    ),
  );
}
function createUpdates({ app, root, testRoot, stopService, startService }) {
  const managed =
    !testRoot &&
    process.platform === "linux" &&
    fs.existsSync("/etc/alshitasks-managed-updates");
  const supported =
    !testRoot &&
    process.arch === "x64" &&
    (process.platform === "win32"
      ? app.isPackaged
      : process.platform === "linux" && root === "/usr/lib/alshitasks");
  const directory = path.join(app.getPath("userData"), "updates");
  const running = app.getVersion();
  function status() {
    let value = {};
    try {
      value = JSON.parse(fs.readFileSync(sharedStatus, "utf8"));
    } catch {}
    let disk = running;
    try {
      disk = JSON.parse(
        fs.readFileSync(path.join(root, "package.json"), "utf8"),
      ).version;
    } catch {}
    if (disk !== running)
      return {
        ...value,
        phase: "ready",
        availableVersion: disk,
        installedVersion: disk,
        error: "",
      };
    return { phase: "idle", enabled: true, ...value };
  }
  return new Updates({
    directory,
    version: running,
    platform: process.platform,
    arch: process.arch,
    supported,
    managed: managed
      ? {
          status,
          check: () =>
            command("/usr/bin/pkexec", [
              "/usr/lib/alshitasks/desktop/update-check",
            ]),
        }
      : null,
    install: async (pending, relaunch) => {
      if (
        managed &&
        status().installedVersion === status().availableVersion &&
        status().availableVersion !== running
      )
        return true;
      if (process.platform === "win32") {
        await stopService();
        try {
          const args = [
            "/S",
            "--updated",
            ...(relaunch ? ["--force-run"] : []),
          ];
          await new Promise((resolve, reject) => {
            const child = spawn(path.join(directory, pending.name), args, {
              detached: true,
              stdio: "ignore",
              windowsHide: true,
            });
            child.once("error", reject);
            child.once("spawn", () => {
              child.unref();
              resolve();
            });
          });
          return true;
        } catch (error) {
          await startService();
          throw error;
        }
      }
      // Authorization and other-user checks happen before the helper stops services.
      const result = await command("/usr/bin/pkexec", [
        helper,
        "--requester",
        String(process.pid),
        ...(managed
          ? []
          : [
              "--version",
              pending.version,
              "--package",
              path.join(directory, pending.name),
            ]),
      ]);
      if (!result?.installed)
        throw new Error(
          result?.error ||
            "The update is waiting for other desktop sessions to close.",
        );
      return true;
    },
  });
}
module.exports = { createUpdates };
