/**
 * Win32: companion 当前工作 overlay → split (copy requirement) + merge independent items.
 * Mock WS fixture only. Opt-in: WORK_ITEM_MERGE_SPLIT_GUI_OPT_IN=1
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  EDGE_LAUNCH_ARGS,
  acceptLatestComposerConfirm,
  edge,
  openCurrentWorkOverlay,
  pw,
  refreshCurrentWorkOverview,
  resolvePython,
  waitControlDone,
  currentWorkItemByTitle,
  currentWorkQueueBody,
} from "./current-work-overlay-browser-shared.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const repo = path.resolve(desktop, "..");
const guiOptIn = process.env.WORK_ITEM_MERGE_SPLIT_GUI_OPT_IN === "1";

function itemByTitle(overview, title) {
  for (const group of overview?.groups ?? []) {
    if (group.kind !== "independent") continue;
    const item = group.items?.find(row => row.title === title);
    if (item) return item;
  }
  return null;
}

function independentItemCount(overview) {
  return (overview?.groups ?? [])
    .filter(group => group.kind === "independent")
    .reduce((total, group) => total + (group.items?.length ?? 0), 0);
}

test("companion overlay: split requirement then merge two independent items", {
  skip: !guiOptIn || process.platform !== "win32" || !existsSync(pw) || !existsSync(edge)
    ? "Win32 opt-in: WORK_ITEM_MERGE_SPLIT_GUI_OPT_IN=1"
    : false,
  timeout: 180_000,
}, async t => {
  const python = resolvePython();
  const esbuild = createRequire(path.join(desktop, "package.json"))("esbuild");
  const { chromium } = createRequire(path.join(desktop, "package.json"))(pw);
  const child = spawn(python, [path.join(here, "work-item-merge-split-browser-fixture.py"), path.join(repo, "agent-core")], {
    cwd: desktop,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  t.after(() => { if (child.exitCode === null) child.kill(); });
  const lines = createInterface({ input: child.stdout });
  let stderr = "";
  child.stderr.on("data", chunk => { stderr += chunk; });
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`fixture startup timeout: ${stderr}`)), 25_000);
    lines.once("line", line => { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch (e) { reject(e); } });
    child.once("exit", code => { clearTimeout(timer); reject(new Error(`fixture exited ${code}: ${stderr}`)); });
  });
  assert.equal(ready.companion_unbound, true);

  const bundle = await esbuild.build({
    stdin: {
      contents: 'export { mountUnifiedShell } from "./src/shells/unified/index.ts"; export { AgentWsClient } from "./src/api/ws.ts";',
      resolveDir: desktop,
      loader: "ts",
    },
    bundle: true, write: false, platform: "browser", format: "iife", globalName: "UnifiedUnderTest",
    define: { "import.meta.url": '"http://127.0.0.1/isolated.js"' },
    loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
    plugins: [{ name: "no-css", setup(b) { b.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }],
    logLevel: "silent",
  });
  const css = readFileSync(path.join(desktop, "src/shells/unified/unified.css"), "utf8")
    + readFileSync(path.join(desktop, "src/shells/unified/current-work-overview.css"), "utf8");

  const browser = await chromium.launch({ headless: true, executablePath: edge, args: EDGE_LAUNCH_ARGS });
  let page;
  try {
    const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1280, height: 900 } });
    await context.route("**/*", r => r.request().url() === `http://127.0.0.1:${ready.port}/`
      ? r.fulfill({ status: 200, contentType: "text/html", body: `<!doctype html><style>${css}</style><div id="root"></div>` })
      : r.abort());
    page = await context.newPage();
    page.setDefaultTimeout(20_000);
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${ready.port}/`);
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(async port => {
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      window.client = new UnifiedUnderTest.AgentWsClient("127.0.0.1", port);
      window.receivedEvents = [];
      window.client.onEvent(event => window.receivedEvents.push(event));
      window.dispose = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), window.client);
      await window.client.connect();
    }, ready.port);

    const banner = await page.evaluate(() => window.receivedEvents.find(e => e.type === "session.banner"));
    assert.equal(banner?.project_id ?? "", "");
    assert.doesNotMatch(await page.evaluate(() => document.querySelector(".unified-shell")?.className ?? ""), /\bis-tool-fullpage\b/);

    const work = await openCurrentWorkOverlay(page);
    assert.match(await page.evaluate(() => document.querySelector(".unified-shell")?.className ?? ""), /is-current-work-overlay/);
    assert.equal(await page.locator("[data-pending-confirm-hint]").count(), 0);
    assert.equal(await page.locator(".unified-chat-confirm-card").count(), 0);

    let overview = await refreshCurrentWorkOverview(page);
    assert.equal(independentItemCount(overview), 3);

    const queue = currentWorkQueueBody(work);
    await page.waitForFunction(() => {
      const panel = document.querySelector('#unified-current-work [data-cw-segment-panel="queue"]:not([hidden]) .current-work-zone-body');
      return panel?.querySelectorAll(".unified-current-work-item").length === 3;
    });
    const splitItem = currentWorkItemByTitle(work, "Split parent item");
    await splitItem.locator("[data-cw-select-item]").click();
    await splitItem.getByRole("button", { name: "拆出为新事项" }).click();
    await acceptLatestComposerConfirm(page);
    await waitControlDone(page, "copy_requirement_to_new_item");
    overview = await refreshCurrentWorkOverview(page);
    assert.equal(independentItemCount(overview), 4);
    const splitChild = overview.groups.flatMap(g => g.items).find(i => i.item_id !== ready.split_parent_item
      && i.requirements?.some(r => r.text === "Requirement to split into new item"));
    assert.ok(splitChild, "split produced a new independent item with copied requirement text");

    await queue.locator("[data-cw-merge-toggle]").click();
    const targetPick = queue.locator(`input[data-cw-merge-pick][data-item-id="${ready.target_item}"]`);
    const sourcePick = queue.locator(`input[data-cw-merge-pick][data-item-id="${ready.source_item}"]`);
    await targetPick.check();
    await sourcePick.check();
    await queue.locator("[data-cw-merge-apply]").click();
    await acceptLatestComposerConfirm(page);
    await waitControlDone(page, "merge_independent_item");
    overview = await refreshCurrentWorkOverview(page);

    const target = itemByTitle(overview, "AAA merge target item");
    const source = itemByTitle(overview, "ZZZ merge source item");
    assert.ok(target?.requirements?.some(r => r.text === "Requirement to merge into target"));
    assert.ok(!source?.requirements?.some(r => r.requirement_id === ready.source_requirement));
    assert.equal(errors.length, 0, errors.join("\n"));
    await page.evaluate(() => { window.dispose(); window.client.destroy(); });
    await context.close();
  } catch (error) {
    const events = page ? await page.evaluate(() => window.receivedEvents) : [];
    throw new Error(`${error.message}\nevents=${JSON.stringify(events)}\nstderr=${stderr}`);
  } finally {
    await browser.close();
    child.stdin.end("\n");
    await new Promise(resolve => {
      if (child.exitCode !== null) return resolve();
      child.once("exit", resolve);
      setTimeout(() => { child.kill(); resolve(); }, 5000);
    });
    lines.close();
    assert.equal(stderr.trim(), "", stderr);
  }
});
