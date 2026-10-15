import { expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

// Exercise the shared components through the real browser transport and store.
export async function verifyServerUI(page) {
  await mkdir('build', {recursive: true});
  const sidebar = page.locator('.app-sidebar');
  await expect(sidebar.getByRole('button', {name: 'Workspaces', exact: true})).toBeVisible();
  await expect(sidebar).toHaveCSS('width', '256px');
  await sidebar.getByRole('button', {name: 'Collapse menu', exact: true}).click();
  await expect(sidebar).toHaveCSS('width', '72px');
  await expect(sidebar.locator('.nav-label').first()).toBeHidden();
  await sidebar.getByRole('button', {name: 'Expand menu', exact: true}).click();
  await expect.poll(async () => (await page.locator('.app-content').boundingBox()).x).toBe(256);
  await page.getByRole('button', {name: 'New task +', exact: true}).click();
  const pane = page.locator('.project-item-pane');
  await expect(pane).toBeVisible();
  await pane.getByLabel('Title', {exact: true}).fill('Shared UI task');
  await pane.getByLabel('Description', {exact: true}).fill('**One UI** across desktop and browser.');
  await pane.getByText('Preview description (Markdown)', {exact: true}).click();
  await expect(pane.locator('.project-note-markdown strong')).toHaveText('One UI');
  await pane.locator('select[name=priority]').selectOption('high');
  await pane.getByRole('button', {name: 'Save', exact: true}).click();
  await expect(page.locator('.project-task').filter({hasText:'Shared UI task'})).toBeVisible();
  if (await pane.count()) await pane.getByRole('button', {name:'Close item', exact:true}).click();

  const fixtures = await page.evaluate(async () => {
    const create = (kind,data) => window.tasks.request('/v1/projects/items','POST',{kind,data,idempotency_key:crypto.randomUUID()});
    const resource = await create('resource',{title:'Launch API',body:'Keep the configuration',resource:{kind:'api',status:'active',provider:'Fixture',environment:'test',public_url:'https://example.test',external_id:null,tags:['launch']}});
    const note = await create('note',{title:'Launch decisions',body:'Keep this note',color:'yellow'});
    const housed = await create('note',{title:'API instructions',body:'Keep this link',resource_id:resource.id});
    return {resource,note,housed};
  });
  await page.reload();
  const card = item => page.locator(`.sp-sticky[data-item-id="${item.id}"]`);
  const state = item => page.evaluate(id=>window.tasks.request(`/v1/projects/items/${id}`), item.id);
  const dialog = page.getByRole('dialog').filter({has:page.locator('#card-lifecycle-title')});
  const deleted = page.getByRole('button',{name:'Deleted items',exact:true});
  for (const theme of ['Light','Dark']) {
    await page.getByRole('radio',{name:theme,exact:true}).click();
    await expect(card(fixtures.note).getByRole('button',{name:'Delete note: Launch decisions',exact:true})).toBeVisible();
    await expect(card(fixtures.resource).getByRole('button',{name:'Delete resource: Launch API',exact:true})).toBeVisible();
    await page.screenshot({animations:'disabled', path:`build/server-ui-${theme.toLowerCase()}.png`});
  }
  await card(fixtures.note).getByRole('button',{name:'Delete note: Launch decisions',exact:true}).click();
  await expect(dialog.getByRole('button',{name:'Cancel',exact:true})).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await card(fixtures.note).getByRole('button',{name:'Delete note: Launch decisions',exact:true}).click();
  await dialog.getByRole('button',{name:'Delete note',exact:true}).click();
  await expect(card(fixtures.note)).toHaveCount(0);
  assert.equal((await state(fixtures.note)).data.body,'Keep this note');
  await deleted.click();
  await card(fixtures.note).getByRole('button',{name:'Restore note: Launch decisions',exact:true}).click();
  await dialog.getByRole('button',{name:'Restore note',exact:true}).click();
  await deleted.click();
  await expect(card(fixtures.note)).toBeVisible();
  await card(fixtures.resource).getByRole('button',{name:'Launch API',exact:true}).click();
  await page.locator('.connected-paper').getByRole('button',{name:'Delete resource: Launch API',exact:true}).click();
  await dialog.getByRole('button',{name:'Delete resource',exact:true}).click();
  await expect(card(fixtures.housed)).toBeVisible();
  assert.equal((await state(fixtures.housed)).data.resource_id,fixtures.resource.id);
  await deleted.click();
  await card(fixtures.resource).getByRole('button',{name:'Restore resource: Launch API',exact:true}).click();
  await dialog.getByRole('button',{name:'Restore resource',exact:true}).click();
  await deleted.click();
  assert.deepEqual((await state(fixtures.resource)).data.resource,fixtures.resource.data.resource);
  await expect(card(fixtures.housed)).toHaveCount(0);

  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  await expect(sidebar).toBeVisible();
  await expect(sidebar.getByRole('button',{name:'Workspaces',exact:true})).toBeVisible();
  await page.screenshot({animations:'disabled', path:'build/server-ui-mobile-sidebar.png'});
  await page.keyboard.press('Escape');
  await expect(sidebar).toBeHidden();
  await page.getByRole('button',{name:'+ Task',exact:true}).click();
  await expect(pane).toBeVisible();
  const bounds = await pane.boundingBox();
  assert.ok(bounds.width <= 390 && bounds.x >= 0);
  await page.screenshot({animations:'disabled', path:'build/server-ui-mobile-editor.png'});
  await pane.getByRole('button',{name:'Close item',exact:true}).click();
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('radio',{name:'Light',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'Check for Updates',exact:true}).count(),0);
  console.log('Shared sidebar, task editor, themes, mobile layout and note/resource delete/restore passed.');
}
