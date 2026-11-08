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
const python = path.join(repo, ".venv", "Scripts", "python.exe");
const require = createRequire(path.join(desktop, "package.json"));
const pw = process.env.CODEX_PLAYWRIGHT_PATH || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const edge = process.env.ISOLATED_TEST_BROWSER || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

test("real user.message worker batch displays distinct success and failure in desktop UI", {
  skip: process.platform !== "win32" || !existsSync(python) || !existsSync(pw) || !existsSync(edge), timeout: 90000,
}, async t => {
  const esbuild = require("esbuild");
  const { chromium } = require(pw);
  const fixture = path.join(here, "project-worker-batch-browser-fixture.py");
  const child = spawn(python, [fixture, path.join(repo, "agent-core")], {
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
  try {
    const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1100, height: 800 } });
    await context.route("**/*", r => r.request().url() === `http://127.0.0.1:${ready.port}/`
      ? r.fulfill({ status: 200, contentType: "text/html", body: '<!doctype html><style>' + css + '</style><div id="root"></div>' }) : r.abort());
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    const errors = []; page.on("pageerror", e => errors.push(e.message));
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
    const input = page.locator("#unified-input");
    await input.fill("Run isolated worker batch");
    await page.locator("#unified-send").click();
    await page.waitForFunction(() => window.receivedEvents?.some(event => event.type === "user.message.persisted"));
    const persisted = await page.evaluate(() => window.receivedEvents.filter(event => event.type === "user.message.persisted"));
    assert.equal(persisted.length, 1, "one composer submission reaches the real server persistence event");
    assert.equal(persisted[0].request_id?.length, 36);
    const chat = page.locator("#unified-chat");
    // Resolve only actual prompts after checking their rendered disclosure in
    // the browser. Clicks go through the desktop UI's real confirmation route.
    try {
      await page.waitForFunction(() => window.receivedEvents?.some(event => event.type === "confirm.request"), null, { timeout: 30000 });
      const firstPrompt = await page.evaluate(() => window.receivedEvents.find(event => event.type === "confirm.request"));
      assert.ok(firstPrompt.preview.startsWith("CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM\n"));
      await page.locator(`[data-id="${firstPrompt.request_id}"][data-confirm="y"]`).click();
      const batch = page.locator('[data-project-worker-batch-confirm]');
      await batch.waitFor({ state: "visible", timeout: 30000 });
      assert.match(await batch.innerText(), /browser-batch-success/);
      assert.match(await batch.innerText(), /browser-batch-failure/);
      await batch.locator('button[data-confirm="y"]').click();
      await page.waitForFunction(() => {
        const text = document.querySelector("#unified-chat")?.innerText || "";
        return text.includes("browser-batch-success") && text.includes("browser-batch-failure");
      }, null, { timeout: 60000 });
    } catch (error) { throw new Error(`${error.message}\\nbody=${await page.locator("body").innerText()}\\nevents=${JSON.stringify(await page.evaluate(() => window.receivedEvents))}\\nstderr=${stderr}`); }
    const visible = await chat.innerText();
    assert.match(visible, /browser-batch-success model_turn_finished/);
    assert.match(visible, /browser-batch-failure failed/);
    assert.equal(errors.length, 0, errors.join("\n"));
    await page.evaluate(() => { window.dispose(); window.client.destroy(); });
    await context.close();
  } finally {
    await browser.close(); child.stdin.end("\n");
    await new Promise(resolve => { if (child.exitCode !== null) return resolve(); child.once("exit", resolve); setTimeout(() => { child.kill(); resolve(); }, 5000); });
    lines.close(); assert.equal(stderr.trim(), "", stderr);
  }
});
