import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
const artifacts =
  process.env.UI_ARTIFACTS || join(tmpdir(), "alshitasks-workspace-ui");
mkdirSync(artifacts, { recursive: true });
const browser = await chromium.launch({
  ...(process.env.BROWSER_PATH
    ? { executablePath: process.env.BROWSER_PATH }
    : {}),
  headless: true,
});
const page = await browser.newPage({
  viewport: { width: 1440, height: 1000 },
  reducedMotion: "reduce",
});
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.addInitScript(() => {
  const rows = [
    {
      id: "00000000-0000-4000-8000-000000000001",
      name: "Local workspace",
      local: true,
      state: "local",
    },
    ...Array.from({ length: 1000 }, (_, i) => ({
      id: "w" + i,
      name: i === 0 ? "Tracegrade" : `Workspace ${String(i).padStart(4, "0")}`,
      state: "connected",
      kind: i % 2 ? "customer" : "internal",
      sync: "up-to-date",
      settings_url: "/workspace/w" + i + "/settings/",
      portal_origin: "https://alshival.ai",
      scopes: ["project:read", "project:write"],
    })),
  ];
  const boards = [
    {
      id: "board-one",
      revision: 1,
      kind: "board",
      data: { title: "Delivery" },
    },
    {
      id: "board-two",
      revision: 1,
      kind: "board",
      data: { title: "Planning" },
    },
  ];
  window.calls = [];
  window.externalLinks = [];
  window.tasks = {
    request: async (path, method = "GET", body) => {
      window.calls.push(path);
      const url = new URL(path, "http://fixture"),
        p = url.pathname;
      if (p.endsWith("/boards")) return { boards: structuredClone(boards) };
      if (p.endsWith("/items") && method === "POST") {
        const item = {
          id: "created-board",
          revision: 1,
          kind: body.kind,
          data: body.data,
        };
        boards.push(item);
        return { item };
      }
      if (p.includes("/items/") && method === "PATCH") {
        const item = boards.find((b) => b.id === p.split("/").pop());
        if (item.revision !== body.revision) throw Error("Stale revision");
        Object.assign(item.data, body.data);
        item.revision++;
        return { item };
      }
      if (p === "/v1/workspaces") return { workspaces: rows };
      if (p === "/v1/portal/account")
        return {
          account: {
            id: "fixture",
            name: "Fixture",
            origin: "https://alshival.ai",
          },
          managed: true,
          status: { state: "connected" },
          navigation: [],
        };
      if (p === "/v1/notifications")
        return { items: [], next_page: null, unread_count: 0, portal: {} };
      if (p.endsWith("/statuses"))
        return {
          can_manage: true,
          statuses: ["todo", "doing", "done"].map((s, i) => ({
            id: s,
            key: s,
            name: s,
            position: i,
            color: "#8058dd",
          })),
        };
      return {
        items: [],
        next: null,
        boards: [],
        edges: [],
        members: [],
        keys: [],
        drafts: [],
        conflicts: [],
        publications: [],
      };
    },
    onChanged: () => () => {},
    onWindowState: () => () => {},
    onNotification: () => () => {},
    initialNotification: async () => null,
    windowAction: async () => false,
    external: async (url) => window.externalLinks.push(url),
    theme: async () => {},
    connect: async () => {},
  };
});
try {
  await page.goto(process.env.UI_BASE_URL || "http://127.0.0.1:8383");
  await page
    .getByRole("heading", { name: "Workspaces", exact: true })
    .waitFor();
  assert.equal(await page.locator(".wd-card").count(), 30);
  assert.ok(
    !(await page.evaluate(() => window.calls)).some(
      (p) => p.includes("/items") && p.includes("workspace_id=w"),
    ),
  );
  await page.screenshot({ path: join(artifacts, "workspaces-desktop.png") });
  await page.locator(".workspace-toggle").click();
  await page.locator("#workspace-options").waitFor();
  await page.mouse.click(1250, 200);
  await page.locator("#workspace-options").waitFor({ state: "hidden" });
  await page.locator(".workspace-toggle").click();
  await page.keyboard.press("Escape");
  assert.equal(
    await page.locator(".workspace-toggle").getAttribute("aria-expanded"),
    "false",
  );
  await page.locator(".wd-directory input[type=search]").fill("Tracegrade");
  assert.equal(await page.locator(".wd-card").count(), 1);
  await page.locator(".wd-card summary").click();
  await page
    .getByRole("button", { name: "Manage boards", exact: true })
    .click();
  const manager = page.getByRole("region", {
    name: "Manage boards in Tracegrade",
  });
  await manager.getByLabel("Board name", { exact: true }).fill("Release");
  await manager
    .getByRole("button", { name: "Create board", exact: true })
    .click();
  await manager
    .getByLabel("Name for Release", { exact: true })
    .fill("Release checks");
  const form = manager
    .locator("form")
    .filter({ has: page.getByLabel("Name for Release", { exact: true }) });
  await form.getByRole("button", { name: "Rename", exact: true }).click();
  const renamed = manager.locator("form").filter({
    has: page.getByLabel("Name for Release checks", { exact: true }),
  });
  await renamed
    .getByRole("button", { name: "Archive board", exact: true })
    .click();
  await renamed
    .getByRole("button", { name: "Restore board", exact: true })
    .click();
  await page
    .getByRole("navigation", { name: "Boards in Tracegrade" })
    .getByRole("button", { name: "Planning", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Tracegrade", exact: true, level: 1 })
    .waitFor();
  const boardNav = page.getByRole("navigation", {
    name: "Current workspace boards",
  });
  await page.waitForFunction(() =>
    document
      .querySelector('.wd-board-navigation [aria-current="page"]')
      ?.textContent.includes("Planning"),
  );
  await boardNav.getByRole("button", { name: "Delivery", exact: true }).click();
  await page.waitForFunction(() =>
    document
      .querySelector('.wd-board-navigation [aria-current="page"]')
      ?.textContent.includes("Delivery"),
  );
  assert.equal(
    await page.getByRole("button", { name: "Open board", exact: true }).count(),
    0,
  );
  assert.equal(
    await page
      .getByRole("button", { name: "Manage boards", exact: true })
      .count(),
    0,
  );
  assert.equal(
    await page.getByLabel("Project board", { exact: true }).count(),
    0,
  );
  await page
    .getByRole("button", { name: "Settings for Tracegrade", exact: true })
    .click();
  assert.equal((await page.evaluate(() => window.externalLinks)).length, 1);
  await page.screenshot({
    path: join(artifacts, "workspace-board-desktop.png"),
  });
  await page.getByRole("button", { name: "Account", exact: true }).click();
  await page.getByRole("button", { name: "Startup preferences" }).click();
  await page.locator(".wd-startup select").selectOption("last");
  await page.reload();
  await page
    .getByRole("heading", { name: "Tracegrade", exact: true, level: 1 })
    .waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: join(artifacts, "workspace-mobile-desktop.png"),
  });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.screenshot({ path: join(artifacts, "workspace-dark.png") });
  assert.deepEqual(errors, []);
  console.log(
    "1000-workspace pagination, search, dismissal, title, settings destination and startup restoration passed.",
  );
} finally {
  await browser.close();
}
