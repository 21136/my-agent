import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { mkdtemp } from "node:fs/promises";

const testDir = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(testDir, "..");
const ts = createRequire(path.join(desktopRoot, "package.json"))("typescript");
const overviewSource = readFileSync(path.join(desktopRoot, "src/shells/unified/current-work-overview.ts"), "utf8");
const overviewJs = ts.transpileModule(overviewSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const overviewModule = {};
new Function("exports", "require", overviewJs)(overviewModule, specifier => {
  if (specifier.startsWith(".")) {
    const resolved = path.resolve(path.dirname(path.join(desktopRoot, "src/shells/unified/current-work-overview.ts")),
      specifier.endsWith(".ts") ? specifier : `${specifier}.ts`);
    const dependencySource = readFileSync(resolved, "utf8");
    const dependencyJs = ts.transpileModule(dependencySource, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const dependencyExports = {};
    new Function("exports", dependencyJs)(dependencyExports);
    return dependencyExports;
  }
  return createRequire(path.join(desktopRoot, "package.json"))(specifier);
});
const css = readFileSync(path.join(desktopRoot, "src/shells/unified/current-work-overview.css"), "utf8");

function syntheticGroup(kind, id, prefix) {
  return {
    kind, id, visibility: "visible", status: "in_progress", status_counts: { in_progress: 4 },
    item_count: 2, requirement_count: 4,
    items: [0, 1].map(itemIndex => ({
      item_id: `${prefix}-item-${itemIndex + 1}`, title: `${prefix}事项 ${itemIndex + 1}`,
      status: "in_progress", status_counts: { in_progress: 2 },
      requirements: [1, 2].map(reqIndex => ({
        requirement_id: `${prefix}-r-${itemIndex}-${reqIndex}`,
        text: `${prefix}事项${itemIndex + 1}要求${reqIndex}`, status: "in_progress",
        ...(reqIndex === 2 ? { depends_on: [`${prefix}-r-${itemIndex}-1`] } : {}),
      })), current_results: [],
    })),
  };
}

const projects = Array.from({ length: 5 }, (_, index) => syntheticGroup("project", `合成项目${index + 1}`, `P${index + 1}`));
const independent = syntheticGroup("independent", "internal-independent-id-must-not-display", "独立");
independent.items = [{ ...independent.items[0], title: "修改简历", requirements: independent.items[0].requirements.map((requirement, index) => index === 1 ? { ...requirement, depends_on: ["not-in-this-authorized-item"] } : requirement) }];
independent.item_count = 1;
independent.requirement_count = 2;
independent.status_counts = { in_progress: 2 };
const groups = [...projects, independent];
const result = {
  type: "work-records.overview.result", session_id: "synthetic-session", request_id: "synthetic-request",
  state: "visible", groups, unread_important_results: [], unread_status: "known", visibility_limitations: [],
  visibility_counts: { visible_groups: groups.length, visible_items: groups.reduce((sum, group) => sum + group.item_count, 0), collapsed_groups: 0, collapsed_items: 0 },
  snapshot_id: "synthetic-snapshot",
  page: { page_size: 20, offset: 0, total_groups: groups.length, total_visible_groups: groups.length,
    total_visible_items: 11, total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null },
};
result.aggregation_scope = "selected_project_sessions";
result.schedule_preview = {
  state: "visible",
  candidates: [{ project_id: "project-a", item_id: "item-1", requirement_id: "req-ready", priority: 4 }],
  blocked: [{ project_id: "project-b", item_id: "item-2", requirement_id: "req-wait", priority: 3, reasons: ["依赖 dep 状态为 queued，尚未完成"] }],
  limitations: ["只读预览；不会启动任务或修改事项状态"],
};
const panel = overviewModule.buildCurrentWorkOverviewPanel(result, { open: true });
assert.match(panel.bodyHtml, /只读调度预览/);
assert.match(panel.bodyHtml, /req-ready/);
assert.match(panel.bodyHtml, /req-wait/);
assert.match(panel.bodyHtml, /不会启动任务/);
assert.match(panel.bodyHtml, /依赖关系（1）/);
assert.match(panel.bodyHtml, /当前要求受阻/);
assert.match(panel.bodyHtml, /依赖未包含在当前授权事项中，状态无法核实/);
assert.match(css, /unified-current-work-dependency-edges/);
const screenshotBase = path.join(os.tmpdir(), `current-work-overview-render-${process.pid}-${Date.now()}`);
const screenshotPath = `${screenshotBase}.png`;
const independentScreenshot = `${screenshotBase}-independent.png`;
const narrowScreenshot = `${screenshotBase}-narrow.png`;

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const samePath = (a, b) => process.platform === "win32"
  ? path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase()
  : path.resolve(a) === path.resolve(b);

// Fail closed: only the exact mkdtemp result, a direct child of the checked temp root,
// with unchanged directory identity and no symlink/junction, is eligible for removal.
function profileIsSafe({ profile, tmpRoot, realRoot, realProfile, stat, createdStat, prefix, exited }) {
  if (!exited || !path.isAbsolute(profile) || !path.isAbsolute(tmpRoot) || !path.isAbsolute(realRoot) || !path.isAbsolute(realProfile)) return false;
  const name = path.basename(profile);
  if (!samePath(path.dirname(profile), tmpRoot) || !name.startsWith(prefix) || name.length <= prefix.length) return false;
  if (!stat.isDirectory() || stat.isSymbolicLink() || !createdStat.isDirectory() || createdStat.isSymbolicLink()) return false;
  if (stat.dev !== createdStat.dev || stat.ino !== createdStat.ino) return false;
  return samePath(path.dirname(realProfile), realRoot) && samePath(realProfile, path.join(realRoot, name));
}

async function cleanupOwnedProfile({ profile, tmpRoot, realRoot, createdStat, prefix, exited, fileOps, sleep = delay }) {
  const { lstat, realpath, rm } = fileOps ?? await import("node:fs/promises");
  if (!exited) return { removed: false, reason: "owned Edge process exit was not confirmed" };
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const rootNow = await realpath(tmpRoot);
      const stat = await lstat(profile);
      const realProfile = await realpath(profile);
      if (!samePath(rootNow, realRoot) || !profileIsSafe({ profile, tmpRoot, realRoot, realProfile, stat, createdStat, prefix, exited })) {
        return { removed: false, reason: "profile path, real path, directory identity or link validation failed" };
      }
      await rm(profile, { recursive: true, force: false, maxRetries: 0 });
      return { removed: true };
    } catch (error) {
      if (["EBUSY", "EPERM", "EACCES", "ENOTEMPTY"].includes(error.code) && attempt < 2) {
        await sleep(400 * (attempt + 1));
        continue;
      }
      return { removed: false, reason: `cleanup or validation failed (${error.code || error.message}); profile may be partially removed` };
    }
  }
  return { removed: false, reason: "cleanup retries exhausted; profile may be partially removed" };
}

