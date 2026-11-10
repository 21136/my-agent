/**
 * Shared Win32 Playwright flow: companion 当前工作 schedule → real schedule.dispatch.
 * Mock and live-LLM browser tests import this module; confirms use composer strip helpers.
 */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createInterface } from "node:readline";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const here = path.dirname(fileURLToPath(import.meta.url));
export const desktop = path.resolve(here, "..");
export const repo = path.resolve(desktop, "..");
export const PROJECTS = ["mp-host-alpha", "mp-host-beta"];
const CURRENT_WORK_ENTRY = 'button.unified-topbar-current-work[aria-controls="unified-current-work-popover"]';

export const EDGE_LAUNCH_ARGS = [
  "--disable-background-networking",
  "--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessRestrictions,BlockInsecurePrivateNetworkRequests",
  "--no-first-run",
];

export function resolvePython() {
  for (const candidate of [
    path.join(repo, ".venv", "Scripts", "python.exe"),
    path.join(repo, ".venv", "bin", "python"),
    "python3",
  ]) {
    if (!["python3"].includes(candidate) && !existsSync(candidate)) continue;
    const probe = spawnSync(candidate, ["-c", "import websockets, httpx, pytest"], { encoding: "utf8" });
    if (probe.status === 0) return candidate;
  }
  return "python3";
}

export const python = resolvePython();
const require = createRequire(path.join(desktop, "package.json"));
export const pw = process.env.CODEX_PLAYWRIGHT_PATH
  || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
export const edge = process.env.ISOLATED_TEST_BROWSER || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

export function confirmRequestCount(page) {
  return page.evaluate(() => window.receivedEvents?.filter(event => event.type === "confirm.request").length ?? 0);
}

export function scheduleDispatchApplied(page) {
  return page.evaluate(() => {
    const done = window.receivedEvents?.findLast(event => event.type === "work-records.schedule.dispatch.done");
    return done?.status === "applied" && done?.tool_ok === true;
  });
}

export function latestUnsettledConfirmRequestId(page) {
  return page.evaluate(() => {
    const requests = window.receivedEvents?.filter(event => event.type === "confirm.request") ?? [];
    for (let index = requests.length - 1; index >= 0; index -= 1) {
      const id = requests[index]?.request_id;
      if (typeof id !== "string" || !id) continue;
      const settled = window.receivedEvents?.some(event => event.type === "confirm.done"
        && event.request_id === id && ["y", "n", "timeout"].includes(event.choice));
      if (!settled) return id;
    }
    return null;
  });
}

export async function acceptComposerConfirmById(page, requestId, { timeout = 20_000 } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const alreadyDone = await page.evaluate(id => window.receivedEvents?.some(
      event => event.type === "confirm.done" && event.request_id === id && event.choice === "y",
    ), requestId);
    if (alreadyDone) return true;

    const stillTarget = await latestUnsettledConfirmRequestId(page);
    if (stillTarget !== requestId) return false;

    const clickState = await page.evaluate((id) => {
      const btn = document.querySelector(
        `[data-pending-confirm-hint] [data-confirm="y"][data-id="${CSS.escape(id)}"]`,
      );
      if (!(btn instanceof HTMLButtonElement)) return "missing";
      if (btn.disabled) return "disabled";
      const summary = document.querySelector(
        "[data-pending-confirm-hint] details.unified-pending-confirm-hint-expand > summary",
      );
      if (summary instanceof HTMLElement) summary.click();
      btn.scrollIntoView({ block: "nearest", inline: "nearest" });
      btn.click();
      return "clicked";
    }, requestId);

    if (clickState === "clicked") {
      await page.waitForFunction(
        id => window.receivedEvents?.some(
          event => event.type === "confirm.done" && event.request_id === id && event.choice === "y",
        ),
        requestId,
        { timeout: Math.max(deadline - Date.now(), 1_000) },
      );
      return true;
    }

    await page.waitForTimeout(150);
  }
  return false;
}

