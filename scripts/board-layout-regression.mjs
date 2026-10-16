import assert from 'node:assert/strict';
import { expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

// Run on an isolated fixture with classic scrollbars enabled. A horizontal
// scrollbar must not steal space from a short column and repeatedly rewrap it.
export async function verifyBoardLayout(page) {
  const viewport = page.viewportSize();
  const items = await page.evaluate(async () => {
    const result = [];
    for (const title of ['Review the tablet task form', 'Check keyboard navigation and task assignment in the tablet layout']) {
      result.push(await window.tasks.request('/v1/projects/items', 'POST', {
        idempotency_key: crypto.randomUUID(), kind: 'task', data: {title},
      }));
    }
    return result;
  });
  try {
    await page.reload();
    await expect(page.locator('.project-task')).toHaveCount(2);
    await page.evaluate(() => document.fonts.ready);
    await mkdir('build', {recursive: true});
    for (const width of [1440, 1024, 901, 900, 820, 768, 760, 390]) {
      await page.setViewportSize({width, height: 850});
      // Allow sidebar transitions and the initial ResizeObserver measurement.
      await page.waitForTimeout(400);
      const frames = await page.evaluate(() => new Promise(resolve => {
        const samples = [];
        const sample = () => {
          const root = document.querySelector('.project-columns-adapter');
          const board = root.querySelector('.sp-columns');
          samples.push({
            height: board.getBoundingClientRect().height,
            gutter: board.offsetHeight - board.clientHeight,
            layout: root.dataset.boardLayout,
            overflow: [...board.querySelectorAll('.sp-stack')].some(stack =>
              stack.closest('.sp-column').hidden === false && stack.scrollHeight > stack.clientHeight + 1),
          });
          if (samples.length < 45) requestAnimationFrame(sample);
          else resolve(samples);
        };
        requestAnimationFrame(sample);
      }));
      assert.equal(new Set(frames.map(frame => frame.height)).size, 1, `Board keeps resizing at ${width}px`);
      assert.ok(frames.every(frame => !frame.overflow), `Short task column unnecessarily scrolls at ${width}px`);
      if (width === 1024) {
        assert.ok(frames.every(frame => frame.gutter > 0), 'Regression requires a real horizontal scrollbar');
        await page.screenshot({path: 'build/board-tablet-stable.png'});
        await page.getByRole('button', {name: 'New task +', exact: true}).click();
        const pane = page.locator('.project-item-pane');
        await expect(pane).toBeVisible();
        assert.ok((await pane.getByLabel('Title', {exact:true}).boundingBox()).y <
          (await pane.getByLabel('Description', {exact:true}).boundingBox()).y);
        await page.screenshot({path: 'build/board-tablet-editor.png'});
        await pane.getByRole('button', {name:'Cancel', exact:true}).click();
      }
    }
    console.log('Board height remains stable with classic scrollbars across desktop, tablet and phone widths.');
  } finally {
    await page.evaluate(async rows => {
      for (const item of rows) await window.tasks.request(`/v1/projects/items/${item.id}`, 'PATCH', {
        idempotency_key:crypto.randomUUID(), revision:item.revision, data:{archived:true},
      });
    }, items);
    await page.setViewportSize(viewport);
    await page.reload();
    await expect(page.locator('.project-columns-adapter')).toBeVisible();
  }
}
