import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
const artifacts =
  process.env.UI_ARTIFACTS || join(tmpdir(), "alshitasks-workspace-ui");
mkdirSync(artifacts, { recursive: true });
const browser = await chromium.launch({
  ...(process.env.BROWSER_PATH
    ? { executablePath: process.env.BROWSER_PATH }
    : {}),
  headless: true,
});
const page = await browser.newPage({
  viewport: { width: 1440, height: 1000 },
  reducedMotion: "reduce",
});
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.addInitScript(() => {
  const rows = [
    {
      id: "00000000-0000-4000-8000-000000000001",
      name: "Local workspace",
      local: true,
      state: "local",
    },
    ...Array.from({ length: 1000 }, (_, i) => ({
      id: "w" + i,
      name: i === 0 ? "Tracegrade" : `Workspace ${String(i).padStart(4, "0")}`,
      state: "connected",
      kind: i % 2 ? "customer" : "internal",
      sync: "up-to-date",
      settings_url: "/workspace/w" + i + "/settings/",
      neural_labs_url: i === 0 ? "/workspace/w0/neural-labs/" : "",
      portal_origin: "https://alshival.ai",
      scopes: ["project:read", "project:write"],
    })),
  ];
  const boards = [
    {
      id: "board-one",
      revision: 1,
      kind: "board",
      data: { title: "Delivery" },
    },
    {
      id: "board-two",
      revision: 1,
      kind: "board",
      data: { title: "Planning" },
    },
  ];
  const task = {id:"task-one",kind:"task",revision:1,author_id:"member-one",data:{type_id:"bug",label_ids:["backend"],title:"Assignment fixture",board_id:"board-one",status_id:"todo",state:"todo",assignee:null,body:"",visibility:"team",priority:"normal",deleted:false,archived:false,checklist:[]}};
  window.calls = [];
  window.externalLinks = [];
  window.tasks = {
    request: async (path, method = "GET", body) => {
      window.calls.push(path);
      const url = new URL(path, "http://fixture"),
        p = url.pathname;
      if (p.endsWith("/boards")) return { boards: structuredClone(boards) };
      if (p.endsWith("/items") && method === "POST") {
        const item = {
          id: "created-board",
          revision: 1,
          kind: body.kind,
          data: body.data,
        };
        boards.push(item);
        return { item };
      }
      if (p.includes("/items/") && method === "PATCH") {
        if (window.failAssignment && p.endsWith("/task-one")) throw Error("Assignment could not be saved");
        const item = [task, ...boards].find((b) => b.id === p.split("/").pop());
        if (item.revision !== body.revision) throw Error("Stale revision");
        Object.assign(item.data, body.data);
        item.revision++;
        return item.id === "task-one" ? structuredClone(item) : { item };
      }
      if (p.endsWith("/taxonomy")) return {types:[{id:"task",name:"Task",icon:"task",position:0,is_default:true,archived:false,revision:1},{id:"bug",name:"Bug",icon:"bug",position:1,is_default:false,archived:false,revision:1}],labels:[{id:"backend",name:"Backend",archived:false,revision:1}],revision:"fixture",can_manage:true};
      if (p.endsWith("/members")) return {members:[{id:"member-one",display_name:"Alex Rivera"},{id:"member-two",display_name:"Morgan Lee"}]};
      if (p.endsWith("/items")) return {items:url.searchParams.get("workspace_id")==="w0" ? [structuredClone(task)] : [],next:null};
      if (p === "/v1/workspaces") return { workspaces: rows };
      if (p === "/v1/portal/account")
        return {
          account: {
            id: "fixture",
            name: "Fixture",
            origin: "https://alshival.ai",
          },
          managed: true,
          status: { state: "connected" },
          navigation: [],
        };
      if (p === "/v1/notifications")
        return { items: [], next_page: null, unread_count: 0, portal: {} };
      if (p.endsWith("/statuses"))
        return {
          can_manage: true,
          statuses: ["todo", "doing", "done"].map((s, i) => ({
            id: s,
            key: s,
            board_id: "board-one",
            category: s,
            legacy_state: s,
            name: s,
            position: i,
            color: "#8058dd",
          })),
        };
      return {
        items: [],
        next: null,
        boards: [],
        edges: [],
        members: [],
        keys: [],
        drafts: [],
        conflicts: [],
        publications: [],
      };
    },
    onChanged: () => () => {},
    onWindowState: () => () => {},
    onNotification: () => () => {},
    initialNotification: async () => null,
    windowAction: async () => false,
    external: async (url) => window.externalLinks.push(url),
    theme: async () => {},
    connect: async () => {},
  };
});
try {
  await page.goto(process.env.UI_BASE_URL || "http://127.0.0.1:8383");
  await page
    .getByRole("heading", { name: "Workspaces", exact: true })
    .waitFor();
  assert.equal(await page.locator(".wd-card").count(), 30);
  assert.ok(
    !(await page.evaluate(() => window.calls)).some(
      (p) => p.includes("/items") && p.includes("workspace_id=w"),
    ),
  );
  assert.equal(await page.locator('.wd-open').first().evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(0, 0, 0)');
  assert.equal(await page.locator('.wd-boards summary').first().evaluate(e=>getComputedStyle(e).fontWeight),'650');
  await page.screenshot({ path: join(artifacts, "workspaces-desktop.png") });
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    await page.waitForFunction(theme=>document.documentElement.classList.contains('dark-style') === (theme==='dark'),theme);
    const styles = await page.evaluate(() => Object.fromEntries(['.wd-open','.wd-badge','.wd-boards summary','.wd-card h2'].map(selector => {
      const css=getComputedStyle(document.querySelector(selector));
      return [selector,Object.fromEntries(['color','backgroundColor','fontSize','fontWeight','lineHeight','padding','borderRadius','minHeight'].map(key=>[key,css[key]]))];
    })));
    writeFileSync(join(artifacts,`controls-${theme}.json`),JSON.stringify(styles,null,2));
    await page.screenshot({path:join(artifacts,`workspaces-${theme}.png`)});
  }
  await page.emulateMedia({colorScheme:'light'});
  await page.locator(".workspace-toggle").click();
  await page.locator("#workspace-options").waitFor();
  await page.mouse.click(1250, 200);
  await page.locator("#workspace-options").waitFor({ state: "hidden" });
  await page.locator(".workspace-toggle").click();
  await page.keyboard.press("Escape");
  assert.equal(
    await page.locator(".workspace-toggle").getAttribute("aria-expanded"),
    "false",
  );
  await page.locator(".wd-directory input[type=search]").fill("Tracegrade");
  assert.equal(await page.locator(".wd-card").count(), 1);
  await page.locator(".wd-card summary").click();
  await page
    .getByRole("button", { name: "Manage boards", exact: true })
    .click();
  const manager = page.getByRole("region", {
    name: "Manage boards in Tracegrade",
  });
  await manager.getByLabel("Board name", { exact: true }).fill("Release");
  await manager
    .getByRole("button", { name: "Create board", exact: true })
    .click();
  await manager
    .getByLabel("Name for Release", { exact: true })
    .fill("Release checks");
  const form = manager
    .locator("form")
    .filter({ has: page.getByLabel("Name for Release", { exact: true }) });
  await form.getByRole("button", { name: "Rename", exact: true }).click();
  const renamed = manager.locator("form").filter({
    has: page.getByLabel("Name for Release checks", { exact: true }),
  });
  await renamed
    .getByRole("button", { name: "Archive board", exact: true })
    .click();
  await renamed
    .getByRole("button", { name: "Restore board", exact: true })
    .click();
  await page
    .getByRole("navigation", { name: "Boards in Tracegrade" })
    .getByRole("button", { name: "Planning", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Tracegrade", exact: true, level: 1 })
    .waitFor();
  const boardNav = page.getByRole("navigation", {
    name: "Current workspace boards",
  });
  await page.waitForFunction(() =>
    document
      .querySelector('.wd-board-children [aria-current="page"]')
      ?.textContent.includes("Planning"),
  );
  await boardNav.getByRole("button", { name: "Delivery", exact: true }).click();
  await page.waitForFunction(() =>
    document
      .querySelector('.wd-board-children [aria-current="page"]')
      ?.textContent.includes("Delivery"),
  );
  assert.equal(
    await page.getByRole("button", { name: "Open board", exact: true }).count(),
    0,
  );
  assert.equal(
    await page
      .getByRole("button", { name: "Manage boards", exact: true })
      .count(),
    0,
  );
  assert.equal(
    await page.getByLabel("Project board", { exact: true }).count(),
    0,
  );
  await page
    .getByRole("button", { name: "Settings for Tracegrade", exact: true })
    .click();
  await page.getByRole("dialog").getByRole("heading", {name:"Task types",exact:true}).waitFor();
  await page.getByRole("button", {name:"Other portal settings",exact:true}).click();
  assert.equal((await page.evaluate(() => window.externalLinks)).length, 1);
  await page.getByRole("dialog").getByRole("button", {name:"Close",exact:true}).click();
  await page.screenshot({
    path: join(artifacts, "workspace-board-desktop.png"),
  });
  const assignee = page.getByRole('button',{name:'Assign Assignment fixture',exact:true});
  await assignee.click();
  const picker = page.getByRole('dialog',{name:'Assign task',exact:true});
  await picker.waitFor();
  await picker.getByRole('searchbox',{name:'Find a person'}).fill('nobody');
  await picker.getByText('No matching people.').waitFor();
  await picker.getByRole('searchbox',{name:'Find a person'}).fill('Alex');
  await page.screenshot({path:join(artifacts,'assignee-light.png')});
  await picker.getByRole('button',{name:'Alex Rivera',exact:true}).click();
  await picker.waitFor({state:'hidden'});
  assert.equal(await assignee.textContent(),'Alex Rivera');
  await assignee.click();
  await page.keyboard.press('Escape');
  await picker.waitFor({state:'hidden'});
  assert(await assignee.evaluate(e=>e===document.activeElement));
  await assignee.click();
  await page.evaluate(()=>{ window.failAssignment=true; });
  await picker.getByRole('button',{name:'Morgan Lee',exact:true}).click();
  await picker.getByRole('alert').waitFor();
  assert.equal(await assignee.textContent(),'Alex Rivera');
  await page.evaluate(()=>{ window.failAssignment=false; });
  await picker.getByRole('button',{name:'Unassigned',exact:true}).click();
  await picker.waitFor({state:'hidden'});
  assert.equal(await assignee.textContent(),'Unassigned');
  await page.emulateMedia({colorScheme:'dark'});
  assert.equal(await page.getByRole('button',{name:'New task +',exact:true}).evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(255, 255, 255)');
  await assignee.click();
  await page.screenshot({path:join(artifacts,'assignee-dark.png')});
  await page.getByRole('heading',{name:'Tracegrade',exact:true,level:1}).click();
  await picker.waitFor({state:'hidden'});
  await page.setViewportSize({width:390,height:844});
  await assignee.click();
  const rect = await picker.boundingBox();
  assert(rect.x >= 0 && rect.x + rect.width <= 390);
  assert(rect.y >= 0 && rect.y + rect.height <= 844);
  await page.screenshot({path:join(artifacts,'assignee-mobile.png')});
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:1440,height:1000});
  await page.emulateMedia({colorScheme:'light'});
  const rail = page.locator('.app-sidebar');
  await rail.getByRole('button',{name:'Neural Labs (opens in browser)',exact:true}).click();
  assert.equal((await page.evaluate(() => window.externalLinks)).at(-1),'https://alshival.ai/workspace/w0/neural-labs/');
  await rail.locator('[data-app-toggle]').click();
  assert.equal(await rail.evaluate(e => Math.round(e.getBoundingClientRect().width)),72);
  const avatar = rail.locator('.wd-avatar').first();
  assert.equal(await avatar.evaluate(e => getComputedStyle(e).opacity),'1');
  assert.equal(await rail.locator('.nav-label:visible').count(),0);
  assert(await rail.locator('.wd-nav-list').evaluate(e => e.scrollWidth <= e.clientWidth));
  await avatar.hover();
  await page.getByRole('tooltip').waitFor();
  assert.equal(await rail.locator('[data-app-toggle]').getAttribute('aria-expanded'),'false');
  await page.screenshot({path:join(artifacts,'sidebar-collapsed-light.png')});
  await rail.getByRole('button',{name:'Choose board',exact:true}).click();
  await page.locator('#sidebar-boards').getByRole('button',{name:'Planning',exact:true}).click();
  await page.locator('#sidebar-boards').waitFor({state:'hidden'});
  await page.mouse.move(600,100);
  await page.emulateMedia({colorScheme:'dark'});
  await page.screenshot({path:join(artifacts,'sidebar-collapsed-dark.png')});
  await rail.locator('[data-app-toggle]').click();
  await page.screenshot({path:join(artifacts,'sidebar-expanded-dark.png')});
  await page.emulateMedia({colorScheme:'light'});
  await page.getByRole("button", { name: "Account", exact: true }).click();
  await page.getByRole("button", { name: "Startup preferences" }).click();
  await page.locator(".wd-startup select").selectOption("last");
  await page.reload();
  await page
    .getByRole("heading", { name: "Tracegrade", exact: true, level: 1 })
    .waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: join(artifacts, "workspace-mobile-desktop.png"),
  });
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  await page.getByRole('dialog',{name:'Alshival navigation'}).waitFor();
  assert(await page.locator('.app-content').evaluate(e => e.inert));
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.app-content').evaluate(e => e.inert),false);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.emulateMedia({ colorScheme: "dark" });
  await page.screenshot({ path: join(artifacts, "workspace-dark.png") });
  assert.deepEqual(errors, []);
  console.log(
    "1000-workspace pagination, search, dismissal, title, settings destination and startup restoration passed.",
  );
} finally {
  await browser.close();
}
