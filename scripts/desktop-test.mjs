import { _electron as electron } from "playwright";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import { execFileSync } from "node:child_process";
import path from "node:path";
import assert from "node:assert/strict";
const root = await mkdtemp(path.join(os.tmpdir(), "alshitasks-desktop-"));
let app;
try {
  app = await electron.launch({
    executablePath: "/usr/lib/alshitasks/electron/electron",
    chromiumSandbox: true,
    args: [
      "--ozone-platform=" + (process.env.ALSHITASKS_TEST_DISPLAY || "x11"),
      process.env.ALSHITASKS_TEST_APP || path.resolve("."),
    ],
    env: { ...process.env, ALSHITASKS_TEST_ROOT: root },
    timeout: 30000,
  });
  const page = await app.firstWindow();
  const errors = [];
  page.on("dialog", async (dialog) => {
    console.log("Fixture dialog:", dialog.message());
    await dialog.accept();
  });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.getByRole("button", { name: "New task +", exact: true }).click();
  await page
    .getByLabel("Title", { exact: true })
    .fill("Offline acceptance task");
  await page
    .getByLabel("Description", { exact: true })
    .fill("Created through the packaged desktop without a portal account");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page
    .getByRole("heading", { name: "Offline acceptance task", exact: true })
    .first()
    .waitFor();
  const result = await page.evaluate(() =>
    window.tasks.request("/v1/projects/items"),
  );
  assert.equal(result.items.filter((x) => x.kind === "task").length, 1);
  assert.equal(result.items[0].data.title, "Offline acceptance task");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "New Note", exact: true }).click();
  await page
    .getByRole("textbox", { name: /^Note title:/ })
    .fill("Offline note");
  await page.getByLabel("Edit note text", { exact: true }).click();
  await page
    .getByRole("textbox", { name: /^Note text:/ })
    .fill("Persisted note content");
  await page.waitForFunction(async () => {
    const { items } = await window.tasks.request("/v1/projects/items");
    return items.some(
      (i) =>
        i.kind === "note" &&
        i.data.title === "Offline note" &&
        i.data.body === "Persisted note content",
    );
  });
  await page.getByRole("button", { name: "New Resource", exact: true }).click();
  await page.getByLabel("Title", { exact: true }).fill("Offline resource");
  await page
    .getByLabel("Description", { exact: true })
    .fill("Resource metadata fixture");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.waitForFunction(async () => {
    const { items } = await window.tasks.request("/v1/projects/items");
    return items.some(
      (i) => i.kind === "resource" && i.data.title === "Offline resource",
    );
  });
  await page.keyboard.press("Escape");
  for (const name of [
    "Graph",
    "Timeline",
    "Activity",
    "Deliverables",
    "Board",
  ]) {
    await page.getByRole("link", { name, exact: true }).click();
    assert.equal(
      await page.getByLabel("Project view", { exact: true }).inputValue(),
      name.toLowerCase(),
    );
  }
  await page.evaluate(async () => {
    const { items } = await window.tasks.request("/v1/projects/items");
    const task = items.find((i) => i.kind === "task");
    await window.tasks.request(
      "/v1/projects/items/" + task.id + "/actions",
      "POST",
      {
        action: "trash",
        revision: task.revision,
        idempotency_key: crypto.randomUUID(),
      },
    );
  });
  await page.getByRole("link", { name: "Trash", exact: true }).click();
  assert.equal(
    await page.getByLabel("Project view", { exact: true }).inputValue(),
    "trash",
  );
  await page.getByRole("button", { name: "Restore", exact: true }).click();
  await page.waitForFunction(async () => {
    const { items } = await window.tasks.request("/v1/projects/items");
    return items.some(
      (i) => i.kind === "task" && !i.data.deleted && i.revision === 3,
    );
  });
  await page.getByRole("link", { name: "Board", exact: true }).click();
  const preferences = await app.evaluate(({ BrowserWindow }) => {
    const p =
      BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences();
    return {
      sandbox: p.sandbox,
      nodeIntegration: p.nodeIntegration,
      contextIsolation: p.contextIsolation,
    };
  });
  assert.deepEqual(preferences, {
    sandbox: true,
    nodeIntegration: false,
    contextIsolation: true,
  });
  await page.screenshot({
    path: "/tmp/alshitasks-desktop-projects.png",
    fullPage: true,
  });
  for (const name of ["Resources", "Logs", "Connections", "Settings"]) {
    await page.getByRole("button", { name, exact: true }).click();
    await page.getByRole("heading", { level: 1 }).waitFor();
  }
  await page.screenshot({
    path: "/tmp/alshitasks-desktop-settings.png",
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  console.log(
    "Electron: offline task/note/resource editing, project views, Trash restore, all application tabs, sandbox preferences and rendering passed.",
  );
} catch (e) {
  console.error(e);
  if (app) {
    for (const page of app.windows()) {
      console.error(
        "Window:",
        page.url(),
        (
          await page
            .locator("body")
            .innerText({ timeout: 2000 })
            .catch(() => "")
        ).slice(0, 1200),
      );
      await page
        .screenshot({ path: "/tmp/alshitasks-desktop-failure.png" })
        .catch(() => {});
    }
  }
  process.exitCode = 1;
} finally {
  try {
    execFileSync(
      "/usr/bin/python3",
      [path.resolve("service/alshitasks-cli.py"), "--root", root, "stop"],
      { timeout: 5000, stdio: "ignore" },
    );
  } catch {}
  if (app) {
    app.process().kill("SIGTERM");
    await Promise.race([
      app.close().catch(() => {}),
      new Promise((r) => setTimeout(r, 3000)),
    ]);
  }
  await rm(root, { recursive: true, force: true });
}
