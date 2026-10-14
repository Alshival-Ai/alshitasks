import { _electron as electron, expect } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
const root = await mkdtemp(path.join(os.tmpdir(), "alshitasks-log-ui-"));
let app;
try {
  app = await electron.launch({
    executablePath: process.env.ALSHITASKS_TEST_EXE || "/usr/lib/alshitasks/electron/electron",
    chromiumSandbox: true,
    args: [
      ...(process.platform === "win32" ? [] : ["--ozone-platform=x11"]),
      process.env.ALSHITASKS_TEST_APP || path.resolve("."),
    ],
    env: { ...process.env, ALSHITASKS_TEST_ROOT: root },
  });
  const page = await app.firstWindow();
  await page.getByRole("button", { name: /New task/ }).waitFor();
  await page.evaluate(async () => {
    const item = await window.tasks.request("/v1/projects/items", "POST", {
      kind: "resource",
      data: { title: "Log test app" },
      idempotency_key: crypto.randomUUID(),
    });
    const setup = await window.tasks.request(
      "/v1/logs/setup?resource_id=" + item.id,
    );
    await window.tasks.request(setup.logs_path, "POST", {
      events: [
        {
          message: "Cloud-compatible fixture",
          severity: "TRACE",
          source: "worker",
          trace_id: "ab".repeat(16),
          span_id: "cd".repeat(8),
        },
      ],
    });
  });
  await page.reload();
  await page.getByRole("button", { name: "Log test app", exact: true }).click();
  await page
    .getByRole("navigation", { name: "Resource content" })
    .getByRole("button", { name: "Logs", exact: true })
    .click();
  await page.getByLabel("Severity", { exact: true }).selectOption("TRACE");
  await page.getByLabel("Trace ID", { exact: true }).fill("ab".repeat(16));
  await page.getByRole("button", { name: "Refresh logs", exact: true }).click();
  await page.getByText("Cloud-compatible fixture", { exact: true }).waitFor();
  await page.getByText("Source: worker", { exact: true }).waitFor();
  await page.getByText("Send logs here", { exact: true }).click();
  await page
    .getByRole("button", { name: "Enable localhost listener", exact: true })
    .waitFor();
  assert.match(
    await page
      .getByLabel("OpenTelemetry exporter configuration", { exact: true })
      .inputValue(),
    /OTEL_EXPORTER_OTLP_LOGS_ENDPOINT/,
  );
  await page
    .getByRole("button", { name: "Create sender token", exact: true })
    .click();
  await page.getByLabel("New sender token").waitFor();
  await page
    .getByRole("button", { name: "Dismiss token", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Revoke Log sender", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Revoke Log sender", exact: true })
    .waitFor({ state: "detached" });
  // Exercise the packaged renderer with deterministic discovery transitions.
  // Keep local operations on the real fixture service; never connect a real account.
  await app.evaluate(({ipcMain}) => {
    const original = ipcMain._invokeHandlers.get('tasks:request');
    globalThis.logFixture = {access:'restricted', context:'fixture:1', requests:0, hold:false};
    ipcMain.removeHandler('tasks:request');
    ipcMain.handle('tasks:request', async (event, route, ...args) => {
      const fixture = globalThis.logFixture;
      if (route.startsWith('/v1/logs/setup?')) {
        const result = await original(event, route, ...args);
        return {...result, connected:true, portal_logs:fixture.access, portal_logs_context:fixture.context};
      }
      if (route.startsWith('/v1/portal/logs?')) {
        fixture.requests++;
        if (fixture.hold) await new Promise(resolve => {fixture.release = resolve;});
        return {portal_logs:'available',items:[{id:'cloud',timestamp:'2026-01-01T00:00:00Z',severity:'INFO',message:'Permission-scoped cloud fixture'}],cache:{state:'cached',stale:false}};
      }
      return original(event, route, ...args);
    });
  });
  const source = page.getByLabel('Source', {exact:true});
  const portal = source.locator('option[value="portal"]');
  await expect(source).toHaveValue('local');
  await expect(page.getByText('Cloud Logs isn’t available for this workspace.', {exact:true})).toBeVisible();
  await expect(portal).toHaveJSProperty("disabled", true);
  await page.waitForTimeout(5500);
  assert.equal(await app.evaluate(()=>globalThis.logFixture.requests),0);
  await app.evaluate(()=>{globalThis.logFixture.access='unknown';});
  await expect(page.getByText('Cloud Logs availability hasn’t been confirmed for this workspace.', {exact:true})).toBeVisible();
  await expect(portal).toHaveJSProperty("disabled", true);
  assert.equal(await page.getByRole('alert').count(),0);
  await app.evaluate(()=>{globalThis.logFixture.access='available';});
  await expect(portal).toHaveJSProperty("disabled", false);
  await source.selectOption('portal');
  await expect(page.getByText('Permission-scoped cloud fixture',{exact:true})).toBeVisible();
  await app.evaluate(()=>{globalThis.logFixture.access='restricted';});
  await expect(source).toHaveValue('local');
  await expect(page.getByText('Permission-scoped cloud fixture',{exact:true})).toHaveCount(0);
  const requests = await app.evaluate(()=>globalThis.logFixture.requests);
  await page.waitForTimeout(5500);
  assert.equal(await app.evaluate(()=>globalThis.logFixture.requests),requests);
  await app.evaluate(()=>{globalThis.logFixture.access='available';globalThis.logFixture.hold=true;});
  await expect(portal).toHaveJSProperty("disabled", false);
  await source.selectOption('portal');
  await expect.poll(()=>app.evaluate(()=>Boolean(globalThis.logFixture.release))).toBe(true);
  await app.evaluate(()=>{globalThis.logFixture.context='other-account:2';globalThis.logFixture.access='unknown';});
  await expect(source).toHaveValue('local');
  await app.evaluate(()=>globalThis.logFixture.release());
  await expect(page.getByText('Permission-scoped cloud fixture',{exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Refresh logs',exact:true}).click();
  await expect(page.getByText('Cloud-compatible fixture',{exact:true})).toBeVisible();
  console.log(
    "PASS: packaged Logs view, permission transitions, polling suppression, late-response fence, local ingestion/query and token lifecycle.",
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
