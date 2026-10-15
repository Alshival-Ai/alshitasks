import { _electron as electron, expect } from '@playwright/test';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';

// Compare the real Electron renderer with the authenticated browser renderer.
// Both use fresh, isolated stores; never point this check at an existing workspace.
export async function verifyDesktopParity(browserPage) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'alshitasks-parity-'));
  let app;
  try {
    app = await electron.launch({
      executablePath: process.env.ALSHITASKS_TEST_EXE || path.resolve('node_modules/electron/dist',process.platform === 'win32' ? 'electron.exe' : 'electron'),
      args: [...(process.platform === 'win32' ? [] : ['--ozone-platform=x11']), path.resolve('.')],
      env: {...process.env, ALSHITASKS_TEST_ROOT: root},
    });
    const desktop = await app.firstWindow();
    desktop.setDefaultTimeout(15000);
    await desktop.setViewportSize({width:1440, height:1000});
    await expect(desktop.locator('.project-columns-adapter')).toBeVisible({timeout:20000});
    const pages = [desktop, browserPage];
    await mkdir('build', {recursive:true});
    const snapshot = (page, selector) => page.locator(selector).evaluate(root => {
      const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
      const css = el => {
        const s = getComputedStyle(el);
        return Object.fromEntries(['fontFamily','fontSize','color','backgroundColor','borderRadius','padding','gap'].map(k => [k,s[k]]));
      };
      return {
        style:css(root),
        controls:Array.from(root.querySelectorAll('input:not([type=hidden]),textarea,select,button')).filter(visible).map(el => ({
          tag:el.tagName, name:el.getAttribute('name'), type:el.getAttribute('type'),
          label:el.getAttribute('aria-label') || (el.tagName === 'BUTTON' ? el.textContent.trim() : ''),
          options:el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.textContent) : undefined,
          style:css(el),
        })),
      };
    });
    for (const theme of ['Light','Dark']) {
      for (const page of pages) await page.getByRole('radio', {name:theme, exact:true}).click();
      assert.deepEqual(await snapshot(desktop,'.app-sidebar'),await snapshot(browserPage,'.app-sidebar'),`${theme} sidebar differs`);
      assert.deepEqual(await snapshot(desktop,'.sp-tabs'),await snapshot(browserPage,'.sp-tabs'),`${theme} board views differ`);
      for (const page of pages) await page.getByRole('button',{name:'New task +',exact:true}).click();
      await expect(desktop.locator('.project-item-pane')).toBeVisible();
      await expect(browserPage.locator('.project-item-pane')).toBeVisible();
      assert.deepEqual(await snapshot(desktop,'.project-item-pane'),await snapshot(browserPage,'.project-item-pane'),`${theme} task form differs`);
      for (const [i,page] of pages.entries()) {
        await page.screenshot({path:`build/parity-${i ? 'browser' : 'desktop'}-${theme.toLowerCase()}.png`,animations:'disabled'});
        await page.getByRole('button',{name:'Close item',exact:true}).click();
      }
    }
    for (const page of pages) {
      await page.locator('.wd-menu-toggle').click();
      await expect(page.locator('.wd-header-menu').getByRole('button',{name:'Team',exact:true})).toHaveCount(0);
      await page.getByRole('button',{name:'Device settings',exact:true}).click();
      await page.getByRole('button',{name:'Manage people and droids',exact:true}).click();
      await page.getByRole('button',{name:'Create profile',exact:true}).click();
    }
    assert.deepEqual(await snapshot(desktop,'.team-editor'),await snapshot(browserPage,'.team-editor'),'Profile editor differs');
    for (const page of pages) {
      await page.getByRole('button',{name:'Back to Settings',exact:true}).click();
      await page.locator('.app-sidebar').getByRole('button',{name:'Local workspace',exact:true}).click();
      await page.getByRole('radio',{name:'Light',exact:true}).click();
    }
    console.log('Electron/browser navigation, board views, task forms, themes and profile editor match.');
  } catch (error) {
    console.error('Desktop/browser comparison failed:', error);
    throw error;
  } finally {
    try {
      execFileSync(process.platform === 'win32' ? 'python' : '/usr/bin/python3',
        [path.resolve('service/alshitasks-cli.py'),'--root',root,'stop'], {timeout:5000,stdio:'ignore'});
    } catch {}
    if (app) {
      app.process().kill('SIGTERM');
      await Promise.race([app.close().catch(() => {}),new Promise(resolve => setTimeout(resolve,3000))]);
    }
    await rm(root,{recursive:true,force:true,maxRetries:10,retryDelay:250});
  }
}
