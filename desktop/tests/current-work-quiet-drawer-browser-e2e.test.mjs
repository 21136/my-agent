/**
 * Win32 GUI: #110 scoped composer confirms + #111 quiet 当前工作 drawer.
 * Synthetic WS only. Opt-in: CURRENT_WORK_QUIET_DRAWER_GUI_OPT_IN=1
 *
 * Pass = interaction/layout contract. Screenshots are NOT visual authenticity / Electron glass parity.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createInterface } from "node:readline";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  EDGE_LAUNCH_ARGS,
  CURRENT_WORK_ENTRY,
  acceptComposerConfirmById,
  edge,
  openCurrentWorkOverlay,
  pw,
  resolvePython,
  currentWorkItemByTitle,
} from "./current-work-overlay-browser-shared.mjs";
import { mountIsolatedUnifiedPage } from "./isolated-unified-browser-mount.mjs";
import { VISUAL_DISCLAIMER } from "./isolated-shell-styles.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const guiOptIn = process.env.CURRENT_WORK_QUIET_DRAWER_GUI_OPT_IN === "1";

const CONFIRM_IDS = [
  "confirm-tool-trash",
  "confirm-post-turn-harness",
  "confirm-verification-harness",
];

async function rejectComposerConfirmById(page, requestId, { timeout = 20_000 } = {}) {
  await page.waitForFunction(
    (id) => document.querySelector(`[data-pending-confirm-hint] [data-confirm="n"][data-id="${CSS.escape(id)}"]`),
    requestId,
    { timeout },
  );
  await page.evaluate((id) => {
    const summary = document.querySelector(
      "[data-pending-confirm-hint] details.unified-pending-confirm-hint-expand > summary",
    );
    if (summary instanceof HTMLElement) summary.click();
    const btn = document.querySelector(`[data-pending-confirm-hint] [data-confirm="n"][data-id="${CSS.escape(id)}"]`);
    btn?.click();
  }, requestId);
  await page.waitForFunction(
    (id) => window.receivedEvents?.some((event) => event.type === "confirm.done"
      && event.request_id === id && event.choice === "n"),
    requestId,
    { timeout },
  );
}

test("companion: scoped composer FIFO confirms + quiet drawer copy", {
  skip: !guiOptIn || process.platform !== "win32" || !existsSync(pw) || !existsSync(edge)
    ? "Win32 opt-in: CURRENT_WORK_QUIET_DRAWER_GUI_OPT_IN=1"
    : false,
  timeout: 180_000,
}, async (t) => {
  const python = resolvePython();
  const { chromium } = createRequire(path.join(desktop, "package.json"))(pw);
  const child = spawn(python, [path.join(here, "current-work-quiet-drawer-browser-fixture.py")], {
    cwd: desktop,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  t.after(() => { if (child.exitCode === null) child.kill(); });
  const lines = createInterface({ input: child.stdout });
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`fixture startup timeout: ${stderr}`)), 25_000);
    lines.once("line", (line) => { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch (e) { reject(e); } });
    child.once("exit", (code) => { clearTimeout(timer); reject(new Error(`fixture exited ${code}: ${stderr}`)); });
  });
  assert.equal(ready.companion_unbound, true);

  const artifactDir = process.env.CW_QUIET_DRAWER_GUI_ARTIFACT_DIR
    || path.join(here, "artifacts", "current-work-quiet-drawer-gui");
  mkdirSync(artifactDir, { recursive: true });

  const browser = await chromium.launch({ headless: true, executablePath: edge, args: EDGE_LAUNCH_ARGS });
  let page;
  let styleReport;
  try {
    const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1280, height: 900 } });
    ({ page, styleReport } = await mountIsolatedUnifiedPage(context, { desktop, port: ready.port }));
    assert.ok(styleReport.ok, `interaction styles: ${styleReport.detail}`);
    console.log(JSON.stringify({
      report: "current-work-quiet-drawer-gui",
      interactionStyles: styleReport,
      visualAuthenticity: "not_claimed",
      visualDisclaimer: VISUAL_DISCLAIMER,
    }));

    const banner = await page.evaluate(() => window.receivedEvents.find((e) => e.type === "session.banner"));
    assert.equal(banner?.project_id ?? "", "");

    const work = await openCurrentWorkOverlay(page);
    await page.screenshot({ path: path.join(artifactDir, "01-overlay-open.png"), fullPage: true });

    assert.equal(await page.locator(".unified-chat-confirm-card").count(), 0);
    await page.waitForFunction(
      (count) => (window.receivedEvents?.filter((e) => e.type === "confirm.request").length ?? 0) >= count,
      CONFIRM_IDS.length,
    );

    const firstPending = await page.evaluate(() => {
      const events = window.receivedEvents ?? [];
      const done = new Set(events.filter((e) => e.type === "confirm.done").map((e) => e.request_id));
      return events.find((e) => e.type === "confirm.request" && !done.has(e.request_id))?.request_id ?? "";
    });
    assert.equal(firstPending, CONFIRM_IDS[0], "FIFO: ordinary tool confirm is first");

    const hint = page.locator("[data-pending-confirm-hint]");
    await hint.waitFor({ state: "visible" });
    const details = hint.locator("details.unified-pending-confirm-hint-expand");
    if (await details.count() > 0) {
      assert.equal(await details.evaluate((el) => el.open), false, "composer confirm details start collapsed");
    }

    assert.ok(await acceptComposerConfirmById(page, CONFIRM_IDS[0]));
    assert.ok(await acceptComposerConfirmById(page, CONFIRM_IDS[1]));
    await rejectComposerConfirmById(page, CONFIRM_IDS[2]);
    assert.equal(await hint.count(), 0, "hint clears after queue drained");

    const entryText = await page.locator(CURRENT_WORK_ENTRY).innerText();
    assert.match(entryText, /需关注/, "actionable attention visible in topbar pill");
    assert.doesNotMatch(entryText, /^34/, "passive unread count does not dominate entry label");

    const item = currentWorkItemByTitle(work, "推进文档");
    await item.waitFor({ state: "visible" });
    assert.match(await item.innerText(), /有结果待看/);
    assert.match(await item.innerText(), /结果待你确认/);
    assert.equal(await work.locator(".unified-current-work-history-fold").count(), 0, "collapsed: no history wall");

    await item.locator("[data-cw-select-item]").click();
    const historyFold = work.locator(".unified-current-work-history-fold");
    await historyFold.waitFor({ state: "visible" });
    assert.equal(await historyFold.evaluate((el) => el.open), false, "history in closed details");

    await historyFold.locator("summary").click();
    const historyText = await historyFold.locator("ul").innerText();
    assert.doesNotMatch(historyText, /host_status|task_id|worker_id|project_worker_host_lifecycle/);
    assert.match(historyText, /待确认|验证/);

    assert.match(await page.evaluate(() => document.querySelector(".unified-shell")?.className ?? ""), /is-current-work-overlay/);
    const chatFlex = await page.evaluate(() => getComputedStyle(document.querySelector(".unified-stage > .unified-chat")).flexBasis);
    assert.notEqual(chatFlex, "50%");

    await page.screenshot({ path: path.join(artifactDir, "02-quiet-drawer-expanded.png"), fullPage: true });
    assert.equal((await page.evaluate(() => window.receivedEvents?.filter((e) => e.type === "confirm.request").length)) ?? 0, 3);

    await page.evaluate(() => { window.dispose(); window.client.destroy(); });
    await context.close();
  } catch (error) {
    const events = page ? await page.evaluate(() => window.receivedEvents) : [];
    throw new Error(`${error.message}\nevents=${JSON.stringify(events)}\nstderr=${stderr}\nstyle=${JSON.stringify(styleReport)}`);
  } finally {
    await browser.close();
    child.stdin.end("\n");
    await new Promise((resolve) => {
      if (child.exitCode !== null) return resolve();
      child.once("exit", resolve);
      setTimeout(() => { child.kill(); resolve(); }, 5000);
    });
    lines.close();
    assert.equal(stderr.trim(), "", stderr);
  }
});