async function waitForExit(exitPromise, timeoutMs) {
  let timer;
  const timedOut = new Promise(resolve => { timer = setTimeout(() => resolve(false), timeoutMs); });
  try { return await Promise.race([exitPromise, timedOut]); } finally { clearTimeout(timer); }
}

test("profile cleanup validation fails closed for unsafe paths, links and unconfirmed exit", () => {
  const root = path.resolve(os.tmpdir());
  const prefix = `current-work-overview-edge-${process.pid}-`;
  const profile = path.join(root, prefix + "sample");
  const dir = { isDirectory: () => true, isSymbolicLink: () => false, dev: 1, ino: 2 };
  const valid = { profile, tmpRoot: root, realRoot: root, realProfile: profile, stat: dir, createdStat: dir, prefix, exited: true };
  assert.equal(profileIsSafe(valid), true);
  assert.equal(profileIsSafe({ ...valid, exited: false }), false);
  assert.equal(profileIsSafe({ ...valid, profile: path.join(root, "other-profile") }), false);
  assert.equal(profileIsSafe({ ...valid, profile: path.join(root, "nested", prefix + "sample") }), false);
  assert.equal(profileIsSafe({ ...valid, realProfile: path.join(root, "elsewhere") }), false);
  assert.equal(profileIsSafe({ ...valid, stat: { ...dir, isSymbolicLink: () => true } }), false);
  assert.equal(profileIsSafe({ ...valid, stat: { ...dir, ino: 3 } }), false);
});
test("cleanup retains profile on unsafe validation or persistent Windows lock", async () => {
  const root = path.resolve(os.tmpdir());
  const prefix = `current-work-overview-edge-${process.pid}-`;
  const profile = path.join(root, prefix + "mock-only");
  const stat = { isDirectory: () => true, isSymbolicLink: () => false, dev: 1, ino: 2 };
  let removals = 0;
  const fileOps = { lstat: async () => stat, realpath: async p => p,
    rm: async () => { removals++; const error = new Error("locked"); error.code = "EPERM"; throw error; } };
  const args = { profile, tmpRoot: root, realRoot: root, createdStat: stat, prefix, exited: true, fileOps, sleep: async () => {} };
  const unsafe = await cleanupOwnedProfile({ ...args, prefix: "unexpected-prefix-" });
  assert.equal(unsafe.removed, false);
  assert.match(unsafe.reason, /validation failed/);
  assert.equal(removals, 0, "unsafe path never reaches recursive removal");
  const locked = await cleanupOwnedProfile(args);
  assert.equal(locked.removed, false);
  assert.match(locked.reason, /EPERM/);
  assert.equal(removals, 3, "locked profile is retained after bounded retries");
});

