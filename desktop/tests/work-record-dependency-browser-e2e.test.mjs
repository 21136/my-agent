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

test("browser saves cross-item dependency only after server rejection/approval and server blocks premature start", {
  skip: process.platform !== "win32" || !existsSync(python) || !existsSync(pw) || !existsSync(edge), timeout: 90000,
}, async t => {
  const esbuild = require("esbuild");
  const { chromium } = require(pw);
  const child = spawn(python, [path.join(here, "work-record-dependency-browser-fixture.py"), path.join(repo, "agent-core")], {
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
    const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1200, height: 900 } });
    await context.route("**/*", r => r.request().url() === `http://127.0.0.1:${ready.port}/`
      ? r.fulfill({ status: 200, contentType: "text/html", body: '<!doctype html><style>' + css + '</style><div id="root"></div>' }) : r.abort());
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    const errors = []; page.on("pageerror", e => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${ready.port}/`);
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(async port => {
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      window.client = new UnifiedUnderTest.AgentWsClient("127.0.0.1", port);
      window.sentControls = [];
      const send = window.client.send.bind(window.client);
      window.client.send = message => { if (message?.type === "work-record.control") window.sentControls.push(message); return send(message); };
      window.receivedEvents = []; window.client.onEvent(event => window.receivedEvents.push(event));
      window.dispose = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), window.client);
      await window.client.connect();
    }, ready.port);
    await page.evaluate(sessionId => { window.fixtureSessionId = sessionId; return window.client.openSession(sessionId); }, ready.session_id);
    await page.waitForFunction(() => window.client.currentConversationId === window.fixtureSessionId);
    await page.evaluate(() => window.client.queryWorkRecordsOverview());
    await page.waitForFunction(() => window.receivedEvents.some(event => event.type === "work-records.overview.result"));
    await page.locator("#unified-request-current-work").click();
    const item = page.locator(".unified-current-work-item").filter({ has: page.locator("strong").getByText("Synthetic dependent item", { exact: true }) });
    assert.ok(await item.count(), `dependent item missing; body=${await page.locator("body").innerText()}`);
    await item.locator("details.unified-current-work-requirements > summary").click();
    const dependency = item.locator("input[data-work-dependency-ref-item]").first();
    assert.ok(await dependency.count(), `dependency editor missing; item=${await item.innerHTML()}`);
    assert.equal(await dependency.getAttribute("data-work-dependency-ref-item"), ready.source_item);
    assert.equal(await dependency.getAttribute("data-work-dependency-ref-requirement"), ready.source_requirement);
    await dependency.check();
    await item.getByRole("button", { name: "保存依赖" }).click();
    await page.waitForFunction(() => window.receivedEvents.some(event => event.type === "confirm.request"));
    const rejectPrompt = await page.evaluate(() => window.receivedEvents.filter(event => event.type === "confirm.request").at(-1));
    await page.locator(`[data-id="${rejectPrompt.request_id}"][data-confirm="n"]`).click();
    await page.waitForFunction(() => window.receivedEvents.some(event => event.type === "work-record.control.done" && event.status === "rejected"));
    const rejectedDone = await page.evaluate(() => window.receivedEvents.filter(event => event.type === "work-record.control.done").at(-1));
    assert.equal(rejectedDone.status, "rejected");
    await page.waitForFunction(() => !document.querySelector('[data-work-dependency-ref-item]')?.checked);

    const overviewCountBeforeRefresh = await page.evaluate(() => window.receivedEvents.filter(event => event.type === "work-records.overview.result").length);
    await page.evaluate(() => window.client.queryWorkRecordsOverview());
    await page.waitForFunction(previous => window.receivedEvents.filter(event => event.type === "work-records.overview.result").length > previous, overviewCountBeforeRefresh);
    const refreshedItem = page.locator(".unified-current-work-item").filter({ has: page.locator("strong").getByText("Synthetic dependent item", { exact: true }) });
    await refreshedItem.locator("details.unified-current-work-requirements > summary").click();
    const refreshedDependency = refreshedItem.locator("input[data-work-dependency-ref-item]").first();
    assert.equal(await refreshedDependency.getAttribute("data-work-dependency-ref-item"), ready.source_item);
    assert.equal(await refreshedDependency.getAttribute("data-work-dependency-ref-requirement"), ready.source_requirement);
    await refreshedDependency.check();
    assert.equal(await refreshedDependency.isChecked(), true);
    await refreshedItem.getByRole("button", { name: "保存依赖" }).click();
    await page.waitForFunction(() => window.receivedEvents.filter(event => event.type === "confirm.request").length >= 2);
    const prompt = await page.evaluate(() => window.receivedEvents.filter(event => event.type === "confirm.request").at(-1));
    await page.locator(`[data-id="${prompt.request_id}"][data-confirm="y"]`).click();
    await page.waitForFunction(() => window.receivedEvents.some(event => event.type === "work-record.control.done" && event.status === "applied"));
    const overviewCountBeforeApprovedRefresh = await page.evaluate(() => window.receivedEvents.filter(event => event.type === "work-records.overview.result").length);
    await page.evaluate(() => window.client.queryWorkRecordsOverview());
    await page.waitForFunction(previous => window.receivedEvents.filter(event => event.type === "work-records.overview.result").length > previous, overviewCountBeforeApprovedRefresh);
    const latest = await page.evaluate(() => window.receivedEvents.filter(event => event.type === "work-records.overview.result").at(-1));
    const sentControls = await page.evaluate(() => window.sentControls);
    assert.deepEqual(sentControls.at(-1).depends_on_refs, [{ item_id: ready.source_item, requirement_id: ready.source_requirement }]);
    const projectedTarget = latest.groups.flatMap(group => group.items).find(row => row.title === "Synthetic dependent item");
    assert.ok(projectedTarget.requirements[0].depends_on_refs.some(ref => ref.item_id === ready.source_item && ref.requirement_id === ready.source_requirement), `persisted projection=${JSON.stringify(projectedTarget.requirements[0])}; controls=${JSON.stringify(await page.evaluate(() => window.receivedEvents.filter(event => event.type === "work-record.control.done")))}`);
    assert.equal(errors.length, 0, errors.join("\n"));
    await page.evaluate(() => { window.dispose(); window.client.destroy(); });
    await context.close();
  } finally {
    await browser.close(); child.stdin.end("\n");
    await new Promise(resolve => { if (child.exitCode !== null) return resolve(); child.once("exit", resolve); setTimeout(() => { child.kill(); resolve(); }, 5000); });
    lines.close(); assert.equal(stderr.trim(), "", stderr);
  }
});
