import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const require = createRequire(import.meta.url);
const esbuild = require("esbuild");
const playwrightPath = process.env.CODEX_PLAYWRIGHT_PATH
  || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const edgePath = process.env.ISOLATED_TEST_BROWSER
  || (process.platform === "win32" ? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" : "");
const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("recorded automatic work-record result refreshes overview after earlier turn-end query", {
  skip: !existsSync(playwrightPath) || !existsSync(edgePath), timeout: 30000,
}, async () => {
  const bundle = await esbuild.build({
    entryPoints: [path.join(desktopRoot, "src/shells/unified/index.ts")],
    bundle: true, write: false, platform: "browser", format: "iife", globalName: "UnifiedUnderTest",
    define: { "import.meta.url": '"https://isolated.invalid/entry.js"' },
    loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
    plugins: [{ name: "no-css", setup(build) { build.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }],
    logLevel: "silent",
  });
  const browser = await require(playwrightPath).chromium.launch({
    headless: true, executablePath: edgePath, args: ["--disable-background-networking", "--no-first-run"],
  });
  try {
    const context = await browser.newContext({ serviceWorkers: "block" });
    const networkRequests = [];
    const pageErrors = [];
    await context.route("**/*", route => {
      networkRequests.push(route.request().url());
      if (route.request().url() === "https://isolated.invalid/") {
        return route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><div id=\"root\"></div>" });
      }
      return route.abort();
    });
    const page = await context.newPage();
    page.on("pageerror", error => pageErrors.push(error.message));
    await page.goto("https://isolated.invalid/");
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(() => {
      window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
      window.overviewRequests = [];
      const handlers = new Set();
      const client = new Proxy({
        listSessionsMerged: async () => [],
        onEvent(handler) { handlers.add(handler); return () => handlers.delete(handler); },
        queryWorkRecordsOverview(options) {
          const request = { requestId: `overview-${overviewRequests.length + 1}`, sessionId: window.currentSessionId, options };
          overviewRequests.push(request);
          return request.requestId;
        },
      }, { get: (target, key) => key in target ? target[key] : (..._args) => String(key).startsWith("on") ? () => {} : undefined });
      window.emitServer = event => { for (const handler of handlers) handler(event); };
      window.disposeUnified = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), client);
      window.currentSessionId = "synthetic-current-session";
    });
    const waitForRequestCount = async expected => {
      for (let attempt = 0; attempt < 100; attempt++) {
        const count = await page.evaluate(() => overviewRequests.length);
        if (count >= expected) return;
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      assert.fail(`overview query count did not reach ${expected}`);
    };
    const noRecords = requestId => ({
      type: "work-records.overview.result", session_id: "synthetic-current-session", request_id: requestId,
      state: "no_records", groups: [], unread_important_results: [], unread_status: "known",
      visibility_limitations: [], visibility_counts: { visible_groups: 0, visible_items: 0, collapsed_groups: 0, collapsed_items: 0 },
      snapshot_id: "synthetic-before-save",
      page: { page_size: 6, offset: 0, total_groups: 0, total_visible_groups: 0, total_visible_items: 0,
        total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null },
      visibility_recheck_after_ms: 600000,
    });

    await page.evaluate(sessionId => window.emitServer({
      type: "session.banner", session_id: sessionId, project_id: "synthetic-project",
      project_plan_status: "draft", topics: [], turn_mode: "agent", turn_mode_label: "", phase: "S4",
    }), "synthetic-current-session");
    await waitForRequestCount(1);
    let first = await page.evaluate(() => overviewRequests[0]);
    await page.evaluate(event => window.emitServer(event), noRecords(first.requestId));

    // Ordinary host run ends first. The immediate overview response is correctly stale,
    // because the automatic recognition/save is an asynchronous post-turn task.
    await page.evaluate(() => {
      window.emitServer({ type: "turn.start", run_id: "synthetic-host-run", intent: "execute", intent_label: "执行" });
      window.emitServer({ type: "turn.end", run_id: "synthetic-host-run", ok: true, finish_reason: "completed" });
    });
    await waitForRequestCount(2);
    first = await page.evaluate(() => overviewRequests[1]);
    await page.evaluate(event => window.emitServer(event), noRecords(first.requestId));
    assert.equal(await page.evaluate(() => overviewRequests.length), 2, "the turn-end recheck has completed before save result");

    await page.evaluate(() => window.emitServer({
      type: "work-record.post-turn.result", session_id: "synthetic-current-session",
      request_id: "synthetic-message-request", outcome: "recorded",
      message: "工作记录已保存并关联原回合；事项尚未验证。",
    }));
    await waitForRequestCount(3);
    const postSaveRequest = await page.evaluate(() => overviewRequests[2]);
    assert.equal(postSaveRequest.sessionId, "synthetic-current-session");
    const savedOverview = {
      type: "work-records.overview.result", session_id: "synthetic-current-session", request_id: postSaveRequest.requestId,
      state: "visible", groups: [{
        kind: "independent", id: "synthetic-item-id", visibility: "visible", status: "unknown",
        status_counts: { unknown: 1 }, item_count: 1, requirement_count: 1,
        items: [{ item_id: "synthetic-item-id", title: "Synthetic saved standalone item", status: "unknown",
          status_counts: { unknown: 1 }, requirements: [{ requirement_id: "synthetic-requirement-id", text: "Synthetic requirement", status: "unknown" }],
          current_results: [{ requirement_id: "synthetic-requirement-id", result_id: "synthetic-result-id", status: "unknown", read_status: "unknown" }] }],
      }],
      unread_important_results: [], unread_important_results_total: 0, unread_important_results_truncated: false,
      unread_status: "unknown", visibility_limitations: [],
      visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
      snapshot_id: "synthetic-after-save",
      page: { page_size: 6, offset: 0, total_groups: 1, total_visible_groups: 1, total_visible_items: 1,
        total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null },
      visibility_recheck_after_ms: 600000,
    };
    await page.evaluate(event => window.emitServer(event), savedOverview);
    await page.locator("#unified-request-current-work").click();
    assert.match(await page.locator("#unified-current-work-popover").textContent(), /Synthetic saved standalone item/);
    assert.deepEqual(pageErrors, []);
    assert.ok(networkRequests.every(url => url.startsWith("https://isolated.invalid/")));
    await page.evaluate(() => window.disposeUnified());
    await context.close();
  } finally {
    await browser.close();
  }
});

