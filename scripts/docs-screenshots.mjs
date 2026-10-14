// Capture the real UI with an isolated, fictional workspace. No portal credentials.
import { chromium, expect } from "@playwright/test";
import { spawn, execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm, mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
import net from "node:net";
let server, browser, temporary;
const python = process.env.ALSHITASKS_TEST_PYTHON || (process.platform === "win32" ? "python" : "python3");
const remote = process.env.ALSHITASKS_TEST_URL;
const port = remote ? Number(new URL(remote).port || 443) : await new Promise((resolve, reject) => {
  const probe = net.createServer(); probe.on('error', reject);
  probe.listen(0, '127.0.0.1', () => { const selected = probe.address().port; probe.close(() => resolve(selected)); });
});
const url = remote || `http://127.0.0.1:${port}`;
try {
  let fixture;
  if (remote) fixture = JSON.parse(await readFile(process.env.ALSHITASKS_TEST_CREDENTIAL_FILE, "utf8"));
  else {
    temporary = await mkdtemp(path.join(os.tmpdir(), "alshitasks-server-test-"));
    const directory = path.join(temporary, "fixture");
    execFileSync(python, ["scripts/server-fixture.py", directory], {stdio: "pipe"});
    fixture = JSON.parse(await readFile(path.join(directory, "fixture.json"), "utf8"));
    server = spawn(python, ["service/alshitasks-cli.py", "server", "--data-dir", fixture.root, "--key-file", fixture.key, "--port", String(port), "--base-url", url], {stdio: "pipe", windowsHide: true});
    server.stderr.on("data", data => { if (String(data).includes("Traceback")) process.stderr.write("Server failed; inspect fixture\n"); });
  }
  for (let i=0; i<100; i++) {
    try { if ((await fetch(url + "/healthz")).ok) break; } catch {}
    if (i === 99) throw new Error("Server did not start");
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  browser = await chromium.launch({headless: true, ...(process.env.ALSHITASKS_BROWSER_CHANNEL ? {channel: process.env.ALSHITASKS_BROWSER_CHANNEL} : {})});
  const context = await browser.newContext({viewport: {width:1920,height:1200}, ignoreHTTPSErrors: process.env.ALSHITASKS_TEST_TLS === '1'});
  const page = await context.newPage();
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  page.on("response", response => {
    if (new URL(response.url()).pathname.startsWith('/v1/') && response.status() >= 400) errors.push(`${new URL(response.url()).pathname}: HTTP ${response.status()}`);
  });
  await page.goto(url);
  await page.getByLabel("Owner password").fill(fixture.password);
  await page.getByRole("button", {name:"Sign in", exact:true}).click();
  await expect(page.locator(".project-columns-adapter")).toBeVisible({timeout:20000});
  await expect(page.getByRole("button", {name:"Close window"})).toBeHidden();
  await page.evaluate(async () => {
    const api = (route, data) => window.tasks.request(route, 'POST', data);
    const create = (kind, data) => api('/v1/projects/items', {kind, data, idempotency_key: crypto.randomUUID()});
    const board = await create('board', {title: 'Moonlight launch'});
    const tasks = [
      ['Sketch the launch story', 'todo', 'high', 'Make the first five minutes feel effortless.\n\n- A clear welcome\n- One useful example\n- A small moment of delight'],
      ['Find the perfect rocket emoji', 'todo', 'low', 'Very serious launch research.'],
      ['Build the welcome flow', 'doing', 'high', 'Guide a new crew member from a blank board to their first finished task.'],
      ['Give the workspace its colors', 'doing', 'normal', 'A violet rocket, a warm canvas, and room to think.'],
      ['Review the quick-start guide', 'review', 'normal', 'Try the instructions on a fresh machine. Every command should earn its place.'],
      ['Test the offline adventure', 'review', 'high', 'Keep working when the connection takes a coffee break.'],
      ['Name our launch board', 'done', 'normal', 'Moonlight launch it is.'],
      ['Bring the checklist', 'done', 'normal', 'Small steps, visible progress.']
    ];
    const statuses=(await window.tasks.request('/v1/projects/statuses')).statuses;
    for (const [title,state,priority,body] of tasks) {
      const status=statuses.find(s=>s.board_id===board.id && s.legacy_state===state);
      await create('task', {title,state,priority,body,board_id:board.id,...(status?{status_id:status.id}:{})});
    }
    await create('note', {title:'Launch notebook',body:'## The plan\nBuild something useful. Make it delightful. Ship it together.\n\nDemo workspace for documentation; all content is fictional.',color:'purple',board_id:board.id});
    const art=await window.tasks.request('/v1/workspaces/icon?workspace_id=00000000-0000-4000-8000-000000000001');
    await api('/v1/workspaces/icon?workspace_id=00000000-0000-4000-8000-000000000001',{revision:art.revision,icon:'rocket',icon_color:'#7c3aed'});
  });
  await page.reload();
  await expect(page.locator('.project-columns-adapter')).toBeVisible();
  const expand=page.getByRole('button',{name:'Expand menu',exact:true});
  if(await expand.isVisible()) await expand.click();
  await page.getByRole('button',{name:'Moonlight launch',exact:true}).click();
  await expect(page.getByText('Build the welcome flow',{exact:true}).first()).toBeVisible();
  await page.locator('[data-theme-choice="light"]').click();
  await page.waitForTimeout(500);
  await mkdir('docs/images',{recursive:true});
  await page.screenshot({path:'docs/images/board-light.png',fullPage:true});
  await page.locator('[data-theme-choice="dark"]').click();
  await page.waitForTimeout(300);
  await page.screenshot({path:'docs/images/board-dark.png',fullPage:true});
  await page.getByText('Build the welcome flow',{exact:true}).first().click();
  await page.waitForTimeout(500);
  await page.screenshot({path:'docs/images/task-detail.png',fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('Captured fictional board in light/dark mode and task details.');
} finally {
  if (browser) await browser.close();
  if (server) {
    server.kill();
    await new Promise(resolve => server.exitCode !== null ? resolve() : server.once('exit',resolve));
  }
  if (temporary) await rm(temporary,{recursive:true,force:true,maxRetries:10,retryDelay:250});
}
