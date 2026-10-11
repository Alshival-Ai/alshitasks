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
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("dialog", async (dialog) => {
    console.log("Fixture dialog:", dialog.message());
    await dialog.accept();
  });
  page.on("pageerror", (e) => errors.push(e.message));
  assert.equal(
    await app.evaluate(({ Menu }) => Menu.getApplicationMenu()),
    null,
  );
  await page.getByRole("radio", { name: "Dark", exact: true }).click();
  await page.waitForFunction(
    () => document.documentElement.dataset.theme === "dark",
  );
  assert.equal(
    await page
      .locator("body")
      .evaluate((el) => getComputedStyle(el).backgroundColor),
    "rgb(0, 0, 0)",
  );
  await page.reload();
  await page.getByRole("radio", { name: "Dark", exact: true }).waitFor();
  assert.equal(
    await page
      .getByRole("radio", { name: "Dark", exact: true })
      .getAttribute("aria-checked"),
    "true",
  );
  await page.screenshot({ path: "/tmp/alshitasks-chrome-dark.png" });
  await page.getByRole("radio", { name: "Light", exact: true }).click();
  assert.equal(
    await page
      .locator("body")
      .evaluate((el) => getComputedStyle(el).backgroundColor),
    "rgb(246, 244, 239)",
  );
  await page.screenshot({ path: "/tmp/alshitasks-chrome-light.png" });
  await page.getByRole("radio", { name: "System", exact: true }).click();
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForFunction(
    () => document.documentElement.dataset.theme === "dark",
  );
  await page.getByRole("radio", { name: "Light", exact: true }).click();
  assert.equal(
    await page
      .locator(".projects-app")
      .evaluate((el) =>
        getComputedStyle(el).getPropertyValue("--bright").trim(),
      ),
    "#fffef9",
  );
  assert.equal(
    await page
      .locator(".app-header")
      .evaluate((el) =>
        getComputedStyle(el).getPropertyValue("-webkit-app-region"),
      ),
    "drag",
  );
  assert.equal(
    await page
      .getByRole("button", { name: "Close window", exact: true })
      .evaluate((el) =>
        getComputedStyle(el).getPropertyValue("-webkit-app-region"),
      ),
    "no-drag",
  );
  await page
    .getByRole("button", { name: "Maximize window", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Restore window", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Restore window", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Maximize window", exact: true })
    .waitFor();
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
    animations: "disabled",
  });
  const nav = page.getByRole("navigation", { name: "Application" });
  assert.deepEqual(await nav.getByRole("link").allTextContents(), [
    "Projects",
    "Settings",
  ]);
  await page.getByRole("button", { name: "Expand menu", exact: true }).click();
  await page
    .getByRole("button", { name: "Collapse menu", exact: true })
    .waitFor();
  await page.screenshot({
    animations: "disabled",
    path: "/tmp/alshitasks-sidebar-expanded.png",
  });
  await page.reload();
  await page
    .getByRole("button", { name: "Collapse menu", exact: true })
    .waitFor();
  await nav.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("heading", { name: "Settings", exact: true }).waitFor();
  await page
    .getByRole("heading", { name: "Alshival.Ai account", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Sign in with Alshival.Ai", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Collapse menu", exact: true })
    .click();
  await page.reload();
  await page
    .getByRole("button", { name: "Expand menu", exact: true })
    .waitFor();
  await page.setViewportSize({ width: 760, height: 700 });
  await nav.getByRole("link", { name: "Settings", exact: true }).click();
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  await page.getByRole("button", { name: "Expand menu", exact: true }).click();
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  await page.setViewportSize({ width: 1380, height: 900 });
  await page.screenshot({
    path: "/tmp/alshitasks-desktop-settings.png",
    fullPage: true,
    animations: "disabled",
  });
  execFileSync("/usr/bin/python3", [
    "-c",
    `
import sys,json
sys.path.insert(0,sys.argv[1])
from alshitasks.store import Store,uid
from alshitasks.paths import Paths
s=Store(Paths(sys.argv[2]))
with s.tx():
 s.set_setting('last_account',{'id':'fixture','origin':'https://alshival.ai'})
 for name in ('Workspace A','Workspace B'):
  c=uid()
  s.db.execute('INSERT INTO connections VALUES(?,?,?,?,?,?,?)',(c,'https://alshival.ai','fixture',uid(),'disconnected',None,'{}'))
  s.set_setting('workspace:'+c,{'name':name,'scopes':['project:read']})
  board=s.create({'kind':'board','data':{'title':name+' board'},'idempotency_key':uid()})
  task=s.create({'kind':'task','data':{'title':name+' task','board_id':board['id'],'status_id':next(x['id'] for x in s.statuses() if x.get('board_id')==board['id'] and x['legacy_state']=='todo')},'idempotency_key':uid()})
  s.db.execute('UPDATE items SET connection=? WHERE id IN (?,?)',(c,board['id'],task['id']))
s.db.close()
`,
    path.join(process.env.ALSHITASKS_TEST_APP || path.resolve("."), "service"),
    root,
  ]);
  await page.reload();
  await page.locator(".workspace-toggle").click();
  await page
    .getByRole("group", { name: "Workspaces", exact: true })
    .getByRole("button", { name: /Workspace A/ })
    .click();
  await nav.getByRole("link", { name: "Projects", exact: true }).click();
  await page.getByText("Workspace A task", { exact: true }).first().waitFor();
  assert.equal(
    await page.getByText("Workspace B task", { exact: true }).count(),
    0,
  );
  await page.locator(".workspace-toggle").click();
  await page
    .getByRole("group", { name: "Workspaces", exact: true })
    .getByRole("button", { name: /Workspace B/ })
    .click();
  await page.getByText("Workspace B task", { exact: true }).first().waitFor();
  assert.equal(
    await page.getByText("Workspace A task", { exact: true }).count(),
    0,
  );
  await page.reload();
  await page.getByText("Workspace B task", { exact: true }).first().waitFor();
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
