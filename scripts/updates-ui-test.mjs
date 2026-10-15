import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
const root = await fs.mkdtemp(path.join(os.tmpdir(), "alshitasks-updates-ui-"));
let app;
try {
  app = await electron.launch({
    executablePath:
      process.env.ALSHITASKS_TEST_EXE ||
      "/usr/lib/alshitasks/electron/electron",
    chromiumSandbox: true,
    args: [
      ...(process.platform === "win32" ? [] : ["--ozone-platform=x11"]),
      process.env.ALSHITASKS_TEST_APP || path.resolve("."),
    ],
    env: { ...process.env, ALSHITASKS_TEST_ROOT: root },
  });
  const page = await app.firstWindow();
  await page.waitForURL("alshitasks://app/index.html");
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.locator(".workspace-toggle").waitFor();
  for (const width of [1440, 900, 480]) {
    await app.evaluate(
      ({ BrowserWindow }, width) =>
        BrowserWindow.getAllWindows()[0].setSize(width, 850),
      width,
    );
    const geometry = await page.locator(".workspace-toggle").evaluate((el) => {
      const name = el
          .querySelector(".app-workspace-name")
          .getBoundingClientRect(),
        icon = el.querySelector("svg").getBoundingClientRect();
      return {
        offset: Math.abs(name.y + name.height / 2 - icon.y - icon.height / 2),
        gap: icon.x - name.right,
        icon: icon.width,
      };
    });
    assert(geometry.offset <= 1, JSON.stringify(geometry));
    assert(geometry.gap >= 5 && geometry.gap <= 7);
    assert.equal(geometry.icon, 18);
  }
  const guard = await page.evaluate(async () => {
    let called = false;
    window.addEventListener(
      "alshitasks:prepare-update",
      (event) => {
        called = true;
        event.preventDefault();
      },
      { once: true },
    );
    const installed = await window.tasks.installUpdate(true);
    return { called, installed };
  });
  assert.deepEqual(guard, { called: true, installed: false });
  await page.locator(".workspace-toggle").click();
  assert.equal(
    await page.locator(".workspace-toggle").getAttribute("aria-expanded"),
    "true",
  );
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page
    .getByRole("button", { name: "Check for Updates", exact: true })
    .click();
  await page.getByRole("heading", { name: "Updates", exact: true }).waitFor();
  assert.equal(
    (await page.evaluate(() => window.tasks.getUpdates())).phase,
    "unsupported",
    "Fixtures cannot update real packages",
  );
  const ready = {
    phase: "ready",
    currentVersion: "1.2.3",
    availableVersion: "1.2.4",
    progress: 100,
    error: "",
    enabled: true,
    lastChecked: Date.now(),
    nextCheck: Date.now() + 604800000,
    managed: false,
    supported: true,
  };
  await app.evaluate(
    ({ BrowserWindow }, state) =>
      BrowserWindow.getAllWindows()[0].webContents.send(
        "tasks:updates-changed",
        state,
      ),
    ready,
  );
  await page.getByLabel("Update ready").waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "Restart to update", exact: true })
      .count(),
    2,
  );
  await page.getByRole("button", { name: "Later", exact: true }).click();
  assert.equal(
    await page
      .getByRole("button", { name: "Restart to update", exact: true })
      .count(),
    1,
  );
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(1050, 850),
  );
  await page.waitForTimeout(400);
  await page.screenshot({
    path: path.join(os.tmpdir(), "alshitasks-updates-light.png"),
  });
  await page.evaluate(() =>
    document.documentElement.classList.add("dark-style"),
  );
  await page.screenshot({
    path: path.join(os.tmpdir(), "alshitasks-updates-dark.png"),
  });
  await app.evaluate(
    ({ BrowserWindow }, state) =>
      BrowserWindow.getAllWindows()[0].webContents.send(
        "tasks:updates-changed",
        { ...state, phase: "installing" },
      ),
    ready,
  );
  assert.equal(await page.locator("main").getAttribute("inert"), "");
  await page.locator(".update-installing").waitFor();
  assert.deepEqual(errors, []);
  console.log(
    "PASS: updater controls, later action, installation editing guard, light/dark UI, responsive centered switcher and keyboard dismissal",
  );
} finally {
  await app?.close();
  await fs.rm(root, {
    recursive: true,
    force: true,
    maxRetries: 10,
    retryDelay: 200,
  });
}
