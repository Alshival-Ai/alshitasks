import { verifyBoardLayout } from './board-layout-regression.mjs';
import {verifyWebhooksUI} from './webhooks-ui-test.mjs';
import { verifyServerUI, verifyTeamUI } from "./server-ui-parity.mjs";
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
  if (fixture.password_encrypted) {
    // Decrypt only in memory; never print or persist the fixture credential.
    fixture.password = execFileSync(python, ['-c',
      "import json,sys; from pathlib import Path; from cryptography.fernet import Fernet; f=json.load(sys.stdin); sys.stdout.write(Fernet(Path(f['key']).read_bytes()).decrypt(f['password_encrypted'].encode()).decode())"],
      {input: JSON.stringify(fixture), encoding: 'utf8'});
  }
  for (let i=0; i<100; i++) {
    try { if ((await fetch(url + "/healthz")).ok) break; } catch {}
    if (i === 99) throw new Error("Server did not start");
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  browser = await chromium.launch({headless: true, ignoreDefaultArgs: ['--hide-scrollbars'], ...(process.env.ALSHITASKS_BROWSER_CHANNEL ? {channel: process.env.ALSHITASKS_BROWSER_CHANNEL} : {})});
  const context = await browser.newContext({viewport: {width:1440,height:1000}, ignoreHTTPSErrors: process.env.ALSHITASKS_TEST_TLS === '1'});
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
  if (!remote) await verifyBoardLayout(page);
  if (process.env.ALSHITASKS_COMPARE_DESKTOP === '1') {
    if (remote) throw new Error('Desktop parity requires an isolated local fixture');
    const { verifyDesktopParity } = await import('./desktop-browser-parity.mjs');
    await verifyDesktopParity(page);
  }
  await mkdir("build",{recursive:true});
  await verifyWebhooksUI(page);
  await verifyServerUI(page);
  await verifyTeamUI(page);
  const created = await page.evaluate(async () => {
    const item = await window.tasks.request('/v1/projects/items', 'POST', {idempotency_key: crypto.randomUUID(),kind:'task',data:{title:'Browser acceptance task'}});
    const key = await window.tasks.request('/v1/agent-grants','POST',{name:'Acceptance agent',scopes:['project:read','project:write']});
    return {item,key};
  });
  const mcp = async (method, params={}) => {
    const response = await fetch(url + '/mcp', {method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json, text/event-stream','Authorization':'Bearer '+created.key.token},body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})});
    assert.equal(response.status,200);
    return (await response.json()).result;
  };
  assert.ok((await mcp('tools/list')).tools.length >= 19);
  const records = await mcp('tools/call',{name:'project_list',arguments:{}});
  assert.equal(records.isError,undefined,JSON.stringify(records));
  assert.ok(JSON.stringify(records).includes(created.item.id));
  const updated = await mcp('tools/call',{name:'project_update', arguments:{id:created.item.id,idempotency_key:crypto.randomUUID(),revision:created.item.revision,data:{title:'MCP updated acceptance task'}}});
  assert.equal(updated.isError,undefined,JSON.stringify(updated));
  await expect(page.getByText('MCP updated acceptance task',{exact:true}).first()).toBeVisible({timeout:10000});
  await page.evaluate(async (item) => {
    await window.tasks.request('/v1/attachments','POST',{item_id:item,name:'browser.txt',content:btoa('browser attachment')});
  }, created.item.id);
  const second = await context.newPage(); await second.goto(url);
  await expect(second.locator('.project-columns-adapter')).toBeVisible();
  await expect(second.getByText('MCP updated acceptance task',{exact:true}).first()).toBeVisible();
  await page.reload(); await expect(page.locator('.project-columns-adapter')).toBeVisible();
  await page.getByText('MCP updated acceptance task', {exact:true}).first().click();
  const markdown = '# Launch plan\n\n**Ready**\n\n- First step\n\n`code`\n\n| Item | State |\n| --- | --- |\n| Test | Done |\n\n<script>window.markdownUnsafe=true</script>\n\n[bad](javascript:alert(1))';
  await page.getByLabel('Description', {exact:true}).fill(markdown);
  await page.getByText('Preview description (Markdown)', {exact:true}).click();
  const preview = page.locator('.project-markdown-field').filter({has:page.getByLabel('Description', {exact:true})});
  await expect(preview.locator('h1')).toHaveText('Launch plan');
  await expect(preview.locator('strong')).toHaveText('Ready');
  await expect(preview.locator('table')).toBeVisible();
  assert.equal(await preview.locator('script, a[href^="javascript:"]').count(), 0);
  await page.getByLabel('Acceptance criteria', {exact:true}).fill('**Verified**');
  await page.getByText('Preview acceptance criteria (Markdown)', {exact:true}).click();
  await expect(page.locator('.project-markdown-field strong').filter({hasText:'Verified'})).toBeVisible();
  await page.getByLabel('Reply', {exact:true}).fill('**Saved reply**\n\n- Checked');
  await page.getByRole('button', {name:'Add reply', exact:true}).click();
  await expect(page.locator('.project-note-markdown strong').filter({hasText:'Saved reply'})).toBeVisible();
  await expect(page.getByLabel('Reply', {exact:true})).toHaveValue('');
  const downloadEvent = page.waitForEvent('download');
  await page.evaluate(() => window.tasks.backup());
  assert.equal((await downloadEvent).suggestedFilename(),'alshitasks-backup.zip');
  await mkdir('build',{recursive:true});
  await page.screenshot({path:remote?'build/server-ubuntu.png':'build/server-windows.png',fullPage:true});
  assert.deepEqual(errors,[]);
  await page.evaluate(() => window.tasks.request('/auth/logout','POST'));
  await page.reload(); await expect(page.getByLabel('Owner password')).toBeVisible();
  console.log('Browser login, shared UI, MCP parity, attachments, backup, multi-tab and logout passed.');
} finally {
  if (browser) await browser.close();
  if (server) {
    server.kill();
    await new Promise(resolve => server.exitCode !== null ? resolve() : server.once('exit',resolve));
  }
  if (temporary) await rm(temporary,{recursive:true,force:true,maxRetries:10,retryDelay:250});
}
