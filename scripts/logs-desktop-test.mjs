import { _electron as electron } from "playwright";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
const root = await mkdtemp(path.join(os.tmpdir(), "alshitasks-log-ui-"));
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
  console.log(
    "PASS: cloud log ingestion, trace filtering, fields, setup examples and token lifecycle in Electron.",
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
