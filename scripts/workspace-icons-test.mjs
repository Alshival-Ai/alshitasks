import { _electron as electron } from "playwright";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
const root = await mkdtemp(path.join(os.tmpdir(), "alshitasks-icon-ui-"));
let app;
try {
  app = await electron.launch({
    executablePath:
      process.platform === "win32"
        ? process.env.ALSHITASKS_TEST_EXE
        : "/usr/lib/alshitasks/electron/electron",
    chromiumSandbox: true,
    args: [
      ...(process.platform === "win32" ? [] : ["--ozone-platform=x11"]),
      process.env.ALSHITASKS_TEST_APP || path.resolve("."),
    ],
    env: { ...process.env, ALSHITASKS_TEST_ROOT: root },
  });
  const page = await app.firstWindow();
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(1024, 768),
  );
  await page.getByRole("button", { name: /New task/ }).waitFor();
  const menu = page.locator(".wd-header-menu").first();
  await menu.getByRole("button", { name: "Settings", exact: true }).click();
  assert.equal(
    await menu
      .getByRole("button", { name: "Account settings", exact: true })
      .count(),
    0,
  );
  assert.ok(
    await menu
      .getByRole("button", { name: "Sign in with Alshival.Ai", exact: true })
      .isVisible(),
  );
  await menu.getByRole("button", { name: "Settings", exact: true }).click();
  const bell = page.getByRole("button", { name: /Notifications, \d+ unread/ });
  const bellBox = await bell.boundingBox(),
    themeBox = await page
      .locator(".window-chrome .theme-control")
      .boundingBox();
  assert.ok(
    bellBox.x + bellBox.width <= themeBox.x &&
      Math.abs(bellBox.y - themeBox.y) < 15,
  );
  assert.equal(
    await bell.evaluate((el) => getComputedStyle(el).borderTopWidth),
    "0px",
  );
  await bell.click();
  await page
    .getByRole("dialog", { name: "Notifications", exact: true })
    .waitFor();
  await page.keyboard.press("Escape");
  assert.ok(await bell.evaluate((el) => el === document.activeElement));
  await page
    .getByRole("button", { name: "Workspaces", exact: true })
    .first()
    .click();
  await page
    .getByRole("button", { name: "Settings for Local workspace", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("radio", { name: "Rocket", exact: true }).check();
  assert.equal(
    await dialog.locator(".workspace-icon-picker input[type=radio]").count(),
    24,
  );
  await dialog
    .getByRole("button", { name: "Use color #db2777", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "Save workspace icon", exact: true })
    .click();
  await dialog.getByText("Workspace icon saved.", { exact: true }).waitFor();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await page
    .locator('.wd-nav-list .workspace-icon use[href$="#rocket"]')
    .waitFor({ state: "attached" });
  await page
    .getByRole("button", { name: "Collapse menu", exact: true })
    .click();
  const glyph = page.locator(".wd-nav-list .workspace-icon").first();
  assert.ok(await glyph.isVisible());
  assert.equal(
    await glyph.evaluate(
      (e) => getComputedStyle(e.parentElement).backgroundColor,
    ),
    "rgba(0, 0, 0, 0)",
  );
  const colors = [];
  for (const theme of ["Light", "Dark"]) {
    await page.getByRole("radio", { name: theme, exact: true }).click();
    colors.push(await glyph.evaluate((e) => getComputedStyle(e).color));
    await page.screenshot({
      path: path.join(
        os.tmpdir(),
        "workspace-icon-" + theme.toLowerCase() + ".png",
      ),
    });
  }
  assert.equal(colors[0], "rgb(219, 39, 119)");
  assert.equal(colors[0], colors[1]);
  await page.reload();
  await page
    .locator('.wd-nav-list .workspace-icon use[href$="#rocket"]')
    .waitFor({ state: "attached" });
  await page.getByRole("button", { name: "Expand menu", exact: true }).click();
  await page
    .getByRole("button", { name: "Workspaces", exact: true })
    .first()
    .click();
  await page
    .getByRole("button", { name: "Settings for Local workspace", exact: true })
    .first()
    .click();
  await dialog.getByRole("radio", { name: "Rocket", exact: true }).waitFor();
  assert.ok(
    await dialog
      .getByRole("radio", { name: "Rocket", exact: true })
      .isChecked(),
  );
  assert.equal(
    await dialog.locator("input[type=color]").inputValue(),
    "#db2777",
  );
  await page.screenshot({
    path: path.join(os.tmpdir(), "workspace-icon-picker.png"),
  });
  for (const theme of ["Light", "Dark", "System"]) {
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await page.getByRole("radio", { name: theme, exact: true }).click();
    await page
      .getByRole("button", {
        name: "Settings for Local workspace",
        exact: true,
      })
      .first()
      .click();
    await dialog.getByRole("radio", { name: "Rocket", exact: true }).waitFor();
    const dark = await page.evaluate(
      () => document.documentElement.dataset.theme === "dark",
    );
    assert.equal(
      await dialog.evaluate((el) => getComputedStyle(el).backgroundColor),
      dark ? "rgb(0, 0, 0)" : "rgb(246, 244, 239)",
    );
    assert.equal(
      await dialog
        .locator(".taxonomy-settings select")
        .first()
        .evaluate((el) => getComputedStyle(el).backgroundColor),
      dark ? "rgb(0, 0, 0)" : "rgb(246, 244, 239)",
    );
    await page.screenshot({
      path: path.join(os.tmpdir(), `workspace-settings-${theme}.png`),
    });
  }
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(760, 650),
  );
  assert.ok(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth));
  await dialog
    .locator(".workspace-settings-content")
    .evaluate((el) => (el.scrollTop = el.scrollHeight));
  assert.ok(
    await dialog
      .getByRole("button", { name: "Close", exact: true })
      .isVisible(),
  );
  await page.screenshot({
    path: path.join(os.tmpdir(), "workspace-settings-narrow.png"),
  });
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(1024, 768),
  );
  await dialog.locator("input[type=color]").fill("#123abc");
  await dialog
    .getByRole("button", { name: "Save workspace icon", exact: true })
    .click();
  await dialog.getByText("Workspace icon saved.", { exact: true }).waitFor();
  await dialog
    .getByRole("button", { name: "Reset to default", exact: true })
    .click();
  assert.notEqual(
    await dialog.locator("input[type=color]").inputValue(),
    "#123abc",
  );
  await dialog
    .getByRole("button", { name: "Save workspace icon", exact: true })
    .click();
  await dialog.getByText("Workspace icon saved.", { exact: true }).waitFor();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(900, 650),
  );
  await bell.click();
  const notificationBox = await page
    .getByRole("dialog", { name: "Notifications", exact: true })
    .boundingBox();
  const viewport = await page.evaluate(() => ({
    width: innerWidth,
    height: innerHeight,
  }));
  assert.ok(
    notificationBox.x >= 0 &&
      notificationBox.x + notificationBox.width <= viewport.width,
  );
  assert.ok(
    notificationBox.y >= 0 &&
      notificationBox.y + notificationBox.height <= viewport.height,
  );
  await page
    .getByRole("button", { name: "Close notifications", exact: true })
    .click();
  assert.ok(await bell.evaluate((el) => el === document.activeElement));
  console.log(
    "PASS: workspace icon picker, save, restart persistence, collapsed navigation and light/dark rendering.",
  );
} catch (e) {
  if (app)
    console.error(
      (await (await app.firstWindow()).locator("body").innerText()).slice(
        -5000,
      ),
    );
  throw e;
} finally {
  if (app) {
    await (
      await app.firstWindow()
    )
      .evaluate(() => window.tasks.service("stop"))
      .catch(() => {});
    await app.close();
  }
  await rm(root, {
    recursive: true,
    force: true,
    maxRetries: 20,
    retryDelay: 250,
  });
}
