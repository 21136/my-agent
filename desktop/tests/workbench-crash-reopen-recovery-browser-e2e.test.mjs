/**
 * Playwright: crash/reopen honest 当前工作 after late WS + WS reconnect (Win32).
 * Simulates tray/cold reopen timing; not real Electron kill (see agent-core harness).
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

function resolvePython() {
  const candidates = [
    path.join(repo, ".venv", "Scripts", "python.exe"),
    path.join(repo, ".venv", "bin", "python"),
    "python3",
  ];
  for (const candidate of candidates) {
    if (candidate !== "python3" && !existsSync(candidate)) continue;
    const probe = spawnSync(candidate, ["-c", "import websockets"], { encoding: "utf8" });
    if (probe.status === 0) return candidate;
  }
  return "python3";
}
const python = resolvePython();
const require = createRequire(path.join(desktop, "package.json"));
const pw = process.env.CODEX_PLAYWRIGHT_PATH
  || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const edge = process.env.ISOLATED_TEST_BROWSER || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

const CURRENT_WORK_ENTRY = 'button.unified-topbar-current-work[aria-controls="unified-current-work-popover"]';

async function activateQueueSegment(work, page) {
  await work.locator('[data-cw-segment-select="queue"]').click();
  await work.locator('[data-cw-segment-panel="queue"]:not([hidden])').waitFor({ state: "visible" });
  await page.waitForFunction(() => {
    const el = document.querySelector('#unified-current-work [data-cw-segment-panel="queue"]:not([hidden])');
    return el instanceof HTMLElement && el.getBoundingClientRect().height > 0;
  });
  return work.locator('[data-cw-segment-panel="queue"]:not([hidden])');
}

test("crash reopen GUI: late WS recovery warn, honest labels, cancel after reconnect", {
  skip: process.platform !== "win32" || !existsSync(pw) || !existsSync(edge),
  timeout: 120000,
}, async t => {
  const esbuild = require("esbuild");
  const { chromium } = require(pw);
  const child = spawn(python, [path.join(here, "workbench-crash-reopen-recovery-fixture.py")], {
    cwd: desktop,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", WORKBENCH_RECOVERY_LATE_WS_MS: "400" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  t.after(() => { if (child.exitCode === null) child.kill(); });
  const lines = createInterface({ input: child.stdout });
  let stderr = "";
  child.stderr.on("data", chunk => { stderr += chunk; });
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`fixture startup timeout: ${stderr}`)), 20000);
    lines.once("line", line => { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch (e) { reject(e); } });
    child.once("exit", code => { clearTimeout(timer); reject(new Error(`fixture exited ${code}: ${stderr}`)); });
  });

  const bundle = await esbuild.build({
    stdin: {
      contents: 'export { mountUnifiedShell } from "./src/shells/unified/index.ts"; export { AgentWsClient } from "./src/api/ws.ts";',
      resolveDir: desktop,
      loader: "ts",
    },
    bundle: true,
    write: false,
    platform: "browser",
    format: "iife",
    globalName: "UnifiedUnderTest",
    define: { "import.meta.url": '"http://127.0.0.1/isolated.js"' },
    loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
    plugins: [{ name: "no-css", setup(b) { b.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }],
    logLevel: "silent",
  });
  const css = readFileSync(path.join(desktop, "src/shells/unified/unified.css"), "utf8")
    + readFileSync(path.join(desktop, "src/shells/unified/current-work-overview.css"), "utf8");

  const browser = await chromium.launch({
    headless: true,
    executablePath: edge,
    args: ["--disable-background-networking",
      "--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessRestrictions,BlockInsecurePrivateNetworkRequests",
      "--no-first-run"],
  });
  let page;
  try {
    const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1200, height: 900 } });
    await context.route("**/*", r => r.request().url() === `http://127.0.0.1:${ready.port}/`
      ? r.fulfill({ status: 200, contentType: "text/html", body: `<!doctype html><style>${css}</style><div id="root"></div>` })
      : r.abort());
    page = await context.newPage();
    page.setDefaultTimeout(25000);
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

    await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "notice" && e.level === "warn"
      && /未自动续跑/.test(e.text ?? "")));
    await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "session.banner"));
    await page.evaluate(() => {
      window.client.queryWorkRecordsOverview({ pageSize: 6, detailPaging: true });
    });
    await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "work-records.overview.result"
      && e.unread_status === "unknown"));

    const entry = page.locator(CURRENT_WORK_ENTRY);
    await entry.waitFor({ state: "visible" });
    await entry.click();
    await page.waitForFunction(() => {
      const host = document.querySelector("#unified-current-work");
      return Boolean(host && !host.hidden && host.querySelector("[data-current-work-shell]"));
    });
    const work = page.locator("#unified-current-work");
    const body = await work.innerText();
    assert.match(body, /结果待确认/);
    assert.match(body, /执行状态待确认/);
    assert.match(body, /crashed worker task\s*\n\s*结果待确认/);
    assert.doesNotMatch(body, /当前记录版本不可用/);

    const queuePanel = await activateQueueSegment(work, page);
    const projectItem = queuePanel.locator(".unified-current-work-item-head").filter({ hasText: "project worker task" });
    await projectItem.click();
    const cancel = queuePanel.locator('[data-action="cancel_execution"][data-expected-status="in_progress"]').first();
    await cancel.waitFor({ state: "visible" });
    await cancel.click();
    await page.waitForFunction(() => window.wsFrames?.some(e => e.type === "work-record.control"
      && e.action === "cancel_execution"));

    await page.evaluate(() => {
      const ws = window.client.ws;
      if (ws && ws.readyState === WebSocket.OPEN) ws.close();
    });
    await page.waitForFunction(() => window.receivedEvents?.filter(e => e.type === "notice" && e.level === "warn").length >= 2);
    await page.waitForFunction(() => !document.querySelector(".unified-shell.is-working"));
    await page.waitForFunction(() => {
      const status = document.querySelector("#unified-status")?.textContent ?? "";
      return !/处理中|执行中/.test(status) || /未自动续跑|异常退出/.test(status);
    });

    assert.deepEqual(errors, []);
    await page.evaluate(() => { window.dispose(); window.client.destroy(); });
    await context.close();
  } catch (error) {
    const events = page ? await page.evaluate(() => window.receivedEvents) : [];
    const body = page ? await page.locator("body").innerText() : "";
    throw new Error(`${error.message}\nbody=${body}\nevents=${JSON.stringify(events)}\nstderr=${stderr}`);
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
