import { chromium } from "playwright";
import assert from "node:assert/strict";
const browser = await chromium.launch({
  executablePath: process.env.BROWSER_PATH,
});
const page = await browser.newPage();
page.setDefaultTimeout(8000);
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.addInitScript(() => {
  const changed = new Set();
  let title = "Before portal edit",
    revision = 1;
  window.simulateDownloadedEdit = (next = "Changed in portal") => {
    title = next;
    revision++;
    changed.forEach((fn) => fn());
  };
  window.tasks = {
    request: async (path) => {
      const p = new URL(path, "http://fixture").pathname;
      if (p === "/v1/workspaces")
        return {
          workspaces: [
            {
              id: "workspace",
              name: "Live fixture",
              state: "connected",
              scopes: ["project:read", "project:write"],
              sync: "up-to-date",
              live: { state: "live" },
            },
          ],
        };
      if (p === "/v1/portal/account")
        return {
          account: {
            id: "fixture",
            name: "Fixture",
            origin: "https://alshival.ai",
          },
          status: { state: "connected" },
          navigation: [],
        };
      if (p === "/v1/notifications")
        return { items: [], next_page: null, unread_count: 0, portal: {} };
      if (p.endsWith("/statuses"))
        return {
          can_manage: true,
          statuses: [
            {
              id: "todo",
              name: "To do",
              category: "todo",
              legacy_state: "todo",
              position: 0,
              color: "#784bff",
              is_default: true,
              retired: false,
            },
          ],
        };
      if (p.endsWith("/items"))
        return {
          next: null,
          items: [
            {
              id: "task",
              kind: "task",
              revision,
              author_id: "fixture",
              created_at: "2026-10-11T00:00:00Z",
              updated_at: "2026-10-11T00:00:00Z",
              data: {
                title,
                body: "",
                status_id: "todo",
                state: "todo",
                visibility: "shared",
                priority: "normal",
                acceptance: "",
                assignee: null,
                reviewer: null,
                starts_on: null,
                due_on: null,
                board_id: null,
                parent_id: null,
                archived: false,
                deleted: false,
                color: "yellow",
                checklist: [],
                details: {},
              },
            },
          ],
        };
      return {
        attachments: [],
        events: [],
        items: [],
        boards: [],
        edges: [],
        members: [],
        keys: [],
        drafts: [],
        conflicts: [],
        publications: [],
        next: null,
      };
    },
    onChanged: (fn) => {
      changed.add(fn);
      return () => changed.delete(fn);
    },
    onWindowState: () => () => {},
    onNotification: () => () => {},
    initialNotification: async () => null,
    windowAction: async () => false,
    external: async () => {},
    theme: async () => {},
    connect: async () => {},
  };
});
try {
  await page.goto(process.env.UI_BASE_URL || "http://127.0.0.1:8383");
  await page
    .getByRole("button", { name: "Open workspace", exact: false })
    .click();
  await page.getByText("Before portal edit", { exact: true }).waitFor();
  const started = Date.now();
  await page.evaluate(() => window.simulateDownloadedEdit());
  await page
    .getByText("Changed in portal", { exact: true })
    .waitFor({ timeout: 5000 });
  assert.equal(
    await page.getByText("Before portal edit", { exact: true }).count(),
    0,
  );
  await page
    .getByRole("button", { name: "Changed in portal", exact: true })
    .click();
  await page.getByLabel("Title", { exact: true }).fill("Unsaved local draft");
  await page.evaluate(() =>
    window.simulateDownloadedEdit("Another portal edit"),
  );
  await page
    .getByRole("button", { name: "Load current version", exact: true })
    .waitFor();
  assert.equal(
    await page.getByLabel("Title", { exact: true }).inputValue(),
    "Unsaved local draft",
  );
  assert.deepEqual(errors, []);
  console.log(
    `Live refresh and unsaved draft preservation passed in ${Date.now() - started}ms.`,
  );
} catch (error) {
  await page.screenshot({ path: "/tmp/live-sync-ui-error.png" });
  console.log({ errors });
  throw error;
} finally {
  await browser.close();
}
