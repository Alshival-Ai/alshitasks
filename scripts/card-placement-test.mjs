import { _electron as electron } from "playwright";
import { mkdtemp, rm } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
const root = await mkdtemp(path.join(os.tmpdir(), "alshitasks-placement-"));
let app;
try {
  app = await electron.launch({
    executablePath: "/usr/lib/alshitasks/electron/electron",
    chromiumSandbox: true,
    args: ["--ozone-platform=x11", path.resolve(".")],
    env: { ...process.env, ALSHITASKS_TEST_ROOT: root },
  });
  const page = await app.firstWindow();
  page.setDefaultTimeout(15000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator(".project-note-canvas").waitFor();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.evaluate(async () => {
    for (const kind of ["note", "resource"])
      await window.tasks.request("/v1/projects/items", "POST", {
        idempotency_key: crypto.randomUUID(),
        kind,
        data: {
          title: `Placement ${kind}`,
          body: "Placement regression",
          ...(kind === "resource"
            ? { resource: { kind: "server", status: "active" } }
            : {}),
        },
      });
  });
  await page.reload();
  const board = page.locator(".project-parity-board");
  const card = (kind) =>
    page
      .locator(".sp-sticky")
      .filter({
        has: page.locator(
          `.sp-note-handle[aria-label="Move ${kind}: Placement ${kind}"]`,
        ),
      });
  const handle = (kind) => card(kind).locator(".sp-note-handle");
  const point = (kind) =>
    card(kind).evaluate((el) => ({ x: el.offsetLeft, y: el.offsetTop }));
  const move = async (kind, x, y) => {
    await handle(kind).scrollIntoViewIfNeeded();
    const box = await handle(kind).boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(x, y, { steps: 12 });
    await page.mouse.up();
  };
  // Both types can leave the section and cross the full board width.
  for (const [kind, x] of [
    ["note", 350],
    ["resource", 1150],
  ]) {
    await board.evaluate((el) => (el.scrollTop = 0));
    const b = await board.boundingBox();
    await move(kind, b.x + x, b.y + 170);
    await board.evaluate((el) => (el.scrollTop = 0));
    const rect = await card(kind).boundingBox();
    const heading = await page
      .getByRole("heading", { name: "Notes & Resources", exact: true })
      .boundingBox();
    assert.ok(
      rect.y < heading.y,
      `${kind} must move above the section heading`,
    );
    assert.ok(
      (await point(kind)).y < 0,
      "grid-relative position is preserved above the grid",
    );
  }
  const resourceBox = await card("resource").boundingBox();
  const gridBox = await page.locator(".project-note-canvas").boundingBox();
  assert.ok(
    resourceBox.x > gridBox.x + gridBox.width,
    "resource can cross into the timeline side of the board",
  );
  const saved = {
    note: await point("note"),
    resource: await point("resource"),
  };
  await page.reload();
  await card("note").waitFor();
  assert.deepEqual(await point("note"), saved.note);
  assert.deepEqual(await point("resource"), saved.resource);
  // Keyboard movement uses the same boundary, and Escape cancels a pointer move.
  await handle("note").focus();
  await page.keyboard.press("Shift+ArrowUp");
  assert.equal((await point("note")).y, saved.note.y - 40);
  const beforeCancel = await point("note");
  const h = await handle("note").boundingBox();
  await page.mouse.move(h.x + 100, h.y + 12);
  await page.mouse.down();
  await page.mouse.move(h.x + 200, h.y + 90, { steps: 5 });
  await page.keyboard.press("Escape");
  await page.mouse.up();
  assert.deepEqual(await point("note"), beforeCancel);
  // Scrolling during a drag must not displace the card from the pointer.
  await handle("note").scrollIntoViewIfNeeded();
  const sh = await handle("note").boundingBox();
  await page.mouse.move(sh.x + 100, sh.y + 12);
  await page.mouse.down();
  await board.evaluate((el) => (el.scrollTop += 80));
  await page.mouse.move(sh.x + 105, sh.y + 17);
  await page.mouse.up();
  const afterScroll = await handle("note").boundingBox();
  assert.ok(
    Math.abs(afterScroll.y - sh.y - 5) < 8,
    "drag follows pointer when board scrolls",
  );
  await page.screenshot({ path: "/tmp/alshitasks-card-placement.png" });
  // Compact layout is still flow-based; returning to desktop restores placement.
  await page.setViewportSize({ width: 560, height: 900 });
  assert.equal(await handle("note").isVisible(), false);
  assert.equal(
    await card("note").evaluate((el) => getComputedStyle(el).position),
    "relative",
  );
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "Tidy cards", exact: true }).click();
  assert.equal(await page.locator(".sp-sticky.is-placed").count(), 0);
  await page.reload();
  await card("note").waitFor();
  assert.equal(await page.locator(".sp-sticky.is-placed").count(), 0);
  assert.deepEqual(errors, []);
  console.log(
    "Card placement: notes/resources across board, reload persistence, keyboard, cancel, scroll, compact layout and tidy passed.",
  );
} catch (e) {
  if (app)
    await (
      await app.firstWindow()
    )
      .screenshot({ path: "/tmp/alshitasks-card-placement-failure.png" })
      .catch(() => {});
  throw e;
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
