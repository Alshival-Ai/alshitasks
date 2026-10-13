import { _electron as electron } from "playwright";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
const root = await mkdtemp(path.join(os.tmpdir(), "alshitasks-icon-ui-"));
let app;
try {
  app = await electron.launch({
    executablePath: "/usr/lib/alshitasks/electron/electron",
    chromiumSandbox: true,
    args: [
      "--ozone-platform=x11",
      process.env.ALSHITASKS_TEST_APP || path.resolve("."),
    ],
    env: { ...process.env, ALSHITASKS_TEST_ROOT: root },
  });
  const page = await app.firstWindow();
  await page.getByRole("button", { name: /New task/ }).waitFor();
  await page
    .getByRole("button", { name: "Settings for Local workspace", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("radio", { name: "Rocket", exact: true }).check();
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
  const colors = [];
  for (const theme of ["Light", "Dark"]) {
    await page.getByRole("radio", { name: theme, exact: true }).click();
    colors.push(await glyph.evaluate((e) => getComputedStyle(e).color));
    await page.screenshot({
      path: "/tmp/workspace-icon-" + theme.toLowerCase() + ".png",
    });
  }
  assert.notEqual(colors[0], colors[1]);
  await page.reload();
  await page
    .locator('.wd-nav-list .workspace-icon use[href$="#rocket"]')
    .waitFor({ state: "attached" });
  await page.getByRole("button", { name: "Expand menu", exact: true }).click();
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
  await page.screenshot({ path: "/tmp/workspace-icon-picker.png" });
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
  if (app) await app.close();
  await rm(root, { recursive: true, force: true });
}
