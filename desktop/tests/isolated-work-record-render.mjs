#!/usr/bin/env node
/**
 * Renderer-only smoke for the single-dialogue work-record flow.
 * All sidecar/preload events are synthetic; no backend or model is started.
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import net from "node:net";
import { randomInt } from "node:crypto";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { mkdtemp } from "node:fs/promises";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(scriptDir, "..");
const sandboxRoot = path.resolve(desktopRoot, "..");
const excludedPorts = new Set([5173, 5174, 8765, 8766]);
const artifactsRoot = await mkdtemp(path.join(os.tmpdir(), "my-agent-render-smoke-"));
let viteServer;
let browser;
let browserServer;
let context;
let page;
let browserErrors = [];
let phaseStartedAt = Date.now();

function logPhase(name, state, detail = "") {
  const elapsed = Date.now() - phaseStartedAt;
  const safeDetail = detail ? ` ${String(detail).replace(/[\r\n]+/g, " ").slice(0, 220)}` : "";
  console.log(`[phase] ${name} ${state} +${elapsed}ms${safeDetail}`);
  phaseStartedAt = Date.now();
}

async function phase(name, operation, timeoutMs = 12_000) {
  logPhase(name, "start");
  let timer;
  try {
    const result = await Promise.race([
      Promise.resolve().then(operation),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${name} timeout after ${timeoutMs}ms`)), timeoutMs); }),
    ]);
    logPhase(name, "done");
    return result;
  } catch (error) {
    logPhase(name, "failed", error instanceof Error ? error.message : "unexpected failure");
    throw error;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

function loadPlaywright() {
  const localRequire = createRequire(path.join(desktopRoot, "package.json"));
  try { return localRequire("playwright"); } catch { /* use caller-provided runtime */ }
  const moduleRoot = process.env.MY_AGENT_PLAYWRIGHT_NODE_MODULES;
  if (!moduleRoot) {
    throw new Error("Playwright is not in desktop/node_modules; set MY_AGENT_PLAYWRIGHT_NODE_MODULES to the existing Playwright node_modules directory (no install). ");
  }
  return createRequire(path.join(moduleRoot, "package.json"))("playwright");
}

async function chooseUnusedLoopbackPort() {
  // Probe only randomized high ports. Protected well-known ports are never bound,
  // even briefly; strictPort makes a later race fail closed instead of falling back.
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const port = randomInt(20_000, 60_000);
    if (excludedPorts.has(port)) continue;
    const probe = net.createServer();
    const available = await new Promise((resolve) => {
      probe.once("error", () => resolve(false));
      probe.listen(port, "127.0.0.1", () => resolve(true));
    });
    if (available) {
      await new Promise((resolve) => probe.close(resolve));
      return port;
    }
    try { probe.close(); } catch { /* never close another process; this is our probe socket */ }
  }
  throw new Error("could not reserve an unused randomized loopback port");
}

