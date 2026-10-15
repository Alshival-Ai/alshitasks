import {expect} from '@playwright/test';
export async function verifyWebhooksUI(page) {
  await page.locator('.app-sidebar').getByRole('button',{name:'Workspaces',exact:true}).click();
  const settings=page.getByRole('button',{name:'Settings for Local workspace',exact:true});
  await settings.click();
  const dialog=page.getByRole('dialog',{name:'Local workspace settings',exact:true});
  const section=dialog.getByRole('region',{name:'Webhooks'});
  await section.getByRole('button',{name:'Add webhook',exact:true}).click();
  await section.getByLabel('Name',{exact:true}).fill('Acceptance receiver');
  await section.getByLabel('Receiving URL').fill('http://127.0.0.1:43122/webhooks/alshitasks');
  await section.getByRole('button',{name:'Save webhook',exact:true}).click();
  await expect(section.getByLabel('Webhook signing secret')).toBeVisible();
  await section.getByRole('button',{name:'Hide secret',exact:true}).click();
  await expect(section.getByRole('button',{name:'Send test event',exact:true})).toBeDisabled();
  await expect(section.getByText('Disabled',{exact:true})).toBeVisible();
  await page.screenshot({path:'build/webhooks-settings.png'});
  await dialog.getByRole('button',{name:'Close',exact:true}).first().click();
  const hooks=await page.evaluate(()=>window.tasks.request('/v1/webhooks'));
  const hook=hooks.webhooks.find(h=>h.name==='Acceptance receiver');
  if(!hook||hook.enabled)throw Error('Webhook default must be disabled');
  await page.evaluate(id=>window.tasks.request('/v1/webhooks/'+id,'DELETE'),hook.id);
  await page.locator('.app-sidebar').getByRole('button',{name:'Local workspace',exact:true}).click();
}
