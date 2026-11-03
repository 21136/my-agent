import assert from "node:assert/strict";
import test from "node:test";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const ts = createRequire(import.meta.url)("typescript");
const cache = new Map();
function load(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const exports = {}; cache.set(url.href, exports);
  const js = ts.transpileModule(readFileSync(url, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function("exports", "require", js)(exports, id => { assert.ok(id.startsWith(".")); return load(new URL(id.endsWith(".ts") ? id : `${id}.ts`, url)); });
  return exports;
}
const preview = load(new URL("../src/shells/unified/confirm-preview.ts", import.meta.url));
const { createChatSession } = load(new URL("../src/shells/chat-state.ts", import.meta.url));
const data = {
  session_id: "session-a", project_id: "project-a", item_id: "item-a", requirement_id: "req-a",
  project_name: "Project <A>", requirement_text: "Requirement <script>", pytest_target: "tests/test_one.py::test_case",
  worker_run_id: "worker-run-a", reservation_id: "reservation-a",
};
const request = (patch = {}) => ({
  type: "confirm.request", confirm_subtype: "project_worker_requirement_verification",
  request_id: "confirm-a", session_id: "session-a", run_id: "parent-run-a", preview: "structured verification request",
  allow_approve_all: false, project_worker_verification: data, ...patch,
});
const banner = session_id => ({ type: "session.banner", session_id, topics: [], turn_mode: "agent", turn_mode_label: "", phase: "S4" });
const chat = () => { const c = createChatSession({ showProcess: true, confirmInBlocks: false }); c.handleEvent(banner("session-a")); c.handleEvent({ type: "execution.state", run_id: "parent-run-a", state: "running", sequence: 1 }); return c; };

test("requirement verification payload is exact, complete, and session-bound", () => {
  assert.deepEqual(preview.parseProjectWorkerVerification(data, "session-a"), data);
  for (const patch of [{ session_id: "session-b" }, { project_id: "" }, { requirement_text: " " }, { pytest_target: undefined }, { extra: true }]) {
    assert.equal(preview.parseProjectWorkerVerification({ ...data, ...patch }, "session-a"), null, JSON.stringify(patch));
  }
});

test("confirmation appears in chat and escapes all project-owned text with an explicit execution risk", () => {
  const html = preview.renderConfirmCardHtml("后端预览 <script> --target", '<button data-confirm="y">Allow</button>', {
    confirmSubtype: "project_worker_requirement_verification", sessionId: "session-a", projectWorkerVerification: data,
  });
  assert.match(html, /Project &lt;A&gt;/);
  assert.match(html, /Requirement &lt;script&gt;/);
  assert.match(html, /tests\/test_one\.py::test_case/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /运行单目标测试/);
  assert.match(html, /结果仅记录为用户授权目标测试的自报告测试结果/);
  assert.match(html, /不自动标记整个要求完成/);
  assert.match(html, /不证明整体需求已满足/);
  assert.match(html, /同一操作系统账户下的恶意项目代码可伪造测试报告/);
  assert.match(html, /查看后端执行预览/);
  assert.match(html, /后端预览 &lt;script&gt; --target/);
  assert.doesNotMatch(html, /验证一条项目要求/);
  assert.match(html, /conftest/);
  assert.match(html, /插件/);
  assert.match(html, /本机项目代码/);
});

test("request and completion are bound to the current session, run, subtype and unique request", () => {
  const c = chat();
  assert.equal(c.handleEvent(request({ session_id: "session-b" })), false);
  assert.equal(c.handleEvent(request({ run_id: "" })), false);
  assert.equal(c.handleEvent(request({ run_id: "other-run" })), false);
  assert.equal(c.handleEvent(request({ confirm_subtype: "unknown" })), false);
  assert.equal(c.handleEvent(request({ confirm_subtype: undefined })), false);
  assert.equal(c.handleEvent(request()), true);
  assert.equal(c.handleEvent(request()), false, "duplicate request id must not create a second card");
  assert.equal(c.submitConfirm("confirm-a", "a"), false);
  assert.equal(c.submitConfirm("confirm-a", "y"), true);
  const done = { type: "confirm.done", confirm_subtype: "project_worker_requirement_verification", request_id: "confirm-a", session_id: "session-a", run_id: "parent-run-a", choice: "y" };
  assert.equal(c.handleEvent({ ...done, run_id: "parent-run-b" }), false);
  assert.equal(c.handleEvent({ ...done, confirm_subtype: "wrong" }), false);
  assert.equal(c.handleEvent(done), true);
  assert.equal(c.handleEvent(done), false, "duplicate completion must not resolve twice");
  const c2 = chat();
  assert.equal(c2.handleEvent(request()), true);
  c2.handleEvent({ type: "execution.state", run_id: "next-run", state: "running", sequence: 2 });
  assert.equal(c2.submitConfirm("confirm-a", "y"), false, "new run revokes the old request");
  c.handleEvent(banner("session-b"));
  assert.equal(c.handleEvent(done), false, "old session completion must not attach to new session");
});

test("malformed payload and approve-all fail closed while rejection remains available", () => {
  const c = chat();
  assert.equal(c.handleEvent(request({ project_worker_verification: { ...data, session_id: "other" } })), true);
  assert.equal(c.submitConfirm("confirm-a", "y"), false);
  assert.equal(c.submitConfirm("confirm-a", "a"), false);
  assert.equal(c.submitConfirm("confirm-a", "n"), true);
  const c2 = chat();
  assert.equal(c2.handleEvent(request({ allow_approve_all: true })), false);
  const missingPreview = chat();
  assert.equal(missingPreview.handleEvent(request({ preview: "" })), true);
  assert.equal(missingPreview.submitConfirm("confirm-a", "y"), false);
  assert.equal(missingPreview.submitConfirm("confirm-a", "n"), true);
});

test("actual unified confirm renderer keeps the request inline and disables malformed approval", () => {
  const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  const ast = ts.createSourceFile("index.ts", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  function find(predicate, node = ast) { if (predicate(node)) return node; return ts.forEachChild(node, child => find(predicate, child)); }
  const renderBlock = find(node => ts.isFunctionDeclaration(node) && node.name?.text === "renderBlock");
  const branch = find(node => ts.isIfStatement(node) && node.expression.getText(ast) === 'block.kind === "confirm"', renderBlock);
  const js = ts.transpileModule("return function(block){" + branch.getText(ast) + " return \"\";}", { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const chatState = load(new URL("../src/shells/chat-state.ts", import.meta.url));
  for (const perspective of ["project", "night"]) {
    const render = new Function("perspective", "escapeHtml", "parseWorkRecordReplyExcerpt", "parseWorkRecordAutoCandidate", "parseProjectWorkerVerification", "isWorkCardRecallPreview", "isConfirmInProgressLabel", "renderConfirmCardHtml", js)(
      perspective, chatState.escapeHtml, () => null, preview.parseWorkRecordAutoCandidate, preview.parseProjectWorkerVerification,
      () => false, chatState.isConfirmInProgressLabel, preview.renderConfirmCardHtml,
    );
    const block = { kind: "confirm", confirmKind: "project_worker_requirement_verification", confirmSubtype: "project_worker_requirement_verification", sessionId: "session-a", projectWorkerVerification: data, requestId: "confirm-a", preview: "backend preview", allowApproveAll: false };
    const html = render(block);
    assert.match(html, /data-project-worker-verification/);
    assert.match(html, /Project &lt;A&gt;/);
    assert.match(html, /tests\/test_one\.py::test_case/);
    assert.doesNotMatch(html, /data-confirm="a"/);
    assert.match(render({ ...block, projectWorkerVerification: { ...data, extra: 1 } }), /data-confirm="y"[^>]*disabled/);
    const noPreview = render({ ...block, preview: "" });
    assert.match(noPreview, /后端执行预览缺失/);
    assert.match(noPreview, /data-confirm="y"[^>]*disabled/);
  }
});
test("existing confirm response sends only the request identity and user choice", () => {
  const { AgentWsClient } = load(new URL("../src/api/ws.ts", import.meta.url));
  const client = Object.create(AgentWsClient.prototype);
  const sent = [];
  client.send = payload => sent.push(payload);
  client.sendConfirm("confirm-a", "y");
  assert.deepEqual(sent, [{ type: "confirm.response", request_id: "confirm-a", choice: "y" }]);
});

test("stopping or a new run revokes a pending requirement approval", () => {
  const c = chat();
  assert.equal(c.handleEvent(request()), true);
  c.handleEvent({ type: "execution.state", run_id: "parent-run-a", state: "stopping", sequence: 2 });
  assert.equal(c.submitConfirm("confirm-a", "y"), false);
  assert.equal(c.model.blocks.find(block => block.kind === "confirm")?.resolved, "已取消");
  const c2 = chat();
  assert.equal(c2.handleEvent(request()), true);
  c2.handleEvent({ type: "execution.state", run_id: "next-run", state: "running", sequence: 2 });
  assert.equal(c2.submitConfirm("confirm-a", "y"), false);
  assert.equal(c2.model.blocks.find(block => block.kind === "confirm")?.resolved, "已过期");
});
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
const require = createRequire(import.meta.url);
const browserModule = process.env.CODEX_PLAYWRIGHT_PATH || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const browserPath = process.env.ISOLATED_TEST_BROWSER || (process.platform === "win32" ? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" : "");
const desktopRoot = fileURLToPath(new URL("../", import.meta.url));
test("isolated unified mount consumes the scoped event, renders the decision, and sends only id plus choice", {
  skip: !existsSync(browserModule) || !browserPath || !existsSync(browserPath), timeout: 30000,
}, async () => {
  const esbuild = require("esbuild");
  const bundle = await esbuild.build({
    entryPoints: [path.join(desktopRoot, "src/shells/unified/index.ts")], bundle: true, write: false,
    platform: "browser", format: "iife", globalName: "UnifiedUnderTest",
    define: { "import.meta.url": '"https://isolated.invalid/entry.js"' },
    loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
    plugins: [{ name: "no-css-imports", setup(build) { build.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }],
    logLevel: "silent",
  });
  const css = readFileSync(path.join(desktopRoot, "src/shells/unified/unified.css"), "utf8");
  const browser = await require(browserModule).chromium.launch({ headless: true, executablePath: browserPath, args: ["--disable-background-networking", "--no-first-run"] });
  try {
    const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1100, height: 800 } });
    const requests = [], errors = [];
    await context.route("**/*", route => {
      const url = route.request().url(); requests.push(url);
      return url === "https://isolated.invalid/"
        ? route.fulfill({ status: 200, contentType: "text/html", body: '<!doctype html><div id="root"></div>' })
        : route.abort();
    });
    const page = await context.newPage(); page.setDefaultTimeout(5000); page.on("pageerror", error => errors.push(error.message));
    await page.goto("https://isolated.invalid/");
    await page.addStyleTag({ content: css }); await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(() => {
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      window.calls = [];
      const fake = new Proxy({
        onEvent: handler => { window.emitFake = handler; return () => {}; },
        listSessionsMerged: async () => [], sendConfirm: (...args) => window.calls.push(args),
      }, { get: (object, key) => key in object ? object[key] : (...args) => String(key).startsWith("on") ? () => {} : undefined });
      window.disposeUnified = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), fake);
      window.emitFake({ type: "session.banner", session_id: "session-a", topics: [], turn_mode: "agent", turn_mode_label: "执行", phase: "S4" });
      window.emitFake({ type: "execution.state", run_id: "parent-run-a", state: "running", sequence: 1 });
    });
    const event = request();
    await page.evaluate(value => window.emitFake(value), event);
    const card = page.locator("[data-project-worker-verification]");
    assert.equal(await card.count(), 1);
    assert.match(await card.textContent(), /Project &lt;A&gt;|Project <A>/);
    assert.match(await card.textContent(), /tests\/test_one\.py::test_case/);
    assert.match(await card.textContent(), /conftest/);
    assert.match(await card.textContent(), /插件/);
    assert.equal(await card.locator("img, script").count(), 0);
    await card.locator('[data-confirm="y"]').click();
    assert.deepEqual(await page.evaluate(() => window.calls), [["confirm-a", "y"]]);
    await page.evaluate(value => window.emitFake({ ...value, type: "confirm.done", choice: "y" }), event);
    await page.evaluate(value => window.emitFake({ ...value, request_id: "confirm-b", project_worker_verification: { ...value.project_worker_verification, session_id: "other" } }), event);
    const malformed = page.locator('[data-project-worker-verification]').last();
    assert.match(await malformed.textContent(), /暂不可批准/);
    assert.equal(await malformed.locator('[data-confirm="y"]').isDisabled(), true);
    await malformed.locator('[data-confirm="n"]').click();
    assert.deepEqual(await page.evaluate(() => window.calls), [["confirm-a", "y"], ["confirm-b", "n"]]);
    assert.deepEqual(errors, []);
    assert.ok(requests.every(url => url.startsWith("https://isolated.invalid/")));
    await page.evaluate(() => window.disposeUnified()); await context.close();
  } finally { await browser.close(); }
});

test("backend WsBridge confirmation travels over loopback WebSocket into the mounted Desktop UI", {
  skip: process.platform !== "win32" || !existsSync(browserModule) || !existsSync(browserPath)
    || !existsSync("F:/my-agent-main/.venv/Scripts/python.exe"),
  timeout: 45000,
}, async () => {
  const esbuild = require("esbuild");
  const python = "F:/my-agent-main/.venv/Scripts/python.exe";
  const fixture = path.join(desktopRoot, "tests/project-worker-requirement-ws-fixture.py");
  const core = path.resolve(desktopRoot, "../agent-core");
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1" };
  const child = spawn(python, [fixture, core], { cwd: desktopRoot, env, stdio: ["pipe", "pipe", "pipe"] });
  const stdout = createInterface({ input: child.stdout });
  let stderr = ""; child.stderr.on("data", chunk => stderr += chunk);
  let browser;
  try {
    const ready = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`backend fixture startup timeout: ${stderr}`)), 10000);
      stdout.once("line", line => {
        clearTimeout(timer);
        try { resolve(JSON.parse(line)); } catch (error) { reject(error); }
      });
      child.once("exit", code => { clearTimeout(timer); reject(new Error(`backend fixture exited ${code}: ${stderr}`)); });
    });
    assert.ok(ready.port > 0 && ready.port !== 5173 && ready.port !== 5174 && ready.port !== 8000 && ready.port !== 8765);
    const bundle = await esbuild.build({
      stdin: { contents: 'export { mountUnifiedShell } from "./src/shells/unified/index.ts"; export { AgentWsClient } from "./src/api/ws.ts";', resolveDir: desktopRoot, loader: "ts" },
      bundle: true, write: false, platform: "browser", format: "iife", globalName: "UnifiedUnderTest",
      define: { "import.meta.url": '"http://127.0.0.1/isolated.js"' },
      loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
      plugins: [{ name: "no-css-imports", setup(build) { build.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }],
      logLevel: "silent",
    });
    const css = readFileSync(path.join(desktopRoot, "src/shells/unified/unified.css"), "utf8");
    browser = await require(browserModule).chromium.launch({ headless: true, executablePath: browserPath,
      args: ["--disable-background-networking", "--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessRestrictions,BlockInsecurePrivateNetworkRequests", "--no-first-run"] });
    const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1100, height: 800 } });
    const errors = [];
    await context.route("**/*", route => route.request().url() === `http://127.0.0.1:${ready.port}/`
      ? route.fulfill({ status: 200, contentType: "text/html", body: '<!doctype html><div id="root"></div>' })
      : route.abort());
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on("pageerror", error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${ready.port}/`);
    await page.addStyleTag({ content: css }); await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(async ({ port }) => {
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      window.client = new UnifiedUnderTest.AgentWsClient("127.0.0.1", port);
      window.receivedEvents = []; window.client.onEvent(event => window.receivedEvents.push(event));
      window.disposeUnified = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), window.client);
      await window.client.connect();
    }, { port: ready.port });
    const card = page.locator("[data-project-worker-verification]");
    await card.waitFor({ state: "visible" });
    assert.match(await card.textContent(), /合成项目/);
    assert.match(await card.textContent(), /结果仅记录为用户授权目标测试的自报告测试结果/);
    assert.match(await card.textContent(), /不自动标记整个要求完成/);
    assert.match(await card.textContent(), /同一操作系统账户下的恶意项目代码可伪造测试报告/);
    assert.equal(await card.locator("details.unified-confirm-details").count(), 1);
    assert.match(await card.textContent(), /本次仅运行你授权的单个 pytest 目标/);
    assert.match(await card.locator("details.unified-confirm-details").textContent(), /命令：/);
    assert.match(await card.textContent(), /合成要求/);
    assert.match(await card.textContent(), /tests\/test_synthetic\.py::test_target/);
    assert.match(await card.textContent(), /conftest/);
    assert.match(await card.textContent(), /插件/);
    assert.equal(await card.locator('[data-confirm="a"]').count(), 0);
    await card.locator('[data-confirm="n"]').click();
    try { await page.locator(".unified-confirm-status-text").filter({ hasText: "已跳过" }).waitFor({ state: "visible", timeout: 5000 }); }
    catch { throw new Error(JSON.stringify(await page.evaluate(() => ({ events: window.receivedEvents.map(e => ({type:e.type, choice:e.choice, request_id:e.request_id, session_id:e.session_id, run_id:e.run_id, confirm_subtype:e.confirm_subtype})), body: document.body.innerText })))); }
    assert.doesNotMatch(await page.locator("body").innerText(), /已提交拒绝/);
    assert.deepEqual(errors, []);
    await page.evaluate(() => { window.disposeUnified(); window.client.destroy(); });
    await context.close();
  } finally {
    if (browser) await browser.close();
    child.stdin.end("\n");
    await new Promise(resolve => {
      if (child.exitCode !== null) return resolve();
      child.once("exit", resolve); setTimeout(() => { child.kill(); resolve(); }, 4000);
    });
    if (stderr) assert.equal(stderr.trim(), "", stderr);
  }
});









