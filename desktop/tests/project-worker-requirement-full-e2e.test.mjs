import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const require = createRequire(import.meta.url);
const desktopRoot = fileURLToPath(new URL("../", import.meta.url));
const repoRoot = path.resolve(desktopRoot, "..");
const python = path.resolve(repoRoot, "..", "..", ".venv", "Scripts", "python.exe");
const playwrightPath = process.env.CODEX_PLAYWRIGHT_PATH || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const browserPath = process.env.ISOLATED_TEST_BROWSER || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

test("mounted single-chat approval traverses loopback, real host and pytest, then persists only a self-report", {
  skip: process.platform !== "win32" || !existsSync(python) || !existsSync(playwrightPath) || !existsSync(browserPath),
  timeout: 90000,
}, async t => {
  const esbuild = require("esbuild");
  const { chromium } = require(playwrightPath);
  const fixture = path.join(desktopRoot, "tests/project-worker-requirement-full-e2e-fixture.py");
  const core = path.join(repoRoot, "agent-core");
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1" };
  const child = spawn(python, [fixture, core], { cwd: desktopRoot, env, stdio: ["pipe", "pipe", "pipe"] });
  t.after(() => { if (child.exitCode === null) child.kill(); });
  const stdout = createInterface({ input: child.stdout });
  let stderr = ""; child.stderr.on("data", chunk => stderr += chunk);
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`backend fixture startup timeout: ${stderr}`)), 15000);
    stdout.once("line", line => { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch (error) { reject(error); } });
    child.once("exit", code => { clearTimeout(timer); reject(new Error(`backend fixture exited ${code}: ${stderr}`)); });
  });
  assert.ok(ready.port > 0 && ![5173, 5174, 8000, 8765].includes(ready.port));
  const bundle = await esbuild.build({
    stdin: { contents: 'export { mountUnifiedShell } from "./src/shells/unified/index.ts"; export { AgentWsClient } from "./src/api/ws.ts";', resolveDir: desktopRoot, loader: "ts" },
    bundle: true, write: false, platform: "browser", format: "iife", globalName: "UnifiedUnderTest",
    define: { "import.meta.url": '"http://127.0.0.1/isolated.js"' },
    loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
    plugins: [{ name: "no-css-imports", setup(build) { build.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }],
    logLevel: "silent",
  });
  const css = readFileSync(path.join(desktopRoot, "src/shells/unified/unified.css"), "utf8");
  const browser = await chromium.launch({ headless: true, executablePath: browserPath,
    args: ["--disable-background-networking", "--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessRestrictions,BlockInsecurePrivateNetworkRequests", "--no-first-run"] });
  try {
    const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1100, height: 800 } });
    await context.route("**/*", route => route.request().url() === `http://127.0.0.1:${ready.port}/`
      ? route.fulfill({ status: 200, contentType: "text/html", body: '<!doctype html><div id="root"></div>' }) : route.abort());
    const page = await context.newPage(); page.setDefaultTimeout(12000);
    const errors = []; page.on("pageerror", error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${ready.port}/`);
    await page.addStyleTag({ content: css }); await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(async port => {
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      window.client = new UnifiedUnderTest.AgentWsClient("127.0.0.1", port);
      window.received = []; window.client.onEvent(event => window.received.push(event));
      window.dispose = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), window.client);
      await window.client.connect();
    }, ready.port);
    const card = page.locator("[data-project-worker-verification]");
    // Approve only intermediate real-host prompts; stop at the target-pytest decision.
    for (let step = 0; step < 8 && await card.count() === 0; step++) {
      const approve = page.locator('[data-confirm="y"]:not([disabled])').last();
      await approve.waitFor({ state: "visible" });
      await approve.click();
      await page.waitForTimeout(100);
    }
    try { await card.waitFor({ state: "visible" }); }
    catch (error) { throw new Error(`${error.message}\\nreceived=${JSON.stringify(await page.evaluate(() => window.received))}\\nbody=${await page.locator("body").innerText()}\\nstderr=${stderr}`); }
    assert.match(await card.textContent(), /Feature works/);
    assert.match(await card.textContent(), /test_feature\.py::test_target/);
    assert.equal(await card.locator('[data-confirm="a"]').count(), 0);
    await card.locator('[data-confirm="y"]').click();
    const result = await page.waitForFunction(() => window.received.find(event => event.type === "test.result" || event.type === "test.failure"), null, { timeout: 60000 });
    const event = await result.jsonValue();
    assert.equal(event.type, "test.result", JSON.stringify(event));
    assert.equal(event.pytest_marker, "real pytest ran");
    assert.equal(event.mocked_model_requests, 1);
    assert.equal(event.row.status, "unknown");
    assert.equal(event.row.verification.status, "not_verified");
    assert.deepEqual(event.row.verification.project_test, {
      status: "passed", source: "user_authorized_project_self_report", pytest_target: "test_feature.py::test_target", exit_code: 0,
    });
    assert.equal(errors.length, 0, errors.join("\n"));
    await page.evaluate(() => { window.dispose(); window.client.destroy(); });
    await context.close();
  } finally {
    await browser.close();
    child.stdin.end("\n");
    await new Promise(resolve => {
      if (child.exitCode !== null) return resolve();
      child.once("exit", resolve); setTimeout(() => { child.kill(); resolve(); }, 5000);
    });
    stdout.close();
    assert.equal(stderr.trim(), "", stderr);
  }
});

