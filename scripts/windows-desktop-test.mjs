import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

const appPath = path.resolve(process.env.ALSHITASKS_TEST_APP || "build/windows/win-unpacked");
const root = await fs.mkdtemp(path.join(os.tmpdir(), "alshitasks-windows-ui-"));
let app;
try {
  app = await electron.launch({ executablePath: path.join(appPath, "Alshival.Ai.exe"),
    env: { ...process.env, ALSHITASKS_TEST_ROOT: root }, timeout: 30000 });
  const page = await app.firstWindow();
  await page.waitForURL("alshitasks://app/index.html");
  await page.waitForFunction(() => Boolean(window.tasks));
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const status = await page.evaluate(() => window.tasks.service("status"));
  assert.equal(status.running, true);
  assert.equal(status.platform, "win32");
  const created = await page.evaluate(() => window.tasks.request("/v1/mcp", "POST", {
    name: "project_create", arguments: { kind: "task", data: { title: "Windows persistence fixture" }, idempotency_key: crypto.randomUUID() },
  }));
  const cli = (...args) => JSON.parse(execFileSync(path.join(appPath, "resources/python/python.exe"),
    [path.join(appPath, "resources/app/service/alshitasks-cli.py"), "--root", root, ...args], { encoding: "utf8", windowsHide: true }));
  assert.equal(cli("status").paused, false);
  cli("pause"); assert.equal(cli("status").paused, true);
  cli("resume"); assert.equal(cli("status").paused, false);
  await page.evaluate(() => window.tasks.service("restart"));
  assert.equal((await page.evaluate(() => window.tasks.service("status"))).running, true);
  const reopened = await page.evaluate(id => window.tasks.request("/v1/mcp", "POST", {
    name: "project_get", arguments: { id },
  }), created.id);
  assert.equal(reopened.data.title, "Windows persistence fixture");
  const startup = await page.evaluate(() => window.tasks.getStartup());
  try {
    assert.equal((await page.evaluate(() => window.tasks.setStartup(true))).enabled, true);
    assert.equal((await page.evaluate(() => window.tasks.setStartup(false))).enabled, false);
  } finally { await page.evaluate(enabled => window.tasks.setStartup(enabled), startup.enabled); }
  await page.screenshot({ path: "build/windows-desktop.png" });
  assert.deepEqual(errors, []);
  await page.evaluate(() => window.tasks.service("stop"));
  console.log("Packaged Windows desktop: launch, authenticated service, pause/resume, restart, and stop passed.");
} finally {
  await app?.close();
  // All paths here belong to the fixture root created above.
  if (path.dirname(root) !== os.tmpdir()) throw new Error("Unexpected fixture location");
  await fs.rm(root, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 });
}