function makeSyntheticPreloadShim() {
  return `(() => {
    const sessionId = "synthetic-ui-session";
    const trace = { sockets: [], messages: [], confirmations: [] };
    window.__workRecordUiMock = trace;
    window.myAgentDesktop = {
      getSidecar: async () => ({ host: "synthetic.mock", port: 49151 }),
      getSessionLaneCapabilities: async () => ({ legacySessionLanes: false }),
      setEvolutionConversationId: async () => true,
      onSessionControl: () => () => {},
      onEvolutionRequest: () => () => {},
      openExternal: async () => true,
      switchToCli: async () => {},
    };
    const NativeWebSocket = window.WebSocket;
    function deliver(socket, event) {
      socket.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(event) }));
    }
    function terminal(socket, runId, sequence, finishReason = "completed") {
      deliver(socket, {
        type: "execution.state", run_id: runId, mode: "ordinary", state: "settled",
        sequence, cancel_requested: false, finish_reason: finishReason,
        ok: true, started_at: 1, ended_at: 2,
      });
      deliver(socket, { type: "turn.end", run_id: runId, ok: true, finish_reason: finishReason });
    }
    class SyntheticSocket extends EventTarget {
      static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
      constructor(url) {
        super(); this.url = String(url); this.readyState = SyntheticSocket.CONNECTING;
        this.bufferedAmount = 0; this.protocol = ""; this.extensions = "";
        this.binaryType = "blob"; this._stage = ""; this._confirmIndex = 0;
        trace.sockets.push(this.url);
        queueMicrotask(() => {
          this.readyState = SyntheticSocket.OPEN;
          this.dispatchEvent(new Event("open"));
          setTimeout(() => {
            deliver(this, { type: "session.banner", session_id: sessionId, goal: null,
              topics: [], turn_mode: "agent", turn_mode_label: "动手", phase: "S2",
              active_shell: "grow", project_id: "", project_root: null,
              project_plan_status: "missing", llm_model: "synthetic-mock",
              llm_model_label: "Synthetic mock", llm_model_override: false });
            deliver(this, { type: "session.history", session_id: sessionId, items: [], truncated: false, total_items: 0 });
            deliver(this, { type: "session.memory", message_count: 0, memory_mode: "full", memory_mode_label: "完整" });
            deliver(this, { type: "session.models", models: [], default_flash_id: "synthetic-mock", default_pro_id: "synthetic-mock" });
            deliver(this, { type: "evolve.proposals", items: [] });
            deliver(this, { type: "evolve.memories", items: [] });
          }, 0);
        });
      }
      send(raw) {
        const message = JSON.parse(String(raw)); trace.messages.push(message);
        if (message.type === "user.message") {
          const runId = this._stage === "capture" ? "synthetic-recall-run" : "synthetic-capture-run";
          this._stage = message.text.includes("查找之前的工作记录") ? "recall" : "capture";
          const activeRun = this._stage === "recall" ? "synthetic-recall-run" : "synthetic-capture-run";
          deliver(this, { type: "execution.state", run_id: activeRun, mode: "ordinary", state: "running", sequence: this._stage === "recall" ? 4 : 1, cancel_requested: false });
          deliver(this, { type: "turn.start", run_id: activeRun, intent: "execute", intent_label: "执行" });
          this._confirmIndex = 0;
          this._sendNextConfirmation();
          return;
        }
        if (message.type === "confirm.response") {
          trace.confirmations.push({ stage: this._stage, choice: message.choice });
          deliver(this, { type: "confirm.done", request_id: message.request_id, choice: message.choice, run_id: this._stage === "recall" ? "synthetic-recall-run" : "synthetic-capture-run" });
          if (message.choice !== "y") {
            terminal(this, this._stage === "recall" ? "synthetic-recall-run" : "synthetic-capture-run", this._stage === "recall" ? 6 : 4, "cancelled");
            return;
          }
          if (this._stage === "capture" && this._confirmIndex === 0) {
            this._confirmIndex = 1;
            this._sendNextConfirmation();
            return;
          }
          if (this._stage === "capture") {
            deliver(this, { type: "turn.notice", level: "info", run_id: "synthetic-capture-run", text: "已按本次授权写入当前会话工作记录。" });
            deliver(this, { type: "assistant.done", run_id: "synthetic-capture-run", text: "合成 run 已结束；此渲染 smoke 不执行工具。" });
            deliver(this, { type: "turn.notice", level: "info", run_id: "synthetic-capture-run", text: "本回合已关联到工作记录；完成及验证情况未知，未标记完成。" });
            terminal(this, "synthetic-capture-run", 3);
            this._stage = "capture-settled";
            return;
          }
          deliver(this, { type: "assistant.done", run_id: "synthetic-recall-run", text: "当前会话已存工作记录：独立事项：合成 UI 验收；要求：记录确认、通知及回查可见；状态：unknown。完成和验证情况未知。" });
          terminal(this, "synthetic-recall-run", 6);
        }
      }
      _sendNextConfirmation() {
        const requestId = this._stage === "capture"
          ? (this._confirmIndex === 0 ? "synthetic-capture-outbound" : "synthetic-capture-storage")
          : "synthetic-recall-disclosure";
        const preview = this._stage === "capture"
          ? (this._confirmIndex === 0
            ? "工作记录单次外发确认\\n合成数据：仅由本地 mock 分类。"
            : "工作记录本地落盘确认\\n合成事项：合成 UI 验收\\n本地 track_run 提示：结果只记录 unknown。")
          : "当前会话工作记录回查确认\\n合成事项：合成 UI 验收\\n合成要求：记录确认、通知及回查可见。";
        deliver(this, { type: "confirm.request", request_id: requestId, preview, allow_approve_all: false,
          run_id: this._stage === "recall" ? "synthetic-recall-run" : "synthetic-capture-run" });
      }
      close() { this.readyState = SyntheticSocket.CLOSED; this.dispatchEvent(new Event("close")); }
      addEventListener(...args) { return super.addEventListener(...args); }
      removeEventListener(...args) { return super.removeEventListener(...args); }
    }
    window.WebSocket = new Proxy(NativeWebSocket, {
      construct(Target, args) {
        const url = new URL(String(args[0]), window.location.href);
        // Keep only Vite's own HMR socket real; every application socket is synthetic.
        if (url.host === window.location.host) return Reflect.construct(Target, args);
        return new SyntheticSocket(String(args[0]));
      },
    });
  })();`;
}