export async function tryAcceptUnsettledComposerConfirm(page, { timeout = 20_000 } = {}) {
  const requestId = await latestUnsettledConfirmRequestId(page);
  if (!requestId) return false;
  return acceptComposerConfirmById(page, requestId, { timeout });
}

export async function acceptComposerOrdinaryConfirm(page, {
  timeout = 45000,
  minConfirmRequests = 0,
  requireHintClear = true,
} = {}) {
  await page.waitForFunction(
    min => (window.receivedEvents?.filter(event => event.type === "confirm.request").length ?? 0) > min,
    minConfirmRequests,
    { timeout },
  );
  const requestId = await page.evaluate(
    () => window.receivedEvents.filter(event => event.type === "confirm.request").at(-1)?.request_id,
  );
  assert.ok(requestId, "confirm.request missing request_id");
  const accepted = await acceptComposerConfirmById(page, requestId, { timeout });
  assert.ok(accepted, `composer confirm not accepted: ${requestId}`);
  if (requireHintClear) {
    await page.waitForFunction(() => !document.querySelector("[data-pending-confirm-hint]"), null, { timeout });
  }
}

export async function acceptDispatchConfirmsUntilApplied(page, _confirmRequestsSeen, { timeout = 240_000 } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await scheduleDispatchApplied(page)) return;
    const rejected = await page.evaluate(() => {
      const done = window.receivedEvents?.findLast(event => event.type === "work-records.schedule.dispatch.done");
      return done?.status === "rejected" ? done : null;
    });
    if (rejected) {
      throw new Error(`schedule.dispatch.done rejected: ${rejected.message ?? rejected.status}`);
    }
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    const accepted = await tryAcceptUnsettledComposerConfirm(page, {
      timeout: Math.min(remaining, 15_000),
    });
    if (accepted) continue;
    await page.waitForFunction(
      () => {
        const requests = window.receivedEvents?.filter(event => event.type === "confirm.request") ?? [];
        const latestId = requests.at(-1)?.request_id;
        const unsettled = latestId && !window.receivedEvents?.some(event => event.type === "confirm.done"
          && event.request_id === latestId && ["y", "n", "timeout"].includes(event.choice));
        if (unsettled && document.querySelector("[data-pending-confirm-hint]")) return true;
        return window.receivedEvents?.some(event => event.type === "work-records.schedule.dispatch.done"
          && event.status === "applied" && event.tool_ok === true)
          || Boolean(document.querySelector("[data-pending-confirm-hint]"));
      },
      null,
      { timeout: Math.min(remaining, 1_500) },
    ).catch(() => {});
  }
  if (!(await scheduleDispatchApplied(page))) {
    const pending = await latestUnsettledConfirmRequestId(page);
    throw new Error(pending
      ? `schedule.dispatch never applied; unsettled confirm ${pending}`
      : "schedule.dispatch.done never reached applied");
  }
}

async function readCurrentWorkLayout(page) {
  return page.evaluate(() => {
    const describe = el => {
      if (!(el instanceof HTMLElement)) return null;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return {
        hidden: el.hidden,
        rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
        clientHeight: el.clientHeight,
        visibility: style.visibility,
        display: style.display,
      };
    };
    const popover = document.querySelector("#unified-current-work-popover");
    const schedulePanel = document.querySelector('#unified-current-work [data-cw-segment-panel="schedule"]');
    const launch = document.querySelector("#unified-current-work [data-schedule-launch]");
    return { popover: describe(popover), schedulePanel: describe(schedulePanel), launch: describe(launch) };
  });
}

async function waitForCurrentWorkPopoverSized(page, minHeight = 80) {
  await page.waitForFunction(min => {
    const popover = document.querySelector("#unified-current-work-popover");
    if (!(popover instanceof HTMLElement)) return false;
    return popover.getBoundingClientRect().height >= min
      && getComputedStyle(popover).visibility !== "hidden";
  }, minHeight);
}

