const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const windows = process.platform === "win32";
const base = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "Alshival.Ai");
const testRoot = process.env.ALSHITASKS_TEST_ROOT;
const runtime = testRoot ? path.join(testRoot, "runtime") : windows
  ? path.join(base, "runtime")
  : path.join(process.env.XDG_RUNTIME_DIR || `/run/user/${process.getuid()}`, "alshitasks");
const python = windows
  ? (fs.existsSync(path.join(process.resourcesPath || "", "python", "python.exe"))
    ? path.join(process.resourcesPath, "python", "python.exe") : "python")
  : "/usr/bin/python3";
function connection() {
  if (!windows) return { socketPath: path.join(runtime, "service.sock"), headers: {} };
  const endpoint = JSON.parse(fs.readFileSync(path.join(runtime, "service.json"), "utf8"));
  if (!Number.isInteger(endpoint.port) || endpoint.port < 1 || endpoint.port > 65535 || typeof endpoint.token !== "string")
    throw new Error("Invalid local service endpoint");
  return { hostname: "127.0.0.1", port: endpoint.port, headers: { "X-Alshival-Local": endpoint.token } };
}
module.exports = { windows, base, runtime, python, connection };
