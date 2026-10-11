import { _electron as electron } from 'playwright';
import { mkdtemp, rm } from 'node:fs/promises';
import { spawnSync, execFileSync } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
const root = await mkdtemp(path.join(os.tmpdir(), 'alshitasks-startup-'));
const appPath = process.env.ALSHITASKS_TEST_APP || path.resolve('.');
const env = { ...process.env, ALSHITASKS_TEST_ROOT: root };
const executablePath = '/usr/lib/alshitasks/electron/electron';
const args = ['--ozone-platform=x11', appPath];
let app;
try {
  app = await electron.launch({ executablePath, args, env, chromiumSandbox: true });
  const page = await app.firstWindow();
  await page.getByRole('button', { name: 'New task +', exact: true }).waitFor();
  assert.equal(page.url(), 'alshitasks://app/index.html');
  const secondary = spawnSync(executablePath, args, { env, encoding: 'utf8', timeout: 10000 });
  assert.equal(secondary.status, 0, secondary.stderr);
  assert.doesNotMatch(secondary.stderr, /ERR_FAILED|Desktop startup failed/);
  assert.equal(app.windows().length, 1);
  console.log('Packaged URL loads; repeated launch exits cleanly and retains one window.');
} finally {
  try { execFileSync('/usr/bin/python3', [path.join(appPath,'service/alshitasks-cli.py'), '--root', root, 'stop'], {timeout:5000,stdio:'ignore'}); } catch {}
  if (app) { app.process().kill('SIGTERM'); await Promise.race([app.close().catch(()=>{}), new Promise(r=>setTimeout(r,3000))]); }
  await rm(root,{recursive:true,force:true});
}