async function activateScheduleSegment(work, page, { retries = 8 } = {}) {
  const tab = work.locator('[data-cw-segment-select="schedule"]');
  const panel = work.locator('[data-cw-segment-panel="schedule"]:not([hidden])');
  let lastError;
  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      await tab.click();
      await work.locator('[data-current-work-shell][data-cw-segment="schedule"]').waitFor({ state: "attached", timeout: 4000 });
      await panel.waitFor({ state: "visible", timeout: 4000 });
      await page.waitForFunction(() => {
        const el = document.querySelector('#unified-current-work [data-cw-segment-panel="schedule"]:not([hidden])');
        return el instanceof HTMLElement && el.getBoundingClientRect().height > 0;
      });
      return panel;
    } catch (error) {
      lastError = error;
    }
  }
  const layout = await readCurrentWorkLayout(page);
  throw new Error(`${lastError?.message ?? "schedule segment did not become visible"}\nlayout=${JSON.stringify(layout)}`);
}

async function waitForVisibleScheduleLaunch(work, page) {
  await work.locator("[data-schedule-launch]").waitFor({ state: "attached" });
  const panel = await activateScheduleSegment(work, page);
  const launch = panel.locator("[data-schedule-launch]");
  await launch.waitFor({ state: "visible" });
  await page.waitForFunction(() => {
    const el = document.querySelector("#unified-current-work [data-schedule-launch]");
    return el instanceof HTMLElement && el.clientHeight > 0;
  });
  return launch;
}

async function ensureMappedProjectCatalog(page, minProjects = 2) {
  await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "session.banner"));
  await page.evaluate(() => window.client.listProjects());
  await page.waitForFunction(min => {
    const listed = window.receivedEvents?.findLast(e => e.type === "project.list");
    if (!Array.isArray(listed?.projects)) return false;
    return listed.projects.filter(p => typeof p?.session_id === "string" && p.session_id).length >= min;
  }, minProjects);
}

async function openCurrentWorkOverlay(page) {
  await ensureMappedProjectCatalog(page);
  await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "session.list"));
  await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "work-records.overview.result"));
  const entry = page.locator(CURRENT_WORK_ENTRY);
  await entry.waitFor({ state: "visible" });
  await entry.click();
  await page.waitForFunction(() => {
    const host = document.querySelector("#unified-current-work");
    return Boolean(host && !host.hidden && host.querySelector("[data-current-work-shell]"));
  });
  return page.locator("#unified-current-work");
}

/**
 * @param {import('node:test').TestContext} t
 * @param {{
 *   fixtureScriptName: string,
 *   dispatchConfirmTimeout?: number,
 *   assertReady?: (ready: object) => void,
 *   assertWorkerOutcomes: (statuses: Record<string, string>, workers: object[]) => void,
 * }} options
 */
