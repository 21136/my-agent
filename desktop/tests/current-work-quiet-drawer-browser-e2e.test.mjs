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
  firstUnsettledConfirmRequestId,
  readComposerConfirmDiagnostics,
  edge,
  openCurrentWorkOverlay,
  pw,
  resolvePython,
  currentWorkContractMirror,
  currentWorkQueueBody,
  currentWorkItemByTitle,
  currentWorkItemHistoryFold,
} from "./current-work-overlay-browser-shared.mjs";
import { mountIsolatedUnifiedPage } from "./isolated-unified-browser-mount.mjs";
import { VISUAL_DISCLAIMER } from "./isolated-shell-styles.mjs";
import { verifyInteractionStyles, writeGuiHarnessArtifacts } from "./isolated-gui-artifacts.mjs";

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
  /** @type {Awaited<ReturnType<typeof verifyInteractionStyles>> | null} */
  let styleReport = null;
  try {
    const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1280, height: 900 } });
    ({ page } = await mountIsolatedUnifiedPage(context, { desktop, port: ready.port }));
    const styleReport = await verifyInteractionStyles(page, artifactDir, "00-mount");
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

    assert.equal(await firstUnsettledConfirmRequestId(page), CONFIRM_IDS[0], "FIFO: ordinary tool confirm is first");
    const livePreview = await work.locator(".cw-live-preview").first().innerText();
    assert.doesNotMatch(livePreview, /ids=|harness-demo/i, "live queue must not echo fixture internal ids");

    const hint = page.locator("[data-pending-confirm-hint]");
    await hint.waitFor({ state: "visible" });
    const details = hint.locator("details.unified-pending-confirm-hint-expand");
    if (await details.count() > 0) {
      assert.equal(await details.evaluate((el) => el.open), false, "composer confirm details start collapsed");
    }

    if (!(await acceptComposerConfirmById(page, CONFIRM_IDS[0]))) {
      const diag = await readComposerConfirmDiagnostics(page, CONFIRM_IDS[0]);
      assert.fail(`composer accept failed: ${JSON.stringify(diag)}`);
    }
    assert.equal(await firstUnsettledConfirmRequestId(page), CONFIRM_IDS[1]);
    await page.waitForFunction(
      (id) => document.querySelector(`[data-pending-confirm-hint] [data-confirm="y"][data-id="${CSS.escape(id)}"]`),
      CONFIRM_IDS[1],
      { timeout: 10_000 },
    );
    if (!(await acceptComposerConfirmById(page, CONFIRM_IDS[1]))) {
      const diag = await readComposerConfirmDiagnostics(page, CONFIRM_IDS[1]);
      assert.fail(`composer accept failed: ${JSON.stringify(diag)}`);
    }
    assert.equal(await firstUnsettledConfirmRequestId(page), CONFIRM_IDS[2]);
    await page.waitForFunction(
      (id) => document.querySelector(`[data-pending-confirm-hint] [data-confirm="y"][data-id="${CSS.escape(id)}"]`),
      CONFIRM_IDS[2],
      { timeout: 10_000 },
    );
    await rejectComposerConfirmById(page, CONFIRM_IDS[2]);
    assert.equal(await hint.count(), 0, "hint clears after queue drained");
    assert.equal(
      await page.evaluate(() => window.receivedEvents?.filter((e) => e.type === "confirm.done").length ?? 0),
      3,
      "each confirm.request received matching confirm.done",
    );
    // Fixture leaves execution.state running (no turn.end); status/composer「处理中…」is harness-only, not a product defect.
    assert.equal(
      await page.evaluate(() => window.receivedEvents?.some(
        (e) => e.type === "execution.state" && e.state === "running",
      )),
      true,
    );

    const entryText = await page.locator(CURRENT_WORK_ENTRY).innerText();
    assert.match(entryText, /需关注/, "actionable attention visible in topbar pill");
    assert.doesNotMatch(entryText, /^34/, "passive unread count does not dominate entry label");

    const item = currentWorkItemByTitle(work, "推进文档");
    await item.waitFor({ state: "visible" });
    assert.match(await item.innerText(), /有结果待看/);
    assert.match(await item.innerText(), /结果待你确认/);
    assert.equal(await currentWorkItemHistoryFold(work, "推进文档").count(), 0, "collapsed: no history wall");

    await item.locator("[data-cw-select-item]").click();
    const contractMirror = currentWorkContractMirror(work);
    await page.waitForFunction(() => {
      const mirror = document.querySelector("#unified-current-work .cw-contract-body");
      return mirror instanceof HTMLElement
        && mirror.hidden
        && getComputedStyle(mirror).display === "none";
    });
    assert.equal(await currentWorkQueueBody(work).locator(".unified-current-work-history-fold").count(), 1,
      "visible queue exposes one history fold");
    assert.equal(await contractMirror.locator(".unified-current-work-history-fold").count(), 1,
      "cw-contract-body holds hidden contract mirror only");

    const historyFold = currentWorkItemHistoryFold(work, "推进文档");
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
    if (page) {
      const composerConfirm = await readComposerConfirmDiagnostics(page, CONFIRM_IDS[0]).catch(() => null);
      await writeGuiHarnessArtifacts(page, artifactDir, "failure", {
        styleReport: styleReport ?? null,
        extra: { composerConfirm },
      }).catch(() => {});
    }
    const events = page ? await page.evaluate(() => window.receivedEvents) : [];
    throw new Error(`${error.message}\nartifacts=${artifactDir}\nevents=${JSON.stringify(events)}\nstderr=${stderr}\nstyle=${JSON.stringify(styleReport)}`);
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
