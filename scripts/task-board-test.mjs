import { _electron as electron, expect } from "playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";

const root = await mkdtemp(path.join(os.tmpdir(), "alshitasks-board-"));
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
  });
  const page = await app.firstWindow();
  page.setDefaultTimeout(10000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator(".project-columns-adapter").waitFor();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const { task, statuses } = await page.evaluate(async () => {
    const { statuses } = await window.tasks.request("/v1/projects/statuses");
    const task = await window.tasks.request("/v1/projects/items", "POST", {
      idempotency_key: crypto.randomUUID(),
      kind: "task",
      data: {
        title: "Board interaction task",
        status_id: statuses.find((s) => s.is_default && !s.board_id).id,
      },
    });
    return {
      task,
      statuses: statuses
        .filter((s) => !s.retired)
        .sort((a, b) => a.position - b.position),
    };
  });
  await page.reload();
  const card = page
    .locator(".project-task")
    .filter({ hasText: "Board interaction task" });
  const column = (status) => page.locator(`[data-sp-state="${status.id}"]`);
  const state = () =>
    page.evaluate(
      (id) => window.tasks.request(`/v1/projects/items/${id}`),
      task.id,
    );
  const start = statuses.find((s) => s.id === task.data.status_id);
  const next = statuses.find((s) => s.id !== start.id && s.category !== "done");
  const drag = async (destination, cancel = false) => {
    await card.scrollIntoViewIfNeeded();
    const from = await card.boundingBox(),
      to = await destination.boundingBox();
    await page.mouse.move(from.x + 25, from.y + 18);
    await page.mouse.down();
    await page.mouse.move(from.x + 40, from.y + 25, { steps: 4 });
    await page.mouse.move(to.x + to.width / 2, to.y + 60, { steps: 12 });
    if (cancel) await page.keyboard.press("Escape");
    await page.mouse.up();
  };
  await drag(column(next));
  await expect(column(next).locator(".project-task")).toHaveCount(1);
  assert.equal((await state()).data.status_id, next.id);
  await page.reload();
  await expect(column(next).locator(".project-task")).toHaveCount(1);
  await drag(column(start), true);
  assert.equal((await state()).data.status_id, next.id);
  // The portal-style chooser works from the keyboard and returns focus.
  await page.waitForTimeout(450); // post-drag click suppression
  await card.getByRole("button", { name: "Move to…", exact: true }).focus();
  await page.keyboard.press("Enter");
  const menu = page.getByRole("dialog", {
    name: "Move Board interaction task",
    exact: true,
  });
  await expect(menu).toBeVisible();
  await expect(
    menu.getByRole("button", { name: next.name, exact: true }),
  ).toBeDisabled();
  await page.keyboard.press("End");
  await page.keyboard.press("Home");
  await page.keyboard.press("Escape");
  await expect(menu).not.toBeVisible();
  await expect(
    card.getByRole("button", { name: "Move to…", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await menu.getByRole("button", { name: start.name, exact: true }).click();
  await expect(column(start).locator(".project-task")).toHaveCount(1);

  // No-op and outside releases must not send mutations.
  const revision = (await state()).revision;
  await drag(column(start));
  await drag(page.locator(".app-header"));
  assert.equal((await state()).revision, revision);
  assert.equal(await page.locator(".sp-drag-layer").count(), 0);

  // A concurrent edit must be reported without moving the task on failure.
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setFocusEmulationEnabled", { enabled: true });
  await card.scrollIntoViewIfNeeded();
  let box = await card.boundingBox();
  await page.mouse.move(box.x + 25, box.y + 18);
  await page.mouse.down();
  await page.mouse.move(box.x + 45, box.y + 25, { steps: 4 });
  await page.evaluate(async (id) => {
    const item = await window.tasks.request(`/v1/projects/items/${id}`);
    await window.tasks.request(`/v1/projects/items/${id}`, "PATCH", {
      idempotency_key: crypto.randomUUID(),
      revision: item.revision,
      data: { body: "Concurrent edit retained" },
    });
  }, task.id);
  box = await column(next).boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + 60, { steps: 10 });
  await page.mouse.up();
  await expect(page.getByRole("alert")).toContainText("Record changed");
  assert.equal((await state()).data.status_id, start.id);
  assert.equal((await state()).data.body, "Concurrent edit retained");
  await page.getByRole("button", { name: "Reload board", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);

  // Custom statuses and legacy state-only tasks remain usable.
  await page.evaluate(async () => {
    await window.tasks.request("/v1/projects/statuses", "POST", {
      revision: 0,
      data: {
        name: "Quality check",
        category: "doing",
        position: 2,
        color: "purple",
      },
    });
    await window.tasks.request("/v1/projects/items", "POST", {
      idempotency_key: crypto.randomUUID(),
      kind: "task",
      data: { title: "Legacy state task", state: "doing" },
    });
  });
  await page.reload();
  const legacy = page
    .locator(".project-task")
    .filter({ hasText: "Legacy state task" });
  await expect(legacy).toBeVisible();
  await card.getByRole("button", { name: "Move to…", exact: true }).click();
  await menu
    .getByRole("button", { name: "Quality check", exact: true })
    .click();
  await expect(
    page.locator('[data-status-label="Quality check"] .project-task'),
  ).toHaveCount(1);
  await page.reload();
  await expect(
    page.locator('[data-status-label="Quality check"] .project-task'),
  ).toHaveCount(1);

  // Native touch events: long-press then release on the compact status strip.
  await page.setViewportSize({ width: 560, height: 900 });
  const board = page.locator(".project-columns-adapter");
  await expect(board).toHaveAttribute("data-board-layout", "swipe");
  const custom = (await state()).data.status_id;
  await page.locator(`[data-shared-status="${custom}"]`).click();
  await card.scrollIntoViewIfNeeded();
  // Keep the drop tab in view; the strip may have scrolled to reveal the custom status.
  await page
    .locator(".sp-mobile-filter")
    .evaluate((el) => el.scrollTo({ left: 0, behavior: "instant" }));
  box = await card.boundingBox();
  await cdp.send("Emulation.setTouchEmulationEnabled", {
    enabled: true,
    maxTouchPoints: 1,
  });
  const touch = async (type, x, y) =>
    cdp.send("Input.dispatchTouchEvent", {
      type,
      touchPoints:
        type === "touchEnd" || type === "touchCancel" ? [] : [{ x, y }],
    });
  await touch("touchStart", box.x + box.width / 2, box.y + 20);
  await expect(page.locator(".sp-drag-layer")).toHaveCount(1);
  const tab = page.locator(`[data-shared-status="${start.id}"]`);
  const tabBox = await tab.boundingBox();
  assert.ok(
    await tab.evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return (
        document
          .elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
          ?.closest("button") === el
      );
    }),
    "Touch drop target must be visible and hit-testable",
  );
  await touch(
    "touchMove",
    tabBox.x + tabBox.width / 2,
    tabBox.y + tabBox.height / 2,
  );
  await expect(tab).toHaveClass(/sp-drop-active/);
  await touch("touchEnd");
  await expect.poll(async () => (await state()).data.status_id).toBe(start.id);
  await expect(page.locator(".sp-drag-layer")).toHaveCount(0);
  await page.waitForTimeout(450);
  await tab.click();
  await card.scrollIntoViewIfNeeded();
  box = await card.boundingBox();
  await touch("touchStart", box.x + box.width / 2, box.y + 20);
  await expect(page.locator(".sp-drag-layer")).toHaveCount(1);
  await touch("touchCancel");
  await expect(page.locator(".sp-drag-layer")).toHaveCount(0);
  assert.equal((await state()).data.status_id, start.id);
  await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: false });
  await page.waitForTimeout(450);
  await page.getByRole("button", { name: "Stacked", exact: true }).click();
  await expect(board).toHaveAttribute("data-board-layout", "stacked");
  await page.reload();
  await expect(board).toHaveAttribute("data-board-layout", "stacked");
  assert.deepEqual(errors, []);
  console.log(
    "Task board: mouse and touch movement, keyboard chooser, custom/legacy statuses, persistence, cancellation and revision-conflict handling passed.",
  );
} catch (error) {
  if (app)
    await (
      await app.firstWindow()
    )
      .screenshot({ path: "/tmp/alshitasks-task-board-failure.png" })
      .catch(() => {});
  throw error;
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