export async function runMultiProjectScheduleRealHostGuiE2e(t, {
  fixtureScriptName,
  dispatchConfirmTimeout = 240_000,
  assertReady = () => {},
  assertWorkerOutcomes,
}) {
  const esbuild = require("esbuild");
  const { chromium } = require(pw);
  const child = spawn(python, [
    path.join(here, fixtureScriptName),
    path.join(repo, "agent-core"),
  ], {
    cwd: desktop,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  t.after(() => { if (child.exitCode === null) child.kill(); });
  const lines = createInterface({ input: child.stdout });
  let stderr = "";
  child.stderr.on("data", chunk => { stderr += chunk; });
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`fixture startup timeout: ${stderr}`)), 25000);
    lines.once("line", line => { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch (e) { reject(e); } });
    child.once("exit", code => { clearTimeout(timer); reject(new Error(`fixture exited ${code}: ${stderr}`)); });
  });
  assertReady(ready);

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
    + readFileSync(path.join(desktop, "src/shells/unified/current-work-overview.css"), "utf8")
    + readFileSync(path.join(desktop, "src/shells/unified/schedule-launch.css"), "utf8");

  const browser = await chromium.launch({ headless: true, executablePath: edge, args: EDGE_LAUNCH_ARGS });
  let page;
  try {
    const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1200, height: 900 } });
    await context.route("**/*", r => r.request().url() === `http://127.0.0.1:${ready.port}/`
      ? r.fulfill({ status: 200, contentType: "text/html", body: `<!doctype html><style>${css}</style><div id="root"></div>` })
      : r.abort());
    page = await context.newPage();
    page.setDefaultTimeout(20000);
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${ready.port}/`);
    await page.evaluate(() => {
      window.wsFrames = [];
      const original = WebSocket.prototype.send;
      WebSocket.prototype.send = function sendWrapped(value) {
        try { window.wsFrames.push(JSON.parse(value)); } catch {}
        return original.call(this, value);
      };
    });
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(async port => {
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      window.client = new UnifiedUnderTest.AgentWsClient("127.0.0.1", port);
      window.receivedEvents = [];
      window.client.onEvent(event => window.receivedEvents.push(event));
      window.dispose = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), window.client);
      await window.client.connect();
    }, ready.port);

    await ensureMappedProjectCatalog(page);
    const bannerEvent = await page.evaluate(() => window.receivedEvents.find(e => e.type === "session.banner"));
    assert.equal(bannerEvent?.project_id ?? "", "");

    const work = await openCurrentWorkOverlay(page);
    const schedulePanel = await activateScheduleSegment(work, page);
    const sourcePanel = schedulePanel.locator("[data-selected-project-overview]");
    await sourcePanel.waitFor({ state: "visible" });

    await page.evaluate(projectIds => {
      const host = document.querySelector("#unified-current-work");
      const panel = host?.querySelector("[data-selected-project-overview]");
      if (!(panel instanceof HTMLElement)) throw new Error("source selector missing in overlay");
      for (const id of projectIds) {
        const input = panel.querySelector(`input[data-project-overview-id="${CSS.escape(id)}"]`);
        if (!(input instanceof HTMLInputElement)) throw new Error(`missing checkbox for ${id}`);
        input.checked = true;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }, PROJECTS);
    await schedulePanel.locator("[data-run-selected-project-overview]").click();
    let confirmRequestsSeen = await confirmRequestCount(page);
    await acceptComposerOrdinaryConfirm(page, { minConfirmRequests: confirmRequestsSeen });
    confirmRequestsSeen = await confirmRequestCount(page);
    await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "work-records.overview.result"
      && e.aggregation_scope === "selected_project_sessions"));
    await waitForCurrentWorkPopoverSized(page, 80);

    const launch = await waitForVisibleScheduleLaunch(work, page);
    await page.waitForFunction(() => !document.querySelector(".unified-shell.is-working"));
    await page.waitForFunction(() => !document.querySelector("[data-pending-confirm-hint]"));
    await launch.locator("[data-schedule-launch-send]").click();
    await acceptDispatchConfirmsUntilApplied(page, confirmRequestsSeen, { timeout: dispatchConfirmTimeout });
    const done = await page.evaluate(() => window.receivedEvents.findLast(e => e.type === "work-records.schedule.dispatch.done"));
    assert.equal(done?.status, "applied");
    assert.equal(done?.tool_ok, true);
    const workers = done.workers ?? [];
    const statuses = Object.fromEntries(workers.map(row => [row.project_id, row.status]));
    assertWorkerOutcomes(statuses, workers);
    assert.equal(await page.evaluate(() => window.receivedEvents.filter(e => e.type === "user.message.persisted").length), 0);
    assert.equal(errors.length, 0, errors.join("\n"));
    await page.evaluate(() => { window.dispose(); window.client.destroy(); });
    await context.close();
  } catch (error) {
    const events = page ? await page.evaluate(() => window.receivedEvents) : [];
    const body = page ? await page.locator("body").innerText() : "";
    const layout = page ? await readCurrentWorkLayout(page) : null;
    throw new Error(`${error.message}\nbody=${body}\nlayout=${JSON.stringify(layout)}\nevents=${JSON.stringify(events)}\nstderr=${stderr}`);
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
}
