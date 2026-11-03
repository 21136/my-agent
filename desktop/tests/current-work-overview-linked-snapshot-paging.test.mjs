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
const sessionId = "synthetic-linked-overview-session";
const overviewRequest = (requestId, groups, snapshotId = "overview-snapshot-a") => ({
  type: "work-records.overview.result", session_id: sessionId, request_id: requestId,
  state: "visible", groups, unread_important_results: [], unread_status: "known", visibility_limitations: [],
  visibility_counts: { visible_groups: groups.length, visible_items: groups.reduce((n, g) => n + g.item_count, 0), collapsed_groups: 0, collapsed_items: 0 },
  snapshot_id: snapshotId,
  page: { page_size: 6, offset: 0, total_groups: groups.length, total_visible_groups: groups.length,
    total_visible_items: groups.reduce((n, g) => n + g.item_count, 0), total_collapsed_groups: 0, total_collapsed_items: 0,
    has_more: false, next_cursor: null },
});
const sourceGroup = (suffix, items, offset, cursor) => {
  const id = `linked-source-${suffix}`;
  const page = { kind: "items", group_kind: "linked_source_snapshot", group_id: id, item_id: null,
    offset, total: 2, has_more: Boolean(cursor), next_cursor: cursor };
  return {
    kind: "linked_source_snapshot", id, visibility: "visible", status: "unknown", status_counts: { unknown: 2 },
    item_count: 2, requirement_count: 0, attention_status: "unknown", attention_reason: "captured_requirement_unresolved",
    source_snapshot: { scope: "authorized_source_session_only", mapping_claim: "not_asserted", project_id: "synthetic-project",
      source_session_id: `synthetic-source-${suffix}`, source_revision: 1, captured_at: "2026-10-30T01:00:00Z",
      copy_id: "a".repeat(64), intent_state: "pending", card_id: null, viewed_at: null,
      superseded_snapshot_count: 0, superseded_unviewed_count: 0 },
    items, items_page: page,
  };
};
const sourceItem = (id, title) => ({ item_id: id, title, status: "unknown", status_at_capture: "in_progress",
  status_counts: { unknown: 1 }, requirements: [], current_results: [] });

test("linked source snapshot detail button sends its cursor and merges only the focused snapshot", {
  skip: !existsSync(playwrightPath) || !existsSync(edgePath), timeout: 60000,
}, async () => {
  const bundle = await esbuild.build({
    entryPoints: [path.join(desktopRoot, "src/shells/unified/index.ts")], bundle: true, write: false,
    platform: "browser", format: "iife", globalName: "UnifiedUnderTest",
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
    const pageErrors = [];
    await context.route("**/*", route => route.request().url() === "https://isolated.invalid/"
      ? route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><div id=\"root\"></div>" })
      : route.abort());
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
          const id = `synthetic-overview-${window.overviewRequests.length + 1}`;
          window.overviewRequests.push({ id, options });
          return id;
        },
      }, { get: (target, key) => key in target ? target[key] : (..._args) => String(key).startsWith("on") ? () => {} : undefined });
      window.emitServer = event => { for (const handler of handlers) handler(event); };
      window.disposeUnified = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), client);
      window.syntheticSession = id => window.emitServer({ type: "session.banner", session_id: id,
        project_id: "synthetic-project", project_plan_status: "draft", topics: [], turn_mode: "agent", turn_mode_label: "", phase: "S4" });
      window.respondOverview = event => window.emitServer(event);
    });
    const waitForCalls = async expected => page.waitForFunction(count => window.overviewRequests.length >= count, expected, { timeout: 5000 });
    await page.evaluate(id => window.syntheticSession(id), sessionId);
    await waitForCalls(1);
    let calls = await page.evaluate(() => window.overviewRequests);
    await page.evaluate(event => window.respondOverview(event), overviewRequest(calls[0].id, []));
    await page.locator("#unified-request-current-work").click();
    await waitForCalls(2);
    calls = await page.evaluate(() => window.overviewRequests);
    const first = sourceGroup("one", [sourceItem("one-item-1", "source one first")], 0, "one-cursor");
    const second = sourceGroup("two", [sourceItem("two-item-1", "source two first")], 0, "two-cursor");
    await page.evaluate(event => window.respondOverview(event), overviewRequest(calls[1].id, [first, second]));

    const firstGroup = page.locator(".unified-current-work-group").filter({ hasText: "synthetic-source-one" });
    const button = firstGroup.locator(".unified-current-work-detail-more");
    assert.equal(await button.count(), 1, "the linked source snapshot exposes its server cursor button");
    await button.click();
    await waitForCalls(3);
    calls = await page.evaluate(() => window.overviewRequests);
    assert.equal(calls[2].options.cursor, "one-cursor", "click routes the snapshot cursor to the overview query");

    const firstPage = sourceGroup("one", [sourceItem("one-item-2", "source one second")], 1, null);
    const response = overviewRequest(calls[2].id, [firstPage]);
    response.detail_page = firstPage.items_page;
    await page.evaluate(event => window.respondOverview(event), response);
    await firstGroup.getByText("source one second").waitFor();
    assert.equal(await firstGroup.getByText("source two first").count(), 0);
    const secondGroup = page.locator(".unified-current-work-group").filter({ hasText: "synthetic-source-two" });
    assert.equal(await secondGroup.getByText("source two first").count(), 1,
      "a detail response focused on one source snapshot does not alter another group");
    await secondGroup.locator(".unified-current-work-detail-more").click();
    await waitForCalls(4);
    calls = await page.evaluate(() => window.overviewRequests);
    assert.equal(calls[3].options.cursor, "two-cursor");
    const changedGroup = sourceGroup("two", [sourceItem("two-item-from-new-snapshot", "cross snapshot poison")], 1, null);
    const changedSnapshot = overviewRequest(calls[3].id, [changedGroup], "overview-snapshot-b");
    changedSnapshot.detail_page = changedGroup.items_page;
    await page.evaluate(event => window.respondOverview(event), changedSnapshot);
    await waitForCalls(5);
    calls = await page.evaluate(() => window.overviewRequests);
    assert.equal(calls[4].options.cursor, undefined, "snapshot mismatch restarts the overview instead of merging a detail page");
    assert.equal(await page.getByText("cross snapshot poison").count(), 0,
      "a response from a changed overview snapshot is never mixed into the displayed source snapshot");
    assert.deepEqual(pageErrors, []);
    await page.evaluate(() => window.disposeUnified());
    await context.close();
  } finally {
    await browser.close();
  }
});






