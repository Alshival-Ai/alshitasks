import { _electron as electron, expect } from "playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
const root = await mkdtemp(path.join(os.tmpdir(), "alshitasks-cards-"));
let app;
try {
  app = await electron.launch({
    executablePath:
      process.env.ALSHITASKS_TEST_EXE ||
      "/usr/lib/alshitasks/electron/electron",
    args: [
      "--ozone-platform=x11",
      process.env.ALSHITASKS_TEST_APP || path.resolve("."),
    ],
    env: { ...process.env, ALSHITASKS_TEST_ROOT: root },
  });
  const page = await app.firstWindow();
  page.setDefaultTimeout(15000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator(".project-columns-adapter").waitFor();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const fixtures = await page.evaluate(async () => {
    const create = (kind, data) =>
      window.tasks.request("/v1/projects/items", "POST", {
        kind,
        data,
        idempotency_key: crypto.randomUUID(),
      });
    const resource = await create("resource", {
      title: "Production API",
      body: "Keep this configuration",
      resource: {
        kind: "api",
        status: "active",
        provider: "Alshival",
        environment: "production",
        public_url: "https://example.com",
        external_id: null,
        tags: ["important"],
      },
    });
    const note = await create("note", {
      title: "Release notes",
      body: "Keep this text",
      color: "yellow",
    });
    const housed = await create("note", {
      title: "Resource instructions",
      body: "Keep this relationship",
      resource_id: resource.id,
    });
    const retired = await create("resource", {
      title: "Portal retired resource",
      resource: { kind: "other", status: "retired" },
    });
    return { resource, note, housed, retired };
  });
  await page.reload();
  const card = (item) => page.locator(`.sp-sticky[data-item-id="${item.id}"]`);
  const state = (item) =>
    page.evaluate(
      (id) => window.tasks.request(`/v1/projects/items/${id}`),
      item.id,
    );
  const toggle = page.getByRole("button", {
    name: "Deleted items",
    exact: true,
  });
  const dialog = page
    .getByRole("dialog")
    .filter({ has: page.locator("#card-lifecycle-title") });
  const remove = (item) =>
    card(item).getByRole("button", {
      name: `Delete ${item.kind}: ${item.data.title}`,
      exact: true,
    });
  await expect(card(fixtures.retired)).toHaveCount(0);
  await expect(card(fixtures.housed)).toHaveCount(0);
  await remove(fixtures.note).click();
  await expect(
    dialog.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeFocused();
  await page.screenshot({ path: "/tmp/alshitasks-card-confirm-light.png" });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(remove(fixtures.note)).toBeFocused();
  assert.equal((await state(fixtures.note)).data.archived, false);
  // Pause renderer timers so autosave cannot race the draft-protection assertion.
  await page.clock.install();
  await page.clock.pauseAt(new Date());
  // Dirty note contents must survive an attempted deletion before autosave.
  await card(fixtures.note)
    .getByLabel("Note title: Release notes", { exact: true })
    .fill("Unsaved title");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await remove(fixtures.note).click();
  await dialog
    .getByRole("button", { name: "Delete note", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText("Save your changes");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.clock.runFor(1000);
  await page.clock.resume();
  await expect
    .poll(async () => (await state(fixtures.note)).data.title)
    .toBe("Unsaved title");
  fixtures.note.data.title = "Unsaved title";
  await remove(fixtures.note).click();
  await dialog
    .getByRole("button", { name: "Delete note", exact: true })
    .click();
  await expect(card(fixtures.note)).toHaveCount(0);
  assert.equal((await state(fixtures.note)).data.body, "Keep this text");
  await toggle.click();
  await expect(card(fixtures.retired)).toBeVisible();
  await card(fixtures.note)
    .getByRole("button", { name: "Restore note: Unsaved title", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "Restore note", exact: true })
    .click();
  await expect(card(fixtures.note)).toHaveCount(0);
  await toggle.click();
  await expect(card(fixtures.note)).toBeVisible();
  // Expanded cards use the same confirmation; deleting a resource reveals its housed note.
  await card(fixtures.resource)
    .getByRole("button", { name: "Production API", exact: true })
    .click();
  await page
    .locator(".connected-paper")
    .getByRole("button", {
      name: "Delete resource: Production API",
      exact: true,
    })
    .click();
  await dialog
    .getByRole("button", { name: "Delete resource", exact: true })
    .click();
  await expect(page.locator(".connected-paper")).toHaveCount(0);
  await expect(card(fixtures.housed)).toBeVisible();
  assert.equal(
    (await state(fixtures.housed)).data.resource_id,
    fixtures.resource.id,
  );
  assert.equal(
    (await state(fixtures.resource)).data.resource.status,
    "retired",
  );
  await toggle.click();
  await page.getByRole("radio", { name: "Dark", exact: true }).click();
  await card(fixtures.resource)
    .getByRole("button", {
      name: "Restore resource: Production API",
      exact: true,
    })
    .click();
  await page.screenshot({ path: "/tmp/alshitasks-card-confirm-dark.png" });
  assert.equal(
    await dialog.evaluate((el) => getComputedStyle(el).backgroundColor),
    "rgb(0, 0, 0)",
  );
  await dialog
    .getByRole("button", { name: "Restore resource", exact: true })
    .click();
  assert.deepEqual(
    (await state(fixtures.resource)).data.resource,
    fixtures.resource.data.resource,
  );
  await toggle.click();
  await expect(card(fixtures.housed)).toHaveCount(0);
  // A change arriving after confirmation opens must not be overwritten.
  await remove(fixtures.note).click();
  await page.evaluate(async (id) => {
    const item = await window.tasks.request(`/v1/projects/items/${id}`);
    await window.tasks.request(`/v1/projects/items/${id}`, "PATCH", {
      revision: item.revision,
      idempotency_key: crypto.randomUUID(),
      data: { body: "Concurrent edit" },
    });
  }, fixtures.note.id);
  await dialog
    .getByRole("button", { name: "Delete note", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(/changed|conflict/i);
  assert.equal((await state(fixtures.note)).data.archived, false);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("radio", { name: "Light", exact: true }).click();
  await page.reload();
  await expect(card(fixtures.resource)).toBeVisible();
  await expect(card(fixtures.note)).toBeVisible();
  await page.screenshot({ path: "/tmp/alshitasks-card-board.png" });
  assert.deepEqual(errors, []);
  console.log(
    "Card lifecycle: cancel/focus, unsaved drafts, delete/restore, resource settings and links, portal retirement, stale revisions and restart persistence passed.",
  );
} catch (error) {
  if (app)
    await (
      await app.firstWindow()
    )
      .screenshot({ path: "/tmp/alshitasks-card-failure.png" })
      .catch(() => {});
  throw error;
} finally {
  try {
    execFileSync(
      process.platform === "win32" ? "python" : "/usr/bin/python3",
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
