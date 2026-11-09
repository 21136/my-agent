import assert from "node:assert/strict";
import { spawn } from "node:child_process";
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
const python = existsSync(path.join(repo, ".venv", "Scripts", "python.exe"))
  ? path.join(repo, ".venv", "Scripts", "python.exe")
  : "python3";
const require = createRequire(path.join(desktop, "package.json"));
const pw = process.env.CODEX_PLAYWRIGHT_PATH || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const edge = process.env.ISOLATED_TEST_BROWSER || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

test("schedule launcher browser click sends work-records.schedule.dispatch without running an Agent/worker", {
  skip: process.platform !== "win32" || !existsSync(pw) || !existsSync(edge), timeout: 90000,
}, async t => {
  const esbuild = require("esbuild");
  const { chromium } = require(pw);
  const child = spawn(python, [path.join(here, "schedule-launch-browser-fixture.py")], {
    cwd: desktop, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1" }, stdio: ["pipe", "pipe", "pipe"],
  });
  t.after(() => { if (child.exitCode === null) child.kill(); });
  const lines = createInterface({ input: child.stdout });
  let stderr = ""; child.stderr.on("data", chunk => stderr += chunk);
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`fixture startup timeout: ${stderr}`)), 20000);
    lines.once("line", line => { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch (e) { reject(e); } });
    child.once("exit", code => { clearTimeout(timer); reject(new Error(`fixture exited ${code}: ${stderr}`)); });
  });
  const bundle = await esbuild.build({
    stdin: { contents: 'export { mountUnifiedShell } from "./src/shells/unified/index.ts"; export { AgentWsClient } from "./src/api/ws.ts";', resolveDir: desktop, loader: "ts" },
    bundle: true, write: false, platform: "browser", format: "iife", globalName: "UnifiedUnderTest",
    define: { "import.meta.url": '"http://127.0.0.1/isolated.js"' }, loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
    plugins: [{ name: "no-css", setup(b) { b.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }], logLevel: "silent",
  });
  const css = readFileSync(path.join(desktop, "src/shells/unified/unified.css"), "utf8");
  const browser = await chromium.launch({ headless: true, executablePath: edge, args: ["--disable-background-networking", "--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessRestrictions,BlockInsecurePrivateNetworkRequests", "--no-first-run"] });
  let page;
  try {
    const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1100, height: 800 } });
    await context.route("**/*", r => r.request().url() === `http://127.0.0.1:${ready.port}/`
      ? r.fulfill({ status: 200, contentType: "text/html", body: '<!doctype html><style>' + css + '</style><div id="root"></div>' }) : r.abort());
    page = await context.newPage(); page.setDefaultTimeout(15000);
    const errors = []; page.on("pageerror", e => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${ready.port}/`);
    await page.evaluate(() => {
      window.wsFrames = [];
      const original = WebSocket.prototype.send;
      WebSocket.prototype.send = function (value) { try { window.wsFrames.push(JSON.parse(value)); } catch {} return original.call(this, value); };
    });
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(async port => {
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      window.client = new UnifiedUnderTest.AgentWsClient("127.0.0.1", port);
      window.receivedEvents = []; window.client.onEvent(event => window.receivedEvents.push(event));
      window.dispose = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), window.client);
      await window.client.connect();
    }, ready.port);
    await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "session.banner")
      && window.receivedEvents?.some(e => e.type === "project.list"));
    await page.locator("#unified-request-current-work").click();
    await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "work-records.overview.result"));
    const overview = page.locator("[data-schedule-launch]");
    await overview.waitFor({ state: "attached" });
    const draft = "composer draft stays exactly as typed";
    await page.locator("#unified-input").fill(draft);
  const task = "Synthetic queued requirement; must not execute";
    await page.locator("[data-schedule-write]").first().fill("../outside");
    await page.locator("[data-schedule-launch-send]").click();
    await page.waitForFunction(() => (document.querySelector("#unified-status")?.innerText || "").includes("启动请求未发送"));
    assert.equal(await page.locator("#unified-input").inputValue(), draft, "failed validation keeps composer draft intact");
    assert.equal(await page.evaluate(() => window.wsFrames.filter(e => e.type === "work-records.schedule.dispatch").length), 0);
    await page.locator("[data-schedule-task]").first().inputValue(); // prefilled
    await page.locator("[data-schedule-read]").first().inputValue();
    await page.locator("[data-schedule-write]").first().fill("NOTES.md");
    await page.locator("[data-schedule-launch-send]").click();
    await page.waitForFunction(() => window.receivedEvents?.some(e => e.type === "work-records.schedule.dispatch.done"));
    const dispatch = await page.evaluate(() => window.wsFrames.findLast(e => e.type === "work-records.schedule.dispatch"));
    assert.equal(dispatch.session_id, "schedule-launch-browser-session");
    assert.equal(dispatch.workers[0].task, task);
    assert.deepEqual(dispatch.workers[0].write_targets, ["NOTES.md"]);
    assert.equal(await page.evaluate(() => window.receivedEvents.filter(e => e.type === "user.message.persisted").length), 0);
    assert.deepEqual(errors, []);
    await page.evaluate(() => { window.dispose(); window.client.destroy(); });
    await context.close();
  } catch (error) {
    throw new Error(`${error.message}\nbody=${await page.locator("body").innerText()}\nevents=${JSON.stringify(await page.evaluate(() => window.receivedEvents))}\nstderr=${stderr}`);
  } finally {
    await browser.close(); child.stdin.end("\n");
    await new Promise(resolve => { if (child.exitCode !== null) return resolve(); child.once("exit", resolve); setTimeout(() => { child.kill(); resolve(); }, 5000); });
    lines.close(); assert.equal(stderr.trim(), "", stderr);
  }
});
