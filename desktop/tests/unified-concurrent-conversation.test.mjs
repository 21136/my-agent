import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const require = createRequire(import.meta.url);
const esbuild = require("esbuild");
const playwrightPath = process.env.CODEX_PLAYWRIGHT_PATH || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const edge = process.env.ISOLATED_TEST_BROWSER || (process.platform === "win32" ? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" : "");
const unifiedRoot = new URL("../src/shells/unified/", import.meta.url);
const desktopRoot = new URL("../", unifiedRoot);

test("one composer sends into the shared session during work and reports work delivery accurately", {
  skip: !existsSync(playwrightPath) || !existsSync(edge),
  timeout: 45_000,
}, async () => {
  const background = readFileSync(new URL("assets/chat-background-dark.png", unifiedRoot));
  const css = readFileSync(new URL("../../styles/theme.css", unifiedRoot), "utf8") + "\n"
    + ["unified.css", "work-card.css", "current-work-overview.css", "neon-twin.css", "composer-companion.css", "sidebar-v2.css", "message-reading.css", "settings-panel.css", "music-panel.css", "shell-dev-panel.css", "single-chat-tools.css"]
      .map((file) => readFileSync(new URL(file, unifiedRoot), "utf8"))
      .join("\n")
      .replaceAll("./assets/chat-background-dark.png", `data:image/png;base64,${background.toString("base64")}`);
  const entry = new URL("index.ts", unifiedRoot).pathname.replace(/^\/(\w:)/, "$1");
  const bundle = await esbuild.build({
    entryPoints: [entry], bundle: true, write: false, platform: "browser", format: "iife", globalName: "UnifiedUnderTest",
    define: { "import.meta.url": '"https://parallel-chat.invalid/entry.js"' },
    loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
    plugins: [{ name: "ignore-css", setup(build) { build.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }],
    logLevel: "silent",
  });
  const browser = await require(playwrightPath).chromium.launch({ headless: true, executablePath: edge, args: ["--disable-background-networking", "--no-first-run"] });
  try {
    const context = await browser.newContext({ viewport: { width: 1200, height: 900 }, serviceWorkers: "block" });
    const page = await context.newPage();
    const errors = [];
    const requests = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await context.route("**/*", async (route) => {
      const url = route.request().url();
      requests.push(url);
      if (url === "https://parallel-chat.invalid/") {
        return route.fulfill({ status: 200, contentType: "text/html", body: `<!doctype html><html data-theme="dark"><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; connect-src 'none'"><style>html,body,#root{height:100%;margin:0}${css}</style></head><body><div id="root"></div></body></html>` });
      }
      return route.abort();
    });
    await page.goto("https://parallel-chat.invalid/");
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(() => {
      window.fakeCalls = [];
      window.fakeHandlers = [];
      window.emitFake = (event) => window.fakeHandlers.map((handler) => handler(event));
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      const fake = new Proxy({ onEvent: (handler) => { window.fakeHandlers.push(handler); return () => {}; }, onFileStaged: (handler) => { window.fakeFileStaged = handler; return () => {}; }, listSessionsMerged: async () => [], sendMessage: (text, attachments, opts) => { const requestId = opts?.requestId ?? crypto.randomUUID(); window.fakeCalls.push(["sendMessage", text, attachments, opts, requestId]); return { requestId, sessionId: "session-live" }; }, sendPromptResponse: (...args) => window.fakeCalls.push(["sendPromptResponse", ...args]) }, {
        get(target, key) { return key in target ? target[key] : (...args) => { window.fakeCalls.push([String(key), ...args]); return String(key).startsWith("on") ? () => {} : undefined; }; },
      });
      window.disposeUnified = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), fake);
    });
    const input = page.locator("#unified-input");
    const send = page.locator("#unified-send");
    await page.evaluate(() => {
      window.emitFake({ type: "session.banner", session_id: "session-live", topics: [], turn_mode: "agent", turn_mode_label: "动手", phase: "S4" });
      window.emitFake({ type: "execution.state", run_id: "work-run-1", state: "running", sequence: 1 });
      window.emitFake({ type: "turn.start", run_id: "work-run-1", intent: "execute", intent_label: "后台工作" });
    });
    await input.fill("工作期间的普通问题");
    await page.evaluate(() => window.fakeFileStaged([{ id: "attachment-1", name: "notes.txt", ref: "F:/notes.txt", size: 12, mime: "text/plain", readable_text: true, copied: true }]));
    await page.waitForTimeout(80);
    assert.equal(await send.isEnabled(), false, "unsupported attachments are not sent while work is running");
    assert.match(await page.locator("#unified-status").innerText(), /工作进行中暂不支持发送附件/);
    await page.locator(".file-drop-chip-remove").click();
    await page.waitForTimeout(80);
    assert.equal(await send.isEnabled(), true, "text can be sent after the unsupported attachment is removed");
    await send.click();
    const call = await page.evaluate(() => window.fakeCalls.find((item) => item[0] === "sendMessage"));
    assert.ok(call, "messages during work use the same user.message path");
    const requestId = call[4];
    assert.equal(call[1], "工作期间的普通问题");
    assert.equal(call[2], undefined);
    assert.equal(call[3].turnMode, "agent");
    assert.match(requestId, /^[0-9a-f-]{36}$/i);
    assert.equal(await page.locator("#unified-send-intent").count(), 0, "no chat/work destination chooser");
    const instruction = page.locator(`[data-work-instruction="${requestId}"]`);
    assert.equal(await instruction.getByRole("status").innerText(), "正在发送，等待后台分类…");
    assert.deepEqual(await page.evaluate(() => window.fakeCalls.filter((item) => ["sendParallelChatMessage", "sendWorkInstruction"].includes(item[0]))), []);
    await page.screenshot({ path: path.join(os.tmpdir(), `parallel-chat-pending-${process.pid}.png`) });

    await page.evaluate((rid) => window.emitFake({ type: "work.instruction.state", request_id: rid, run_id: "work-run-1", session_id: "session-live", state: "queued" }), requestId);
    await page.waitForTimeout(120);
    assert.equal(await instruction.getByRole("status").innerText(), "消息已排队，等待进入工作上下文");
    assert.doesNotMatch(await instruction.innerText(), /已进入下一次模型请求上下文/);
    await page.screenshot({ path: path.join(os.tmpdir(), `parallel-chat-queued-${process.pid}.png`) });
    await page.evaluate((rid) => window.emitFake({ type: "work.instruction.state", request_id: rid, run_id: "work-run-1", session_id: "session-live", state: "delivered", delivery_stage: "next_model_request_context" }), requestId);
    await page.waitForTimeout(100);
    assert.equal(await instruction.getByRole("status").innerText(), "已进入下一次模型请求上下文（不代表已遵循或完成）");

    await input.fill("另一条补充信息");
    await send.click();
    const second = await page.evaluate(() => window.fakeCalls.filter((item) => item[0] === "sendMessage")[1]);
    const secondId = second[4];
    await page.evaluate((rid) => window.emitFake({ type: "work.instruction.state", request_id: rid, run_id: "work-run-1", session_id: "session-live", state: "undelivered", reason: "queue_full" }), secondId);
    await page.waitForTimeout(100);
    assert.match(await page.locator(`[data-work-instruction="${secondId}"]`).innerText(), /消息未送达.*queue_full/);

    await input.fill("这是讨论，不是改动");
    await send.click();
    const third = await page.evaluate(() => window.fakeCalls.filter((item) => item[0] === "sendMessage")[2]);
    const thirdId = third[4];
    await page.evaluate((rid) => window.emitFake({ type: "chat.start", request_id: rid, work_run_id: "work-run-1", session_id: "session-live" }), thirdId);
    await page.waitForTimeout(100);
    assert.equal(await page.locator(`[data-work-instruction="${thirdId}"]`).count(), 0, "classified chat reuses the pending bubble");
    assert.equal(await page.locator(`[data-parallel-chat="${thirdId}"]`).count(), 1);
    assert.equal(await page.getByText("这是讨论，不是改动").count(), 1, "one conversation has one user message");
    await page.evaluate((rid) => window.emitFake({ type: "chat.delta", request_id: rid, work_run_id: "work-run-1", session_id: "session-live", text: "讨论回复" }), thirdId);
    await page.evaluate((rid) => window.emitFake({ type: "chat.done", request_id: rid, work_run_id: "work-run-1", session_id: "session-live", text: "讨论回复", finish_reason: "stop" }), thirdId);
    await page.waitForTimeout(100);
    assert.equal(await page.getByText("讨论回复").count(), 1);

    const promptRequestId = "12345678-1234-4234-8234-123456789001";
    await page.evaluate((rid) => window.emitFake({ type: "prompt.request", request_id: rid, run_id: "work-run-1", session_id: "session-live", prompt: "工具需要目标路径" }), promptRequestId);
    await page.waitForTimeout(100);
    await input.fill("src/main.ts");
    await send.click();
    const promptCall = await page.evaluate(() => window.fakeCalls.find((item) => item[0] === "sendPromptResponse"));
    assert.deepEqual(promptCall, ["sendPromptResponse", promptRequestId, "work-run-1", "session-live", "src/main.ts"]);
    assert.deepEqual(await page.evaluate(() => window.fakeCalls.filter((item) => item[0] === "sendMessage").map((item) => item[1])), ["工作期间的普通问题", "另一条补充信息", "这是讨论，不是改动"], "tool input never falls through to natural user.message");
    await page.evaluate((rid) => window.emitFake({ type: "prompt.response.result", request_id: rid, run_id: "work-run-1", session_id: "session-live", accepted: false }), promptRequestId);
    await page.waitForTimeout(100);
    assert.equal(await input.inputValue(), "src/main.ts", "rejected tool input remains available for safe retry");
    await send.click();
    assert.equal(await page.evaluate(() => window.fakeCalls.filter((item) => item[0] === "sendPromptResponse").length), 2);
    await page.evaluate((rid) => window.emitFake({ type: "prompt.response.result", request_id: rid, run_id: "work-run-1", session_id: "session-live", accepted: true }), promptRequestId);
    await page.waitForTimeout(100);
    assert.equal(await input.inputValue(), "", "accepted tool input is cleared after the correlated receipt");

    await page.locator("#unified-stop").click();
    assert.deepEqual(await page.evaluate(() => window.fakeCalls.filter((item) => item[0] === "sendExecutionStop").map((item) => item[1])), ["work-run-1"]);
    assert.deepEqual(await page.evaluate(() => window.fakeCalls.filter((item) => item[0] === "sendTurnCancel")), []);
    await page.evaluate(() => window.disposeUnified());
    assert.deepEqual(errors, []);
    assert.deepEqual(requests, ["https://parallel-chat.invalid/"]);
    await context.close();
  } finally {
    await browser.close();
  }
});
