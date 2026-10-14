import { _electron as electron, expect } from "playwright/test";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { spawn, execFileSync } from "node:child_process";
import { once } from "node:events";
import path from "node:path";
import os from "node:os";
import assert from "node:assert/strict";

// Run under dbus-run-session so the fixture never replaces a desktop's tray host.
assert.equal(process.env.ALSHITASKS_ISOLATED_BUS, "1");
const root = await mkdtemp(path.join(os.tmpdir(), "alshitasks-tray-"));
const appPath = process.env.ALSHITASKS_TEST_APP || path.resolve(".");
const executablePath = "/usr/lib/alshitasks/electron/electron";
const env = { ...process.env, ALSHITASKS_TEST_ROOT: root };
const args = ["--ozone-platform=x11", "--disable-gpu", appPath];
let app, watcher;
async function launch(hidden = false) {
  app = await electron.launch({
    executablePath,
    args: [...args, ...(hidden ? ["--start-in-tray"] : [])],
    env,
    chromiumSandbox: true,
  });
  const page = await app.firstWindow();
  await page.waitForLoadState("domcontentloaded");
  await page.locator(".project-columns-adapter").waitFor({ state: "attached" });
  return page;
}
const visible = () =>
  app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].isVisible(),
  );
async function secondary(extra = []) {
  const child = spawn(executablePath, [...args, ...extra], {
    env,
    stdio: "ignore",
  });
  const [code] = await once(child, "exit");
  assert.equal(code, 0);
}
async function quit() {
  await app.close();
  app = null;
}

try {
  watcher = spawn(
    "/usr/bin/python3",
    [path.resolve("scripts/tray-host-fixture.py")],
    { stdio: ["ignore", "pipe", "inherit"] },
  );
  await once(watcher.stdout, "data");
  let page = await launch();
  await expect.poll(visible).toBe(true);
  const windowId = await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].getNativeWindowHandle().readUInt32LE(0),
  );
  const windowClass = execFileSync("xprop", ["-id", String(windowId), "WM_CLASS"], {
    encoding: "utf8",
  });
  assert.match(windowClass, /WM_CLASS\(STRING\) = "alshitasks", "alshitasks"/);

  await page.evaluate(() => {
    window.trayDraft = "preserved";
  });
  await page.evaluate(() => window.tasks.windowAction("close"));
  await expect.poll(visible).toBe(false);
  await secondary(["--start-in-tray"]);
  assert.equal(await visible(), false);
  await secondary();
  await expect.poll(visible).toBe(true);
  assert.equal(await page.evaluate(() => window.trayDraft), "preserved");
  assert.equal(app.windows().length, 1);
  await secondary(["--start-in-tray"]);
  assert.equal(await visible(), true);
  assert.deepEqual(await page.evaluate(() => window.tasks.getStartup()), {
    enabled: false,
  });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page
    .getByRole("button", { name: "Device settings", exact: true })
    .click();
  const login = page.getByRole("checkbox", {
    name: "Start at login minimized",
  });
  await expect(login).not.toBeChecked();
  await login.click();
  await expect(login).toBeChecked();
  assert.match(
    await readFile(
      path.join(root, "config/autostart/alshitasks.desktop"),
      "utf8",
    ),
    /--start-in-tray/,
  );
  await quit();
  page = await launch(true);
  assert.equal(await visible(), false);
  assert.deepEqual(await page.evaluate(() => window.tasks.getStartup()), {
    enabled: true,
  });
  await secondary(["--notification", "00000000-0000-4000-8000-000000000001"]);
  await expect.poll(visible).toBe(true);
  await page.evaluate(() => window.tasks.setStartup(false));
  const stopped = once(watcher, "exit");
  watcher.kill();
  await stopped;
  watcher = null;
  const exited = once(app.process(), "exit");
  await page.evaluate(() => window.tasks.windowAction("close")).catch(() => {});
  await exited;
  await Promise.race([
    app.close().catch(() => {}),
    new Promise((r) => setTimeout(r, 1000)),
  ]);
  app = null;
  page = await launch(true);
  await expect.poll(visible).toBe(true);
  await quit();
  console.log(
    "Tray lifecycle passed: hide, reopen, repeat launch, login startup, notification, quit and absent-host fallback.",
  );
} finally {
  watcher?.kill();
  if (app) {
    app.process().kill("SIGTERM");
    await app.close().catch(() => {});
  }
  try {
    execFileSync(
      "/usr/bin/python3",
      [path.join(appPath, "service/alshitasks-cli.py"), "--root", root, "stop"],
      { timeout: 5000, killSignal: "SIGKILL", stdio: "ignore" },
    );
  } catch {}
  await rm(root, { recursive: true, force: true });
}

// Electron can leave debug transport handles alive after all assertions and cleanup.
// Reaching this point means the test passed; thrown failures never reach this exit.
process.exit(0);