async function waitForOwnedBrowser(profile, child) {
  const { readFile } = await import("node:fs/promises");
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (child.exitCode !== null || child.signalCode !== null) throw new Error("Owned Edge exited before publishing DevToolsActivePort");
    try {
      const content = await readFile(path.join(profile, "DevToolsActivePort"), "utf8");
      const [portText, browserPath] = content.trim().split(/\r?\n/);
      const port = Number(portText);
      assert.ok(Number.isInteger(port) && port > 0 && port <= 65535, "invalid owned DevTools port");
      assert.match(browserPath || "", /^\/devtools\/browser\/[a-zA-Z0-9-]+$/, "invalid owned browser endpoint");
      return { port, url: `ws://127.0.0.1:${port}${browserPath}` };
    } catch (error) {
      if (error.code !== "ENOENT" && error.code !== "EBUSY" && error.code !== "EACCES") throw error;
    }
    await delay(100);
  }
  throw new Error("Timed out waiting for owned profile's DevToolsActivePort");
}
function connectCdp(url) {
  const socket = new WebSocket(url);
  let nextId = 0;
  const pending = new Map();
  socket.addEventListener("message", event => {
    const message = JSON.parse(String(event.data));
    if (!message.id) return;
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    if (message.error) waiter.reject(new Error(message.error.message));
    else waiter.resolve(message.result);
  });
  const ready = new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  return { socket, ready, send(method, params = {}, sessionId) {
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  } };
}
async function evaluate(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "renderer evaluation failed");
  return result.result?.value;
}