async function waitForVisible(locator, label, timeout = 8000) {
  await locator.waitFor({ state: "visible", timeout }).catch((error) => {
    throw new Error(`${label} did not become visible: ${error.message}`);
  });
}

async function clickNextConfirmation(page) {
  const button = page.locator('button[data-confirm="y"]:visible').last();
  await waitForVisible(button, "synthetic confirmation button");
  await button.click();
}

function waitForText(page, text, timeout = 8000) {
  return page.getByText(text, { exact: false }).first().waitFor({ state: "visible", timeout });
}

async function main() {
  const tempLocalAppData = path.join(artifactsRoot, "localappdata");
  const originalLocalAppData = process.env.LOCALAPPDATA;
  process.env.LOCALAPPDATA = tempLocalAppData;
  try {
    const requireFromDesktop = createRequire(path.join(desktopRoot, "package.json"));
    const { createServer } = await phase("load-vite", () => requireFromDesktop("vite"));
    const moduleRoot = process.env.MY_AGENT_PLAYWRIGHT_NODE_MODULES;
    const playwrightRequire = moduleRoot
      ? createRequire(path.join(moduleRoot, "package.json"))
      : requireFromDesktop;
    const { chromium } = await phase("load-playwright", () => playwrightRequire("playwright"));
    const executablePath = process.env.MY_AGENT_PLAYWRIGHT_EXECUTABLE
      || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
    const { access } = await import("node:fs/promises");
    await phase("check-system-browser", () => access(executablePath));

    const port = await phase("reserve-renderer-port", chooseUnusedLoopbackPort);
    assert.ok(!excludedPorts.has(port), "selected port overlaps a protected service port");
    viteServer = await phase("create-renderer-server", () => createServer({
      root: desktopRoot,
      configFile: path.join(desktopRoot, "vite.shell-preview.config.ts"),
      server: { host: "127.0.0.1", port, strictPort: true },
      clearScreen: false,
    }));
    await phase("listen-renderer", () => viteServer.listen(), 15_000);
    const address = viteServer.httpServer.address();
    assert.ok(address && typeof address === "object");
    assert.equal(address.address, "127.0.0.1");
    assert.equal(address.port, port);
    assert.ok(!excludedPorts.has(address.port));

    browserServer = await phase("launch-owned-temporary-browser", () => chromium.launchServer({
      executablePath,
      headless: true,
      timeout: 12_000,
      args: ["--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-background-networking"],
    }), 15_000);
    browser = await phase("connect-owned-browser", () => chromium.connect(browserServer.wsEndpoint()), 8_000);
    context = await phase("create-isolated-browser-context", () => browser.newContext({
      viewport: { width: 1365, height: 900 }, locale: "zh-CN",
    }));
    await phase("install-synthetic-preload", () => context.addInitScript(makeSyntheticPreloadShim()));
    page = await phase("create-page", () => context.newPage());
    page.setDefaultTimeout(5_000);
    page.on("pageerror", (error) => browserErrors.push(`pageerror: ${error.message}`));
    page.on("console", (message) => { if (message.type() === "error") browserErrors.push(`console: ${message.text()}`); });
    await page.route("**/*", (route) => {
      const url = new URL(route.request().url());
      if (url.protocol === "http:" && url.hostname === "127.0.0.1" && Number(url.port) === port) return route.continue();
      return route.abort("blockedbyclient");
    });

    const url = `http://127.0.0.1:${port}/`;
    await phase("navigate-renderer", () => page.goto(url, { waitUntil: "domcontentloaded", timeout: 10_000 }), 12_000);
    try {
      await phase("wait-for-composer", () => waitForVisible(page.locator("#unified-input"), "chat composer", 5_000), 6_000);
    } catch (error) {
      const diagnostics = await page.evaluate(() => ({
        title: document.title,
        readyState: document.readyState,
        appText: (document.querySelector("#app")?.innerText || "").slice(0, 240),
        preloadPresent: Boolean(window.myAgentDesktop),
      })).catch(() => ({ title: "unavailable", readyState: "unavailable", appText: "unavailable" }));
      console.log(`[diagnostic] renderer=${JSON.stringify(diagnostics)} pageErrors=${JSON.stringify(browserErrors).slice(0, 1200)}`);
      throw error;
    }
    assert.match(await page.title(), /my-agent/i);

    const chatText = () => page.locator("#unified-chat").innerText();
    await phase("submit-synthetic-capture", async () => {
      await page.locator("#unified-input").fill("记录一下：独立事项“合成 UI 验收”，要求记录确认、通知及回查可见。此处仅为合成测试数据。");
      await page.locator("#unified-send").click();
    });
    await phase("capture-outbound-confirm-render", () => waitForText(page, "工作记录单次外发确认"));
    await phase("capture-outbound-confirm-click", () => clickNextConfirmation(page));
    await phase("capture-storage-confirm-render", () => waitForText(page, "工作记录本地落盘确认"));
    await phase("capture-storage-confirm-click", () => clickNextConfirmation(page));
    await phase("capture-notice-render", () => waitForText(page, "已按本次授权写入当前会话工作记录"));
    await phase("run-result-notice-render", () => waitForText(page, "本回合已关联到工作记录；完成及验证情况未知"));

    await phase("submit-synthetic-recall", async () => {
      await page.locator("#unified-input").fill("查找之前的工作记录：合成 UI 验收");
      await page.locator("#unified-send").click();
    });
    await phase("recall-confirm-render", () => waitForText(page, "当前会话工作记录回查确认"));
    await phase("recall-confirm-click", () => clickNextConfirmation(page));
    await phase("recall-result-render", () => waitForText(page, "要求：记录确认、通知及回查可见"));
    await phase("recall-unknown-status-render", () => waitForText(page, "完成和验证情况未知"));

    const trace = await page.evaluate(() => window.__workRecordUiMock);
    assert.equal(trace.sockets.length, 1, "capture and recall stayed in one mocked conversation socket");
    assert.equal(trace.messages.filter((message) => message.type === "user.message").length, 2);
    assert.deepEqual(trace.confirmations.map((item) => item.stage), ["capture", "capture", "recall"]);
    assert.deepEqual(browserErrors, [], "no browser page or console errors");

    const screenshot = path.join(artifactsRoot, "renderer-smoke.png");
    await phase("capture-rendered-screenshot", () => page.screenshot({ path: screenshot, fullPage: false }));
    console.log(JSON.stringify({
      result: "PASS",
      classification: "renderer-only DOM smoke; synthetic preload/WebSocket; no backend, Electron, or model",
      url,
      port,
      browser: executablePath,
      browserAutomation: "Playwright system Edge fallback; Browser plugin unavailable",
      flow: ["two capture confirmations", "record notification", "same-dialog recall confirmation", "recall result"],
      sameSocket: trace.sockets.length === 1,
      userMessageCount: trace.messages.filter((message) => message.type === "user.message").length,
      screenshotFile: path.basename(screenshot),
      tempArtifactsId: path.basename(artifactsRoot),
    }, null, 2));
  } finally {
    logPhase("cleanup-owned-browser-context", "start");
    if (context) await phase("close-owned-browser-context", () => context.close(), 8_000).catch(() => {});
    if (browser) await phase("disconnect-owned-browser-client", () => browser.close(), 8_000).catch(() => {});
    if (browserServer) {
      try {
        await phase("close-owned-browser-process", () => browserServer.close(), 10_000);
      } catch {
        // BrowserServer belongs to this test only; never target system/browser-service PIDs.
        await phase("kill-owned-browser-process", () => browserServer.kill(), 5_000).catch(() => {});
      }
    }
    if (viteServer) await phase("close-owned-vite-server", () => viteServer.close(), 8_000).catch(() => {});
    if (originalLocalAppData === undefined) delete process.env.LOCALAPPDATA;
    else process.env.LOCALAPPDATA = originalLocalAppData;
    console.log(`Synthetic artifacts retained at ${artifactsRoot}; no recursive cleanup performed.`);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack : String(error));
  process.exitCode = 1;
});
