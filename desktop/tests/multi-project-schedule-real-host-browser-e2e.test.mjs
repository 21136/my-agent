/**
 * Win32 Playwright: companion 当前工作 schedule tab → real schedule.dispatch → real host.
 * Mock model HTTP only. Opt-in: REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN=1
 */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createInterface } from "node:readline";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const repo = path.resolve(desktop, "..");
const PROJECTS = ["mp-host-alpha", "mp-host-beta"];
const CURRENT_WORK_ENTRY = 'button.unified-topbar-current-work[aria-controls="unified-current-work-popover"]';

function resolvePython() {
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
const python = resolvePython();
const require = createRequire(path.join(desktop, "package.json"));
const pw = process.env.CODEX_PLAYWRIGHT_PATH
  || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const edge = process.env.ISOLATED_TEST_BROWSER || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

const guiOptIn = process.env.REAL_MULTI_PROJECT_SCHEDULE_OPT_IN === "1"
  && process.env.REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN === "1";

async function approvePendingConfirms(page, rounds = 24) {
  for (let i = 0; i < rounds; i++) {
    const approve = page.locator('[data-confirm="y"]:not([disabled])');
    if (await approve.count() === 0) return;
    await approve.last().click();
    await page.waitForTimeout(120);
  }
}

test("companion schedule GUI: two projects, real host dispatch, distinct outcomes", {
  skip: !guiOptIn || process.platform !== "win32" || !existsSync(pw) || !existsSync(edge)
    ? "Win32 opt-in: REAL_MULTI_PROJECT_SCHEDULE_OPT_IN=1 and REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN=1"
    : false,
  timeout: 180000,
}, async t => {
  const esbuild = require("esbuild");
  const { chromium } = require(pw);
  const child = spawn(python, [
    path.join(here, "multi-project-schedule-real-host-fixture.py"),
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

  const browser = await chromium.launch({ headless: true, executablePath: edge,
    args: ["--disable-background-networking", "--no-first-run"] });
  let page;
  try {
    const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1200, height: 900 } });
    await context.route("**/*", r => r.request().url() === `http://127.0.0.1:${ready.port}/`
      ? r.fulfill({ status: 200, contentType: "text/html", body: `<!doctype html><style>${css}</style><div id="root"></div>` })
      : r.abort());
    page = await context.newPage();
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

    const banner = await page.waitForFunction(() => window.receivedEvents?.find(e => e.type === "session.banner"));
    const bannerEvent = await banner.jsonValue();
    assert.equal(bannerEvent.project_id ?? "", "");

    const entry = page.locator(CURRENT_WORK_ENTRY);
    await entry.waitFor({ state: "visible" });
    await entry.click();
    const work = page.locator("#unified-current-work");
    await work.locator('[data-cw-segment-select="schedule"]').click();
    const schedulePanel = work.locator('[data-cw-segment-panel="schedule"]:not([hidden])');
    await schedulePanel.waitFor({ state: "visible" });

    await page.evaluate(projectIds => {
      const panel = document.querySelector('[data-selected-project-overview]');
      for (const id of projectIds) {
        const input = panel.querySelector(`input[data-project-overview-id="${CSS.escape(id)}"]`);
        input.checked = true;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }, PROJECTS);
    await schedulePanel.locator("[data-run-selected-project-overview]").click();
    await approvePendingConfirms(page);
    await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "work-records.overview.result"
      && e.aggregation_scope === "selected_project_sessions"));

    const launch = schedulePanel.locator("[data-schedule-launch]");
    await launch.waitFor({ state: "visible" });
    await launch.locator("[data-schedule-launch-send]").click();
    await approvePendingConfirms(page, 40);

    await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "work-records.schedule.dispatch.done"
      && e.status === "applied" && e.tool_ok === true), null, { timeout: 120000 });
    const done = await page.evaluate(() => window.receivedEvents.findLast(e => e.type === "work-records.schedule.dispatch.done"));
    const workers = done.workers ?? [];
    const statuses = Object.fromEntries(workers.map(row => [row.project_id, row.status]));
    assert.equal(statuses["mp-host-alpha"], "model_turn_finished");
    assert.equal(statuses["mp-host-beta"], "failed");
    assert.equal(await page.evaluate(() => window.receivedEvents.filter(e => e.type === "user.message.persisted").length), 0);
    assert.equal(errors.length, 0, errors.join("\n"));
    await page.evaluate(() => { window.dispose(); window.client.destroy(); });
    await context.close();
  } catch (error) {
    throw new Error(`${error.message}\nbody=${page ? await page.locator("body").innerText() : ""}\nstderr=${stderr}`);
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
