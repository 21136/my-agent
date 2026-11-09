/**
 * Workbench crash/reopen: 当前工作 shows honest recovery labels and reachable controls.
 * Pairs with agent-core/tests/test_workbench_crash_reopen_recovery_harness.py (no GUI).
 */
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const ts = createRequire(import.meta.url)("typescript");
const cache = new Map();
function loadTs(relativePath) {
  const url = new URL(relativePath, import.meta.url);
  if (cache.has(url.href)) return cache.get(url.href);
  const exports = {};
  cache.set(url.href, exports);
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const localRequire = (specifier) => {
    if (specifier.startsWith(".")) {
      return loadTs(new URL(specifier.endsWith(".ts") ? specifier : `${specifier}.ts`, url));
    }
    return createRequire(import.meta.url)(specifier);
  };
  new Function("exports", "require", js)(exports, localRequire);
  return exports;
}

const { buildCurrentWorkOverviewPanel, shouldShowCurrentWorkOverview } = loadTs(
  "../src/shells/unified/current-work-overview.ts",
);

const basePage = {
  page_size: 6, offset: 0, total_groups: 0, total_visible_groups: 0, total_visible_items: 0,
  total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null,
};
const emptyCounts = { visible_groups: 0, visible_items: 0, collapsed_groups: 0, collapsed_items: 0 };

/** Fixture mirrors post-session.open recovery from the Python harness. */
const recoveryOverview = {
  type: "work-records.overview.result",
  session_id: "20261109-crash-reopen",
  request_id: "11111111-1111-4111-8111-111111111111",
  state: "visible",
  unread_status: "unknown",
  visibility_limitations: [],
  visibility_counts: { visible_groups: 2, visible_items: 3, collapsed_groups: 0, collapsed_items: 0 },
  snapshot_id: null,
  page: { ...basePage, total_groups: 2, total_visible_groups: 2, total_visible_items: 3 },
  groups: [
    {
      kind: "independent",
      id: "current",
      visibility: "visible",
      status: "unknown",
      status_counts: { unknown: 1, in_progress: 1, queued: 1 },
      item_count: 2,
      requirement_count: 3,
      items: [
        {
          item_id: "crashed",
          title: "crashed worker task",
          status: "unknown",
          status_counts: { unknown: 1 },
          requirements: [{ requirement_id: "stale", text: "stale in-flight requirement", status: "unknown" }],
          current_results: [],
        },
        {
          item_id: "live",
          title: "same-process live task",
          status: "in_progress",
          status_counts: { in_progress: 1, queued: 1 },
          requirements: [
            { requirement_id: "live", text: "live in-flight requirement", status: "in_progress" },
            { requirement_id: "queued", text: "queued stop target", status: "queued" },
          ],
          current_results: [],
        },
      ],
    },
    {
      kind: "project",
      id: "project-alpha",
      visibility: "visible",
      status: "in_progress",
      status_counts: { in_progress: 1 },
      item_count: 1,
      requirement_count: 1,
      items: [{
        item_id: "worker",
        title: "project worker task",
        status: "in_progress",
        status_counts: { in_progress: 1 },
        requirements: [{ requirement_id: "w", text: "cancel still reachable", status: "in_progress" }],
        current_results: [],
      }],
    },
  ],
};

const claimRecoveryOverview = {
  ...recoveryOverview,
  session_id: "20261109-claim-unknown",
  groups: [{
    kind: "project",
    id: "project-beta",
    source_session_id: "20261109-claim-unknown",
    source_revision: 3,
    visibility: "visible",
    status: "unknown",
    status_counts: { unknown: 1 },
    item_count: 1,
    requirement_count: 1,
    items: [{
      item_id: "claim-item",
      title: "claimed worker",
      status: "unknown",
      status_counts: { unknown: 1 },
      requirements: [{
        requirement_id: "claim-req",
        text: "claim needs human inspection",
        status: "unknown",
        claim_recovery_needed: true,
      }],
      current_results: [],
    }],
  }],
  visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
  page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 },
};

test("post-reopen overview stays visible with conservative labels, not fake in-progress copy", () => {
  assert.equal(shouldShowCurrentWorkOverview(recoveryOverview), true);
  const panel = buildCurrentWorkOverviewPanel(recoveryOverview, { open: true });
  assert.equal(panel.visible, true);
  assert.match(panel.bodyHtml, /stale in-flight requirement · 结果待确认/);
  assert.match(panel.bodyHtml, /live in-flight requirement · 执行状态待确认/);
  assert.doesNotMatch(panel.bodyHtml, /正在执行|执行中|进行中/);
  assert.doesNotMatch(panel.bodyHtml, /stale in-flight requirement · 执行状态待确认/);
});

test("cancel remains on live in-progress project worker after reopen", () => {
  const panel = buildCurrentWorkOverviewPanel(recoveryOverview, { open: true });
  assert.match(panel.bodyHtml, /data-action="cancel_execution" data-expected-status="in_progress">请求停止 worker/);
});

test("unknown schedule claim exposes inspect path without pretending cancel stopped the worker", () => {
  const panel = buildCurrentWorkOverviewPanel(claimRecoveryOverview, { open: true });
  assert.match(panel.bodyHtml, /claim needs human inspection · 结果待确认/);
  assert.match(panel.bodyHtml, /data-action="inspect_unknown_claim" data-expected-status="unknown"/);
  assert.doesNotMatch(panel.bodyHtml, /data-action="cancel_execution"[^>]*claim-req/);
});