test("current-work overview renders five multi-requirement projects and an independent item", async () => {
  const executablePath = process.env.MY_AGENT_PLAYWRIGHT_EXECUTABLE || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
  const { access, lstat, realpath, writeFile } = await import("node:fs/promises");
  await access(executablePath);
  assert.ok(path.isAbsolute(os.tmpdir()), "OS temporary root must be absolute");
  const tmpRoot = path.resolve(os.tmpdir());
  const realRoot = await realpath(tmpRoot);
  const prefix = `current-work-overview-edge-${process.pid}-`;
  const profile = await mkdtemp(path.join(tmpRoot, prefix));
  let createdStat;
  try { createdStat = await lstat(profile); } catch (error) {
    console.warn(`Temporary Edge profile retained at: ${profile}; creation identity could not be verified (${error.code || error.message})`);
    throw error;
  }
  const child = spawn(executablePath, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", "--disable-component-update", "--no-proxy-server",
    "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
  { stdio: "ignore", windowsHide: true });
  const exitPromise = new Promise(resolve => {
    child.once("close", () => resolve(true));
    child.once("error", () => resolve(false));
  });
  let cdp;
  let browserCdp;
  try {
    const owned = await waitForOwnedBrowser(profile, child);
    cdp = connectCdp(owned.url);
    await cdp.ready;
    assert.equal(child.exitCode, null, "owned Edge must remain running");
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    browserCdp = cdp;
    cdp = { socket: browserCdp.socket, send: (method, params) => browserCdp.send(method, params, sessionId) };
    console.log(`Owned Edge DevTools port ${owned.port} read from unique profile; fresh isolated target created.`);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Network.enable");
    await cdp.send("Network.setBlockedURLs", { urls: ["http://*", "https://*", "ws://*", "wss://*"] });
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1200, height: 900, deviceScaleFactor: 1, mobile: false });
    const html = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
      :root { --ma-chrome-border:#cbd5e1; --ma-border:#cbd5e1; --ma-surface:#fff; --ma-text:#172033; --ma-text-muted:#536174; }
      body { margin:0; padding:0; font:14px/1.45 system-ui,sans-serif; background:#f4f6f8; color:#172033; }
      .fixture-anchor { height:40px; }
      ${css}
    </style></head><body><div class="unified-topbar-current-work-wrap fixture-anchor"><main id="overview" class="unified-current-work-popover">${panel.bodyHtml}</main></div></body></html>`;
    await cdp.send("Page.navigate", { url: "data:text/html;charset=utf-8," + encodeURIComponent(html) });
    await delay(150);
    const initial = await evaluate(cdp, `(() => { const pop=document.querySelector('#overview'), bounds=pop.getBoundingClientRect(); return ({ groups:document.querySelectorAll('.unified-current-work-group').length,
      disclosures:document.querySelectorAll('details.unified-current-work-requirements').length,
      open:document.querySelectorAll('details.unified-current-work-requirements[open]').length,
      titles:[...document.querySelectorAll('.unified-current-work-group h4')].map(x=>x.innerText),
      visibleGroups:[...document.querySelectorAll('.unified-current-work-group')].filter(x=>{ const r=x.getBoundingClientRect(); return r.top>=bounds.top && r.bottom<=bounds.bottom; }).length,
      groupSummaries:[...document.querySelectorAll('.unified-current-work-group-counts')].map(x=>x.innerText),
      requirements:[...document.querySelectorAll('.unified-current-work-group')].map(x=>x.querySelectorAll('details.unified-current-work-requirements > ul > li').length) }); })()`);
    assert.equal(initial.groups, 6, "five projects plus one independent group render");
    assert.equal(initial.disclosures, 11, "each of 11 items has a disclosure");
    assert.equal(initial.open, 0, "requirements default to collapsed");
    assert.ok(initial.visibleGroups >= 4, `at least four project groups should fit without scrolling; saw ${initial.visibleGroups}`);
    assert.match(initial.groupSummaries[0], /4 项要求/, "project status counts summarize its four persisted requirements");
    for (let i = 1; i <= 5; i += 1) assert.match(initial.titles[i - 1], new RegExp(`合成项目${i}`));
    assert.equal(initial.titles[5], "修改简历", "single independent group heading uses item.title, not its internal id");
    assert.deepEqual(initial.requirements, [4, 4, 4, 4, 4, 2]);
    assert.ok(!initial.titles.join(" ").includes(independent.id), "internal group id is not displayed");
    console.log(`Initial 1200x900 viewport: ${initial.visibleGroups} complete groups visible without scrolling.`);
    const initialShot = await cdp.send("Page.captureScreenshot", { format: "png" });
    await writeFile(screenshotPath, Buffer.from(initialShot.data, "base64"));

    await evaluate(cdp, `document.querySelector('details.unified-current-work-requirements summary').click()`);
    const expanded = await evaluate(cdp, `(() => [...document.querySelectorAll('.unified-current-work-group')].map(x=>x.querySelectorAll('details[open]').length))()`);
    assert.deepEqual(expanded, [1, 0, 0, 0, 0, 0], "one disclosure expands without affecting other projects");
    await evaluate(cdp, "document.querySelector(\"#overview\").scrollIntoView({block:\"start\"})");
    await delay(150);
    await evaluate(cdp, "document.querySelector('#overview').scrollTop = document.querySelector('#overview').scrollHeight");
    await delay(150);
    const independentShot = await cdp.send("Page.captureScreenshot", { format: "png" });
    await writeFile(independentScreenshot, Buffer.from(independentShot.data, "base64"));

    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 360, height: 740, deviceScaleFactor: 1, mobile: true });
    await evaluate(cdp, "document.querySelector('details.unified-current-work-requirements summary').scrollIntoView({block:'start'})");
    await delay(150);
    const narrow = await evaluate(cdp, `(() => { const pop=document.querySelector('#overview'), b=pop.getBoundingClientRect(), s=pop.querySelector('summary').getBoundingClientRect();
      return { viewportWidth:document.documentElement.clientWidth, scrollWidth:document.documentElement.scrollWidth,
        popoverWidth:b.width, popoverClientWidth:pop.clientWidth, canScrollVertically:pop.scrollHeight>pop.clientHeight,
        firstSummaryVisible:s.width>0 && s.left>=0 && s.right<=innerWidth }; })()`);
    assert.ok(narrow.popoverWidth <= narrow.viewportWidth, JSON.stringify(narrow));
    assert.ok(narrow.scrollWidth <= narrow.viewportWidth, JSON.stringify(narrow));
    assert.ok(narrow.firstSummaryVisible, "disclosure remains visible at 360px viewport");
    // Synthetic cursor fixtures exercise the real overview renderer inside the isolated
    // Edge DOM. A tiny in-page click recorder stands in for transport; Node applies the
    // same pure merge helpers with synthetic server frames. This is DOM evidence only,
    // not full index/WS or GUI acceptance.
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1200, height: 900, deviceScaleFactor: 1, mobile: false });
    const req = (itemId, index, status = "pending") => ({
      requirement_id: `req-${itemId}-${index}`, text: `synthetic ${itemId} requirement ${index}`, status,
    });
    const reqPage = (groupId, itemId, offset, total, nextCursor) => ({
      kind: "requirements", group_kind: "project", group_id: groupId, item_id: itemId,
      offset, total, has_more: nextCursor !== null, next_cursor: nextCursor,
    });
    const makeItem = (groupId, index, requirementOffset = 0, count = 5) => {
      const itemId = `${groupId}-item-${index}`;
      const allRequirements = Array.from({ length: 6 }, (_, reqIndex) => req(itemId, reqIndex,
        index === 0 && reqIndex === 0 ? "blocked" : "pending"));
      return {
        item_id: itemId, title: `synthetic ${groupId} item ${index}`, status: index === 0 ? "blocked" : "pending",
        status_counts: index === 0 ? { blocked: 1, pending: 5 } : { pending: 6 },
        requirements: allRequirements.slice(requirementOffset, requirementOffset + count),
        current_results: index === 0 && requirementOffset === 0
          ? [{ requirement_id: allRequirements[0].requirement_id, result_id: `result-${itemId}-0`, status: "blocked", read_status: "unread" }]
          : [],
        requirements_page: reqPage(groupId, itemId, requirementOffset, 6,
          requirementOffset + count < 6 ? `requirements-${itemId}-${requirementOffset + count}` : null),
      };
    };
    const bigGroupId = "synthetic-project-0";
    const allBigItems = Array.from({ length: 8 }, (_, index) => makeItem(bigGroupId, index));
    const groupItemsPage = { kind: "items", group_kind: "project", group_id: bigGroupId,
      item_id: null, offset: 0, total: 8, has_more: true, next_cursor: "items-synthetic-project-0-5" };
    const bigGroup = { kind: "project", id: bigGroupId, visibility: "visible", status: "blocked",
      status_counts: { blocked: 1, pending: 7 }, item_count: 8, requirement_count: 48,
      items: allBigItems.slice(0, 5), items_page: groupItemsPage };
    const otherGroups = Array.from({ length: 6 }, (_, index) => {
      const id = `synthetic-project-${index + 1}`;
      const item = makeItem(id, 0, 0, 6);
      return { kind: "project", id, visibility: "visible", status: "pending", status_counts: { pending: 6 },
        item_count: 1, requirement_count: 6, items: [item],
        items_page: { kind: "items", group_kind: "project", group_id: id, item_id: null,
          offset: 0, total: 1, has_more: false, next_cursor: null } };
    });
    const overviewPage = (groups, offset, hasMore, nextCursor) => ({
      type: "work-records.overview.result", session_id: "synthetic-dom-session", request_id: "synthetic-request",
      state: "visible", groups, unread_important_results: [{ group: ["project", bigGroupId],
        item_id: allBigItems[0].item_id, requirement_id: allBigItems[0].requirements[0].requirement_id,
        result_id: `result-${allBigItems[0].item_id}-0` }], unread_status: "known", visibility_limitations: [],
      unread_important_results_total: 1, unread_important_results_truncated: false,
      visibility_counts: { visible_groups: 7, visible_items: 14, collapsed_groups: 0, collapsed_items: 0 },
      snapshot_id: "synthetic-dom-snapshot",
      page: { page_size: 6, offset, total_groups: 7, total_visible_groups: 7, total_visible_items: 14,
        total_collapsed_groups: 0, total_collapsed_items: 0, has_more: hasMore, next_cursor: nextCursor },
    });
    const makePageHtml = (value) => {
      const view = overviewModule.buildCurrentWorkOverviewPanel(value, { open: true });
      const nextGroups = view.canExpand
        ? `<button type="button" id="synthetic-next-groups" data-cursor="${value.page.next_cursor}">${view.moreLabel}</button>` : "";
      return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
        :root { --ma-chrome-border:#cbd5e1; --ma-border:#cbd5e1; --ma-surface:#fff; --ma-text:#172033; --ma-text-muted:#536174; }
        body { margin:0; padding:0; font:14px/1.45 system-ui,sans-serif; background:#f4f6f8; color:#172033; }
        ${css}
      </style></head><body><main id="overview">${nextGroups}${view.bodyHtml}</main><script>
        document.addEventListener('click', event => {
          const button = event.target.closest('button[data-current-work-detail-cursor],#synthetic-next-groups');
          if (!button) return;
          window.syntheticOverviewRequest = { cursor: button.dataset.currentWorkDetailCursor || button.dataset.cursor || '',
            kind: button.dataset.currentWorkDetailKind || 'groups', groupId: button.dataset.currentWorkDetailGroupId || '',
            itemId: button.dataset.currentWorkDetailItemId || '' };
        });
      </script></body></html>`;
    };
    let paged = overviewPage([bigGroup, ...otherGroups.slice(0, 5)], 0, true, "groups-synthetic-6");
    await cdp.send("Page.navigate", { url: "data:text/html;charset=utf-8," + encodeURIComponent(makePageHtml(paged)) });
    await delay(120);
    const firstPage = await evaluate(cdp, `(() => ({ groups:document.querySelectorAll('.unified-current-work-group').length,
      firstProjectItems:document.querySelectorAll('.unified-current-work-group:first-of-type .unified-current-work-item').length,
      attention:document.querySelectorAll('.unified-current-work-group:first-of-type .unified-current-work-item .unified-current-work-unread').length,
      groupMore:document.querySelector('#synthetic-next-groups')?.innerText || '',
      itemCursor:document.querySelector('[data-current-work-detail-kind="items"]')?.dataset.currentWorkDetailCursor || '',
      requirementCursor:document.querySelector('[data-current-work-detail-kind="requirements"]')?.dataset.currentWorkDetailCursor || '' }))()`);
    assert.equal(firstPage.groups, 6);
    assert.equal(firstPage.firstProjectItems, 5);
    assert.equal(firstPage.attention, 1, "unread attention marker remains visible in the initial bounded item page");
    assert.match(firstPage.groupMore, /展开更多/);
    assert.equal(firstPage.itemCursor, "items-synthetic-project-0-5");
    assert.equal(firstPage.requirementCursor, `requirements-${allBigItems[0].item_id}-5`);

    await evaluate(cdp, "document.querySelector('#synthetic-next-groups').click()");
    const groupRequest = await evaluate(cdp, "window.syntheticOverviewRequest");
    assert.deepEqual(groupRequest, { cursor: "groups-synthetic-6", kind: "groups", groupId: "", itemId: "" });
    const seventh = { kind: "independent", id: "synthetic-resume", visibility: "visible", status: "pending",
      status_counts: { pending: 1 }, item_count: 1, requirement_count: 1,
      items: [{ item_id: "synthetic-resume-item", title: "synthetic resume", status: "pending",
        status_counts: { pending: 1 }, requirements: [req("synthetic-resume-item", 0)], current_results: [],
        requirements_page: reqPage("synthetic-resume", "synthetic-resume-item", 0, 1, null) }],
      items_page: { kind: "items", group_kind: "independent", group_id: "synthetic-resume", item_id: null,
        offset: 0, total: 1, has_more: false, next_cursor: null } };
    paged = overviewModule.appendCurrentWorkOverviewPage(paged,
      overviewPage([seventh], 6, false, null));
    assert.equal(paged.groups.length, 7);
    await cdp.send("Page.navigate", { url: "data:text/html;charset=utf-8," + encodeURIComponent(makePageHtml(paged)) });
    await delay(120);
    assert.equal(await evaluate(cdp, "document.querySelectorAll('.unified-current-work-group').length"), 7,
      "the seventh group is accessible after its cursor page");

    await evaluate(cdp, "document.querySelector('[data-current-work-detail-kind=items]').click()");
    const itemRequest = await evaluate(cdp, "window.syntheticOverviewRequest");
    assert.deepEqual(itemRequest, { cursor: "items-synthetic-project-0-5", kind: "items", groupId: bigGroupId, itemId: "" });
    const secondItems = allBigItems.slice(5);
    const secondItemsPage = { ...groupItemsPage, offset: 5, has_more: false, next_cursor: null };
    const itemPageResponse = overviewPage([{ ...bigGroup, items: secondItems, items_page: secondItemsPage }], 0, true, "groups-synthetic-6");
    itemPageResponse.detail_page = secondItemsPage;
    const withAllItems = overviewModule.mergeCurrentWorkOverviewDetailPage(paged, itemPageResponse);
    assert.ok(withAllItems);
    paged = withAllItems;
    await cdp.send("Page.navigate", { url: "data:text/html;charset=utf-8," + encodeURIComponent(makePageHtml(paged)) });
    await delay(120);
    const allItemsDom = await evaluate(cdp, `(() => ({ count:document.querySelectorAll('.unified-current-work-group:first-of-type .unified-current-work-item').length,
      titles:[...document.querySelectorAll('.unified-current-work-group:first-of-type .unified-current-work-item strong')].map(node=>node.innerText) }))()`);
    assert.equal(allItemsDom.count, 8);
    assert.equal(allItemsDom.titles.length, 8);

    await evaluate(cdp, `(() => { const details=document.querySelector('.unified-current-work-group:first-of-type .unified-current-work-requirements'); details.open=true; details.querySelector('[data-current-work-detail-kind="requirements"]').click(); })()`);
    const requirementRequest = await evaluate(cdp, "window.syntheticOverviewRequest");
    assert.equal(requirementRequest.kind, "requirements");
    assert.equal(requirementRequest.itemId, allBigItems[0].item_id);
    assert.equal(requirementRequest.cursor, `requirements-${allBigItems[0].item_id}-5`);
    const finalReqPage = { kind: "requirements", group_kind: "project", group_id: bigGroupId,
      item_id: allBigItems[0].item_id, offset: 5, total: 6, has_more: false, next_cursor: null };
    const finalReqItem = { ...allBigItems[0], requirements: [req(allBigItems[0].item_id, 5)],
      current_results: [], requirements_page: finalReqPage };
    const reqPageResponse = overviewPage([{ ...bigGroup, items: [finalReqItem], items_page: null }], 0, true, "groups-synthetic-6");
    reqPageResponse.detail_page = finalReqPage;
    const withAllRequirements = overviewModule.mergeCurrentWorkOverviewDetailPage(paged, reqPageResponse);
    assert.ok(withAllRequirements);
    paged = withAllRequirements;
    await cdp.send("Page.navigate", { url: "data:text/html;charset=utf-8," + encodeURIComponent(makePageHtml(paged)) });
    await delay(120);
    const completeDom = await evaluate(cdp, `(() => { const group=document.querySelector('.unified-current-work-group');
      const details=group.querySelector('.unified-current-work-requirements'); details.open=true;
      return { items:group.querySelectorAll('.unified-current-work-item').length,
        requirements:details.querySelectorAll('li').length, button:details.querySelector('[data-current-work-detail-kind="requirements"]')?.dataset.currentWorkDetailCursor || null }; })()`);
    assert.equal(completeDom.items, 8);
    assert.equal(completeDom.requirements, 6);
    assert.equal(completeDom.button, null, "no cursor button remains after the final requirements page");
    assert.equal(overviewModule.mergeCurrentWorkOverviewDetailPage(paged,
      { ...reqPageResponse, snapshot_id: "changed-snapshot" }), null, "changed source snapshot must not merge");
    console.log(`Renderer: existing Edge (${executablePath}); source module and CSS loaded from sandbox files.`);
    const narrowShot = await cdp.send("Page.captureScreenshot", { format: "png" });
    await writeFile(narrowScreenshot, Buffer.from(narrowShot.data, "base64"));
    console.log(`Screenshots retained at: ${screenshotPath}; ${independentScreenshot}; ${narrowScreenshot}`);
    console.log(`Narrow viewport evidence: ${JSON.stringify(narrow)}`);
  } finally {
    if (browserCdp && child.exitCode === null && child.signalCode === null) {
      try { await Promise.race([browserCdp.send("Browser.close"), delay(1500)]); } catch { /* socket may close before the response */ }
    }
    try { cdp?.socket.close(); } catch { /* owned CDP socket only */ }
    let exited = await waitForExit(exitPromise, 3000);
    if (!exited && child.exitCode === null && child.signalCode === null) {
      try { child.kill(); } catch { /* owned child only; exit must still be confirmed */ }
      exited = await waitForExit(exitPromise, 3000);
    }
    const cleanup = await cleanupOwnedProfile({ profile, tmpRoot, realRoot, createdStat, prefix, exited });
    if (cleanup.removed) console.log(`Temporary Edge profile removed after confirmed exit: ${profile}`);
    else console.warn(`Temporary Edge profile retained at: ${profile}; ${cleanup.reason}`);
  }
});



