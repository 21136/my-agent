import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const ts = createRequire(import.meta.url)("typescript");
const tsCache = new Map();
function loadTs(relativeOrUrl) {
  const url = relativeOrUrl instanceof URL ? relativeOrUrl : new URL(relativeOrUrl, import.meta.url);
  if (tsCache.has(url.href)) return tsCache.get(url.href);
  const exports = {}; tsCache.set(url.href, exports);
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const localRequire = specifier => {
    if (specifier.startsWith(".")) return loadTs(new URL(specifier.endsWith(".ts") ? specifier : `${specifier}.ts`, url));
    return createRequire(import.meta.url)(specifier);
  };
  new Function("exports", "require", js)(exports, localRequire);
  return exports;
}
const overviewModule = loadTs("../src/shells/unified/current-work-overview.ts");
const { dispatchCurrentWorkOverviewRequest, CURRENT_WORK_OVERVIEW_QUERY, shouldShowCurrentWorkOverview, buildCurrentWorkOverviewPanel, appendCurrentWorkOverviewPage, shouldRestartOverviewAfterExpiredCursor, shouldFallbackOverviewDetailPaging, shouldRefreshOverviewForExecutionState, shouldScheduleOverviewVisibilityRecheck, currentWorkOverviewRecheckDelayMs, CURRENT_WORK_VISIBILITY_RECHECK_MS, collectWorkRecordDependencyRefs, renderCurrentWorkShellPanel } = overviewModule;

const basePage = { page_size: 6, offset: 0, total_groups: 0, total_visible_groups: 0, total_visible_items: 0,
  total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null };
const emptyCounts = { visible_groups: 0, visible_items: 0, collapsed_groups: 0, collapsed_items: 0 };
const overview = (overrides = {}) => ({
  type: "work-records.overview.result", session_id: "s1", request_id: "11111111-1111-4111-8111-111111111111",
  state: "no_records", groups: [], unread_important_results: [], unread_status: "known", visibility_limitations: [],
  visibility_counts: emptyCounts, snapshot_id: null, page: basePage, ...overrides,
});
function completedViewedGroup() {
  return { kind: "project", id: "p1", visibility: "visible", status: "completed", status_counts: { completed: 1 },
    item_count: 1, requirement_count: 1,
    items: [{ item_id: "i1", title: "done", status: "completed", status_counts: { completed: 1 },
      requirements: [{ requirement_id: "r1", text: "done", status: "completed" }],
      current_results: [{ requirement_id: "r1", result_id: "res1", status: "completed", read_status: "viewed" }] }] };
}

test("in-progress project requirements expose worker stop for local and mapped remote sources", () => {
  const group = { kind: "project", id: "project-alpha", visibility: "visible", status: "in_progress",
    status_counts: { in_progress: 1 }, item_count: 1, requirement_count: 1,
    items: [{ item_id: "item-live", title: "live task", status: "in_progress", status_counts: { in_progress: 1 },
      requirements: [{ requirement_id: "req-live", text: "work", status: "in_progress" }], current_results: [] }] };
  const result = overview({ state: "visible", groups: [group], unread_status: "unknown",
    visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 } });
  const panel = buildCurrentWorkOverviewPanel(result, { open: true });
  assert.match(panel.bodyHtml, /data-action="cancel_execution" data-expected-status="in_progress">请求停止 worker/);
  const remote = { ...group, source_session_id: "source-session", source_revision: 4 };
  const remotePanel = buildCurrentWorkOverviewPanel(overview({ state: "visible", groups: [remote], unread_status: "unknown",
    visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 } }), { open: true });
  assert.match(remotePanel.bodyHtml, /data-action="cancel_execution"[^>]*data-source-project-id="project-alpha"/);
  assert.match(remotePanel.bodyHtml, /data-source-revision="4"/);
});

test("natural-language query dispatch remains available and respects current-session send guards", () => {
  const sent = [];
  assert.equal(dispatchCurrentWorkOverviewRequest({ busy: false, allowed: true, send: value => sent.push(value) }), "sent");
  assert.deepEqual(sent, [CURRENT_WORK_OVERVIEW_QUERY]);
  assert.match(CURRENT_WORK_OVERVIEW_QUERY, /当前工作整体概况/);
  assert.equal(dispatchCurrentWorkOverviewRequest({ busy: true, allowed: true, send: value => sent.push(value) }), "busy");
  assert.equal(dispatchCurrentWorkOverviewRequest({ busy: false, allowed: false, send: value => sent.push(value) }), "unavailable");
  assert.equal(dispatchCurrentWorkOverviewRequest({ busy: false, allowed: true, send: () => { throw new Error("offline"); } }), "failed");
  assert.deepEqual(sent, [CURRENT_WORK_OVERVIEW_QUERY]);
});

test("confirmed multi-project overview remains visible, identifies its source and scopes remote controls", () => {
  const remote = { kind: "project", id: "project-beta", source_session_id: "20261107-00000002", source_revision: 7,
    visibility: "visible", status: "queued", status_counts: { queued: 1 }, item_count: 1, requirement_count: 1,
    items: [{ item_id: "remote-item", title: "beta task", priority: 4, status: "queued", status_counts: { queued: 1 },
      requirements: [{ requirement_id: "remote-req", text: "work safely", status: "queued", depends_on: [],
        history: [{ run_id: "old-run", result_id: "old-result", status: "blocked", summary: "stopped at scope gate",
          verification_status: "not_verified", recorded_at: 10 }] }], current_results: [] }] };
  const result = overview({ state: "visible", aggregation_scope: "selected_project_sessions",
    selected_project_ids: ["project-beta"], unread_status: "unknown", visibility_limitations: ["cross_session_result_read_receipts_not_projected"],
    groups: [remote], visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 } });
  assert.equal(shouldShowCurrentWorkOverview(result), true);
  const panel = buildCurrentWorkOverviewPanel(result, { open: true });
  assert.match(panel.bodyHtml, /来源项目 project-beta · 会话 20261107-00000002 · 记录版本 7/);
  assert.match(panel.bodyHtml, /历史尝试/);
  assert.match(panel.bodyHtml, /data-work-record-control[^>]*data-source-project-id="project-beta"/);
  assert.match(panel.bodyHtml, /data-open-source-session="20261107-00000002"/);
  assert.match(panel.bodyHtml, /data-work-record-new-text-for="remote-req"/);
  assert.match(panel.bodyHtml, /data-action="revise_requirement"/);
  assert.match(panel.bodyHtml, /状态待确认|结果查看状态未知/);
});

test("dependencies omitted from authorized local and remote projections render as unverified and escaped", () => {
  const hostileId = 'missing<"&dependency';
  const makeGroup = (kind, id) => ({ kind, id, visibility: "visible", status: "pending", status_counts: { pending: 1 },
    item_count: 1, requirement_count: 1, source_session_id: "synthetic-source", source_revision: 1,
    items: [{ item_id: "synthetic-item", title: "synthetic task", status: "pending", status_counts: { pending: 1 },
      requirements: [{ requirement_id: "synthetic-requirement", text: "safe <requirement>", status: "pending", depends_on: [hostileId] }], current_results: [] }] });
  for (const group of [makeGroup("independent", "local"), makeGroup("project", "remote")]) {
    const panel = buildCurrentWorkOverviewPanel(overview({ state: "visible", groups: [group],
      visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
      page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 } }), { open: true });
    assert.match(panel.bodyHtml, /依赖关系（1）/);
    assert.match(panel.bodyHtml, /依赖未包含在当前授权事项中，状态无法核实/);
    assert.match(panel.bodyHtml, /missing&lt;&quot;&amp;dependency/);
    assert.match(panel.bodyHtml, /safe &lt;requirement&gt;/);
    assert.doesNotMatch(panel.bodyHtml, /missing<"&dependency|safe <requirement>/);
  }
});

test("independent cross-item dependencies render real per-requirement editable controls", () => {
  const local = { kind: "independent", id: "current", visibility: "visible", status: "pending",
    status_counts: { pending: 2 }, item_count: 2, requirement_count: 2,
    items: [
      { item_id: "upstream-item", title: "upstream task", status: "pending", status_counts: { pending: 1 },
        requirements: [{ requirement_id: "upstream-req", text: "finish upstream", status: "pending" }], current_results: [] },
      { item_id: "downstream-item", title: "downstream task", status: "pending", status_counts: { pending: 1 },
        requirements: [{ requirement_id: "downstream-req", text: "finish downstream", status: "pending" }], current_results: [] },
    ] };
  const panel = buildCurrentWorkOverviewPanel(overview({ state: "visible", groups: [local],
    visibility_counts: { visible_groups: 1, visible_items: 2, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 2 } }), { open: true });
  assert.match(panel.bodyHtml, /fieldset data-work-dependency-editor-for="downstream-req"/);
  assert.match(panel.bodyHtml, /data-work-dependency-ref-item="upstream-item" data-work-dependency-ref-requirement="upstream-req"/);
  assert.match(panel.bodyHtml, /data-action="set_dependencies"/);
});

test("dependency editor collects only the checked references for its own requirement", () => {
  const inputs = [
    { checked: true, dataset: { workDependencyRefItem: "upstream-a", workDependencyRefRequirement: "req-a" } },
    { checked: true, dataset: { workDependencyRefItem: "upstream-b", workDependencyRefRequirement: "req-b" } },
  ];
  const editors = [
    { dataset: { workDependencyEditorFor: "dependent-a" }, querySelectorAll: () => [inputs[0]] },
    { dataset: { workDependencyEditorFor: "dependent-b" }, querySelectorAll: () => [inputs[1]] },
  ];
  const item = { querySelectorAll: () => editors };
  assert.deepEqual(collectWorkRecordDependencyRefs(item, "dependent-a"), [
    { item_id: "upstream-a", requirement_id: "req-a" },
  ]);
  assert.deepEqual(collectWorkRecordDependencyRefs(item, "missing"), []);
});

test("independent current-session requirements expose a revision editor with no remote controls", () => {
  const local = { kind: "independent", id: "current", visibility: "visible", status: "pending",
    status_counts: { pending: 1 }, item_count: 1, requirement_count: 1,
    items: [{ item_id: "local-item", title: "local task", status: "pending", status_counts: { pending: 1 },
      requirements: [{ requirement_id: "local-req", text: "original", status: "pending" }], current_results: [] }] };
  const panel = buildCurrentWorkOverviewPanel(overview({ state: "visible", groups: [local],
    visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 } }), { open: true });
  assert.match(panel.bodyHtml, /data-work-record-new-text-for="local-req"/);
  assert.match(panel.bodyHtml, /data-action="revise_requirement"/);
  assert.match(panel.bodyHtml, /data-action="copy_requirement_to_new_item">复制到新事项/);
  assert.doesNotMatch(panel.bodyHtml, /data-source-project-id|data-open-source-session/);
  assert.doesNotMatch(panel.bodyHtml, /data-action="queue"/);
});

test("execution refresh triggers only on meaningful state transitions", () => {
  assert.equal(shouldRefreshOverviewForExecutionState(undefined, "queued"), true);
  assert.equal(shouldRefreshOverviewForExecutionState("queued", "running"), true);
  assert.equal(shouldRefreshOverviewForExecutionState("running", "running"), false);
  assert.equal(shouldRefreshOverviewForExecutionState("running", "settled"), true);
  assert.equal(shouldRefreshOverviewForExecutionState("running", "idle"), false);
});
test("unknown certainty before the first result keeps a retryable entry visible", () => {
  const pending = buildCurrentWorkOverviewPanel(null, { open: false, unavailable: true });
  assert.equal(pending.visible, true);
  assert.match(pending.label, /正在核实/);
  assert.match(pending.bodyHtml, /结果返回前不隐藏入口/);
});

test("30-minute collapse follows authoritative visibility, not read acknowledgements", () => {
  assert.equal(shouldShowCurrentWorkOverview(overview()), false);
  const justViewed = overview({ state: "visible", groups: [completedViewedGroup()],
    unread_important_results_total: 0, unread_important_results_truncated: false,
    visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 },
  });
  assert.equal(justViewed.unread_status, "known");
  assert.deepEqual(justViewed.unread_important_results, []);
  assert.equal(justViewed.groups[0].items[0].current_results[0].read_status, "viewed");
  assert.equal(shouldShowCurrentWorkOverview(justViewed), true, "recently viewed results stay visible until server projection collapses the group");
  assert.equal(shouldScheduleOverviewVisibilityRecheck(justViewed), true);  const stillActive = overview({ state: "visible", unread_status: "known", groups: [{ ...completedViewedGroup(),
    status: "in_progress", status_counts: { in_progress: 1 } }], visibility_counts: {
    visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0,
  }, page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 } });
  assert.equal(shouldScheduleOverviewVisibilityRecheck(stillActive), true, "low-frequency reads can refresh independently while visible work remains active");
  assert.equal(CURRENT_WORK_VISIBILITY_RECHECK_MS, 10 * 60 * 1000);
  assert.equal(currentWorkOverviewRecheckDelayMs({ ...justViewed, visibility_recheck_after_ms: 17 * 60 * 1000 }), 17 * 60 * 1000);
  assert.equal(currentWorkOverviewRecheckDelayMs(justViewed), 10 * 60 * 1000);
  assert.equal(currentWorkOverviewRecheckDelayMs({ ...justViewed, visibility_recheck_after_ms: 5 }), 10 * 60 * 1000);
  assert.equal(shouldShowCurrentWorkOverview(justViewed), true, "a local timer suggestion never hides the overview");
  assert.match(buildCurrentWorkOverviewPanel(justViewed, { open: false }).label, /已查看，待收起/);

  const maturedAndCollapsed = overview({ state: "collapsed", groups: [],
    unread_important_results_total: 0, unread_important_results_truncated: false,
    visibility_counts: { visible_groups: 0, visible_items: 0, collapsed_groups: 1, collapsed_items: 1 },
    page: { ...basePage, total_groups: 0, total_visible_groups: 0, total_collapsed_groups: 1, total_collapsed_items: 1 },
  });
  assert.equal(maturedAndCollapsed.unread_status, "known");
  assert.deepEqual(maturedAndCollapsed.unread_important_results, []);
  assert.equal(shouldShowCurrentWorkOverview(maturedAndCollapsed), false, "only the server's post-wait collapsed projection hides the entry");
  assert.equal(shouldScheduleOverviewVisibilityRecheck(maturedAndCollapsed), false);
});
test("unknown, errors, unread results, and unexpanded server pages fail open", () => {
  assert.equal(shouldShowCurrentWorkOverview(overview({ unread_status: "unknown" })), true);
  assert.equal(shouldScheduleOverviewVisibilityRecheck(overview({ state: "visible", unread_status: "unknown",
    visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 } })), true,
  "missing evidence keeps a low-frequency retry without hiding the entry");
  for (const state of ["error", "cancelled", "expired"]) assert.equal(shouldShowCurrentWorkOverview(overview({ state })), true);
  assert.equal(shouldShowCurrentWorkOverview(overview({ unread_important_results: [{ result_id: "r" }] })), true);
  assert.equal(shouldShowCurrentWorkOverview(overview({ state: "visible", groups: [{ ...completedViewedGroup(), status: "blocked", status_counts: { blocked: 1 } }],
    visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 },
  })), true);
  assert.equal(shouldShowCurrentWorkOverview(overview({ state: "visible", groups: [completedViewedGroup()],
    visibility_counts: { visible_groups: 8, visible_items: 8, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 8, total_visible_groups: 8, total_visible_items: 8, has_more: true, next_cursor: "next" },
  })), true);
});

test("detail paging negotiation falls back only for explicit unsupported-protocol responses", () => {
  assert.equal(shouldFallbackOverviewDetailPaging(true, "error", "invalid_request"), true);
  assert.equal(shouldFallbackOverviewDetailPaging(true, "error", "invalid_detail_paging"), true);
  assert.equal(shouldFallbackOverviewDetailPaging(true, "error", "detail_paging_required"), true);
  assert.equal(shouldFallbackOverviewDetailPaging(false, "error", "invalid_request"), false);
  assert.equal(shouldFallbackOverviewDetailPaging(true, "error", "source_unavailable"), false);
  assert.equal(shouldFallbackOverviewDetailPaging(true, "expired", "source_snapshot_changed"), false);
});

test("unread summary uses validated total, lower bound, or unknown instead of sample length", () => {
  const samples = Array.from({ length: 100 }, (_, index) => ({
    group: ["project", "p1"], item_id: `i-${index}`, requirement_id: `r-${index}`, result_id: `result-${index}`,
  }));
  const exact = overview({ state: "visible", unread_important_results: samples,
    unread_important_results_total: 101, unread_important_results_truncated: true,
    visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    groups: [{ ...completedViewedGroup(), status: "blocked" }],
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 } });
  const exactPanel = buildCurrentWorkOverviewPanel(exact, { open: false });
  assert.match(exactPanel.label, /未读 101/);
  assert.match(exactPanel.bodyHtml, /101 项未读结果/);
  assert.equal(shouldShowCurrentWorkOverview(exact), true);

  const lowerBound = overview({ state: "collapsed", groups: [], unread_important_results: samples,
    unread_important_results_truncated: true, visibility_counts: { visible_groups: 0, visible_items: 0, collapsed_groups: 1, collapsed_items: 1 },
    page: { ...basePage, total_groups: 0, total_visible_groups: 0, total_collapsed_groups: 1, total_collapsed_items: 1 } });
  const lowerPanel = buildCurrentWorkOverviewPanel(lowerBound, { open: false });
  assert.equal(lowerPanel.visible, true);
  assert.match(lowerPanel.label, /至少 100/);
  assert.match(lowerPanel.bodyHtml, /至少 100 项未读结果/);

  const legacyUnknown = overview({ state: "collapsed", groups: [], unread_important_results: [],
    visibility_counts: { visible_groups: 0, visible_items: 0, collapsed_groups: 1, collapsed_items: 1 },
    page: { ...basePage, total_groups: 0, total_visible_groups: 0, total_collapsed_groups: 1, total_collapsed_items: 1 } });
  const legacyPanel = buildCurrentWorkOverviewPanel(legacyUnknown, { open: false });
  assert.equal(legacyPanel.visible, true, "missing legacy counts must not cause current-work to disappear");
  assert.match(legacyPanel.label, /未读结果数未知/);
  assert.match(legacyPanel.bodyHtml, /未读结果数未知/);
});

test("cursor pages append only for the same server snapshot and expired cursors restart", () => {
  const groups = Array.from({ length: 6 }, (_, index) => ({ ...completedViewedGroup(), id: `p${index}` }));
  const current = overview({ state: "visible", groups, visibility_counts: {
    visible_groups: 7, visible_items: 7, collapsed_groups: 0, collapsed_items: 0,
  }, snapshot_id: "same", page: { ...basePage, total_groups: 7, total_visible_groups: 7,
    total_visible_items: 7, has_more: true, next_cursor: "cursor-1" } });
  const next = overview({ state: "visible", groups: [{ ...completedViewedGroup(), id: "p6" }],
    visibility_counts: current.visibility_counts, snapshot_id: "same", page: { ...basePage, offset: 6,
      total_groups: 7, total_visible_groups: 7, total_visible_items: 7 } });
  assert.equal(appendCurrentWorkOverviewPage(current, next).groups.length, 7);
  assert.equal(appendCurrentWorkOverviewPage(current, { ...next, snapshot_id: "changed" }), null);
  assert.equal(appendCurrentWorkOverviewPage(current, { ...next, page: { ...next.page, offset: 5 } }), null);
  assert.equal(shouldRestartOverviewAfterExpiredCursor("expired", "cursor-1"), true);
  assert.equal(shouldRestartOverviewAfterExpiredCursor("expired"), false);
});
test("overview panel renders only visible groups and trusts server pagination counts", () => {
  const visible = completedViewedGroup();
  visible.status = "blocked";
  visible.status_counts = { blocked: 1, pending: 2, completed: 1 };
  visible.requirement_count = 4;
  visible.items[0].status = "blocked";
  visible.items[0].status_counts = { blocked: 1, pending: 2, completed: 1 };
  visible.items[0].requirements = [
    { requirement_id: "rb", text: "blocked", status: "blocked" },
    { requirement_id: "rp1", text: "pending 1", status: "pending" },
    { requirement_id: "rp2", text: "pending 2", status: "pending" },
    { requirement_id: "rc", text: "completed", status: "completed" },
  ];
  const collapsed = { ...completedViewedGroup(), id: "old", visibility: "collapsed", items: [{ ...completedViewedGroup().items[0], title: "must not render" }] };
  const panel = buildCurrentWorkOverviewPanel(overview({ state: "visible", groups: [visible, collapsed],
    visibility_counts: { visible_groups: 7, visible_items: 9, collapsed_groups: 1, collapsed_items: 1 },
    page: { ...basePage, total_groups: 7, total_visible_groups: 7, total_visible_items: 9,
      total_collapsed_groups: 1, total_collapsed_items: 1, has_more: true, next_cursor: "next" },
  }), { open: true });
  assert.equal(panel.visible, true);
  assert.equal(panel.canExpand, true);
  assert.match(panel.moreLabel, /6 组/);
  assert.match(panel.bodyHtml, /项目 · p1/);
  assert.match(panel.bodyHtml, /<span class="unified-current-work-group-status">受阻<\/span>/);
  assert.match(panel.bodyHtml, /<span class="unified-current-work-group-counts">受阻 1 · 待开始 2 · 已记录完成 1<\/span>/);
  assert.doesNotMatch(panel.bodyHtml, /must not render|项目 · old/);
});

test("large project groups show all attention items first and bound ordinary items with an explicit remainder", () => {
  const makeItem = (index, status, readStatus = "viewed") => ({
    item_id: `item-${index}`, title: `synthetic item ${index}`, status,
    status_counts: { [status]: 1 },
    requirements: [{ requirement_id: `req-${index}`, text: `synthetic requirement ${index}`, status }],
    current_results: status === "completed" || status === "unknown"
      ? [{ requirement_id: `req-${index}`, result_id: `result-${index}`, status, read_status: readStatus }]
      : [],
  });
  const items = [
    makeItem(1, "blocked"), makeItem(2, "unknown"), makeItem(3, "in_progress"),
    makeItem(4, "completed", "unread"), makeItem(5, "completed", "unknown"),
    ...Array.from({ length: 7 }, (_, index) => makeItem(index + 6, index === 0 ? "pending" : "completed")),
  ];
  const group = { kind: "project", id: "project-large", visibility: "visible", status: "blocked",
    status_counts: { blocked: 1, unknown: 1, in_progress: 1, pending: 1, completed: 8 },
    item_count: items.length, requirement_count: items.length, items: items.slice(0, 5),
    items_page: { kind: "items", group_kind: "project", group_id: "project-large", item_id: null,
      offset: 0, total: items.length, has_more: true, next_cursor: "cursor-items-5" } };
  const panel = buildCurrentWorkOverviewPanel(overview({ state: "visible", groups: [group],
    visibility_counts: { visible_groups: 1, visible_items: items.length, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: items.length },
  }), { open: true });
  const groupHtml = panel.bodyHtml.match(/<article class="unified-current-work-group">[\s\S]*?<\/article>/)?.[0] ?? "";
  const moreAt = groupHtml.indexOf('class="unified-current-work-items-more"');
  const pageButtonAt = groupHtml.indexOf('data-current-work-detail-kind="items"');
  const endAt = [moreAt, pageButtonAt].filter(index => index >= 0).sort((a, b) => a - b)[0] ?? groupHtml.length;
  const initial = groupHtml.slice(0, endAt);

  for (let index = 1; index <= 5; index += 1) assert.match(initial, new RegExp(`synthetic item ${index}`));
  assert.equal((initial.match(/class="unified-current-work-item"/g) ?? []).length, 5,
    "attention rows are prioritized inside the bounded server page");
  assert.match(groupHtml, /还有 7 项/);
  assert.match(groupHtml, /展开更多/);
  assert.match(groupHtml, /data-current-work-detail-cursor="cursor-items-5"/);
});

test("all attention items remain in the initial project overview even when they exceed the ordinary batch size", () => {
  const items = Array.from({ length: 8 }, (_, index) => ({
    item_id: `attention-${index}`, title: `attention item ${index}`, status: "blocked",
    status_counts: { blocked: 1 },
    requirements: [{ requirement_id: `attention-req-${index}`, text: "synthetic blocked requirement", status: "blocked" }],
    current_results: [],
  }));
  const group = { kind: "project", id: "project-attention", visibility: "visible", status: "blocked",
    status_counts: { blocked: items.length }, item_count: items.length,
    requirement_count: items.length, items };
  const panel = buildCurrentWorkOverviewPanel(overview({ state: "visible", groups: [group],
    visibility_counts: { visible_groups: 1, visible_items: items.length, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: items.length },
  }), { open: true });
  const groupHtml = panel.bodyHtml.match(/<article class="unified-current-work-group">[\s\S]*?<\/article>/)?.[0] ?? "";
  const moreAt = groupHtml.indexOf('class="unified-current-work-items-more"');
  const pageButtonAt = groupHtml.indexOf('data-current-work-detail-kind="items"');
  const endAt = [moreAt, pageButtonAt].filter(index => index >= 0).sort((a, b) => a - b)[0] ?? groupHtml.length;
  const initial = groupHtml.slice(0, endAt);
  for (let index = 0; index < items.length; index += 1) assert.match(initial, new RegExp(`attention item ${index}`));
  assert.doesNotMatch(groupHtml, /unified-current-work-detail-kind="items"/);
});

test("paged overview details expose server cursors and merge all item and requirement pages without mixing sessions", () => {
  const req = (id, status = "pending") => ({ requirement_id: id, text: `text-${id}`, status });
  const requirementPage = (itemId, offset, total, nextCursor) => ({ kind: "requirements", group_kind: "project",
    group_id: "project-large", item_id: itemId, offset, total, has_more: nextCursor !== null, next_cursor: nextCursor });
  const item = (id, requirements, offset, total, nextCursor) => ({ item_id: id, title: id, status: "pending",
    status_counts: { pending: total }, requirements, current_results: [],
    requirements_page: requirementPage(id, offset, total, nextCursor) });
  const initialItems = Array.from({ length: 5 }, (_, index) => item(`item-${index}`,
    index === 0 ? Array.from({ length: 5 }, (_unused, reqIndex) => req(`req-0-${reqIndex}`)) : [req(`req-${index}-0`)],
    0, index === 0 ? 11 : 1, index === 0 ? "cursor-req-5" : null));
  const itemsPage = { kind: "items", group_kind: "project", group_id: "project-large", item_id: null,
    offset: 0, total: 8, has_more: true, next_cursor: "cursor-items-5" };
  const group = { kind: "project", id: "project-large", visibility: "visible", status: "pending",
    status_counts: { pending: 8 }, item_count: 8, requirement_count: 18, items: initialItems, items_page: itemsPage };
  const current = overview({ state: "visible", groups: [group], snapshot_id: "snapshot-a",
    visibility_counts: { visible_groups: 1, visible_items: 8, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 8 } });
  const html = buildCurrentWorkOverviewPanel(current, { open: true }).bodyHtml;
  assert.match(html, /还有 3 项/);
  assert.match(html, /data-current-work-detail-kind="items"/);
  assert.match(html, /data-current-work-detail-cursor="cursor-items-5"/);
  assert.match(html, /要求（1\/1）/);

  const nextItems = [item("item-5", [req("req-5-0")], 0, 1, null), item("item-6", [req("req-6-0")], 0, 1, null),
    item("item-7", [req("req-7-0")], 0, 1, null)];
  const nextItemsPage = { ...itemsPage, offset: 5, has_more: false, next_cursor: null };
  const itemPageEvent = overview({ state: "visible", session_id: current.session_id, snapshot_id: "snapshot-a",
    groups: [{ ...group, items: nextItems, items_page: nextItemsPage }], detail_page: nextItemsPage,
    visibility_counts: current.visibility_counts,
    page: { ...current.page, total_visible_items: 7 } });
  const withItems = overviewModule.mergeCurrentWorkOverviewDetailPage(current, itemPageEvent);
  assert.equal(withItems.groups[0].items.length, 8);
  assert.equal(withItems.groups[0].items_page.has_more, false);
  assert.equal(overviewModule.mergeCurrentWorkOverviewDetailPage(withItems, itemPageEvent), null,
    "duplicate item pages cannot append twice");

  const focused = withItems.groups[0].items[0];
  const reqHtml = buildCurrentWorkOverviewPanel(withItems, { open: true }).bodyHtml;
  assert.match(reqHtml, /还有 6 项要求/);
  assert.match(reqHtml, /data-current-work-detail-kind="requirements"/);
  assert.match(reqHtml, /data-current-work-detail-item-id="item-0"/);
  assert.match(reqHtml, /data-current-work-detail-cursor="cursor-req-5"/);

  const nextRequirements = { ...focused,
    requirements: Array.from({ length: 5 }, (_, index) => req(`req-0-${index + 5}`)), current_results: [],
    requirements_page: requirementPage("item-0", 5, 11, "cursor-req-10") };
  const reqPageEvent = overview({ state: "visible", session_id: current.session_id, snapshot_id: "snapshot-a",
    groups: [{ ...withItems.groups[0], items: [nextRequirements], items_page: null }],
    detail_page: { ...nextRequirements.requirements_page }, visibility_counts: current.visibility_counts,
    page: current.page });
  const outOfOrder = overviewModule.mergeCurrentWorkOverviewDetailPage(withItems, {
    ...reqPageEvent, groups: [{ ...reqPageEvent.groups[0], items: [{ ...reqPageEvent.groups[0].items[0],
      requirements: [req("req-0-10")], requirements_page: requirementPage("item-0", 10, 11, null) }] }],
    detail_page: requirementPage("item-0", 10, 11, null),
  });
  assert.equal(outOfOrder, null, "out-of-order requirement pages cannot create a gap");
  const middle = overviewModule.mergeCurrentWorkOverviewDetailPage(withItems, reqPageEvent);
  assert.equal(middle.groups[0].items[0].requirements.length, 10);
  assert.equal(overviewModule.mergeCurrentWorkOverviewDetailPage(middle, reqPageEvent), null,
    "replayed requirement pages cannot append twice");
  assert.equal(middle.groups[0].items[0].requirements_page.next_cursor, "cursor-req-10");
  const lastRequirements = { ...focused, requirements: [req("req-0-10")], current_results: [],
    requirements_page: requirementPage("item-0", 10, 11, null) };
  const lastReqEvent = overview({ state: "visible", session_id: current.session_id, snapshot_id: "snapshot-a",
    groups: [{ ...middle.groups[0], items: [lastRequirements], items_page: null }],
    detail_page: { ...lastRequirements.requirements_page }, visibility_counts: current.visibility_counts,
    page: current.page });
  const complete = overviewModule.mergeCurrentWorkOverviewDetailPage(middle, lastReqEvent);
  assert.equal(complete.groups[0].items[0].requirements.length, 11);
  assert.equal(complete.groups[0].items[0].requirements_page.has_more, false);
  assert.equal(overviewModule.mergeCurrentWorkOverviewDetailPage(current,
    { ...itemPageEvent, session_id: "other-session" }), null, "late detail replies cannot cross sessions");
  assert.equal(overviewModule.mergeCurrentWorkOverviewDetailPage(current,
    { ...itemPageEvent, snapshot_id: "new-snapshot" }), null, "detail replies cannot merge across snapshots");
});

test("legacy full-response groups remain readable with explicit client-side remainder", () => {
  const items = Array.from({ length: 9 }, (_, index) => ({
    item_id: `legacy-${index}`, title: `legacy item ${index}`, status: index === 0 ? "blocked" : "completed",
    status_counts: { [index === 0 ? "blocked" : "completed"]: 1 },
    requirements: [{ requirement_id: `legacy-req-${index}`, text: `legacy requirement ${index}`, status: index === 0 ? "blocked" : "completed" }],
    current_results: [],
  }));
  const group = { kind: "project", id: "legacy-project", visibility: "visible", status: "blocked",
    status_counts: { blocked: 1, completed: 8 }, item_count: 9, requirement_count: 9, items };
  const panel = buildCurrentWorkOverviewPanel(overview({ state: "visible", groups: [group],
    visibility_counts: { visible_groups: 1, visible_items: 9, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 9 },
  }), { open: true });
  const groupHtml = panel.bodyHtml.match(/<article class="unified-current-work-group">[\s\S]*?<\/article>/)?.[0] ?? "";
  const firstBatch = groupHtml.split('<div class="unified-current-work-items-more">')[0];
  const remainder = groupHtml.match(/<div class="unified-current-work-items-more">[\s\S]*/)?.[0] ?? "";
  assert.equal((firstBatch.match(/class="unified-current-work-item"/g) ?? []).length, 5);
  assert.match(firstBatch, /legacy item 0/);
  assert.match(groupHtml, /还有 4 项（已优先显示需关注事项）/);
  for (let index = 5; index < 9; index += 1) assert.match(remainder, new RegExp(`legacy item ${index}`));
  assert.doesNotMatch(groupHtml, /data-current-work-detail-cursor/);
});

test("independent overview groups use their sole item title and never expose item ids", () => {
  const independent = { ...completedViewedGroup(), kind: "independent", id: "internal-item-id",
    items: [{ ...completedViewedGroup().items[0], item_id: "internal-item-id", title: "<修改简历>" }] };
  const multiple = { ...independent, id: "multi-item-id", items: [
    { ...independent.items[0], title: "first projected title" },
    { ...independent.items[0], item_id: "multi-item-id", title: "second projected title" },
  ] };
  const untitled = { ...independent, id: "untitled-item-id", items: [
    { ...independent.items[0], item_id: "untitled-item-id", title: "  " },
  ] };
  const groups = [independent, multiple, untitled];
  const panel = buildCurrentWorkOverviewPanel(overview({ state: "visible", groups,
    visibility_counts: { visible_groups: 3, visible_items: 3, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 3, total_visible_groups: 3, total_visible_items: 3 },
  }), { open: true });

  assert.match(panel.bodyHtml, /<h4>&lt;修改简历&gt;<\/h4>/);
  assert.equal((panel.bodyHtml.match(/<h4>独立事项组<\/h4>/g) ?? []).length, 2,
    "multiple projected items and blank titles use the generic fallback");
  const groupHeadings = [...panel.bodyHtml.matchAll(/<h4>(.*?)<\/h4>/g)].map(([, heading]) => heading);
  assert.deepEqual(groupHeadings, ["&lt;修改简历&gt;", "独立事项组", "独立事项组"]);
});
test("overview panel escapes stored text and marks unknown read certainty instead of hiding it", () => {
  const group = completedViewedGroup();
  group.items[0].title = "<img src=x>";
  group.items[0].current_results[0].read_status = "unknown";
  const panel = buildCurrentWorkOverviewPanel(overview({ state: "visible", unread_status: "unknown", groups: [group],
    visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 },
  }), { open: true });
  assert.equal(panel.visible, true);
  assert.match(panel.label, /状态待确认/);
  assert.match(panel.bodyHtml, /&lt;img src=x&gt;/);
  assert.match(panel.bodyHtml, /查看状态未知/);
  assert.match(panel.bodyHtml, /未读结果数未知/);
  assert.doesNotMatch(panel.bodyHtml, /0 项未读结果/);
});


test("requirements are collapsed per item while status, read markers, counts, and expansion identity remain available", () => {
  const group = completedViewedGroup();
  group.id = 'p"<1';
  group.items[0].item_id = "item-1";
  group.items[0].title = "<script>task</script>";
  group.items[0].status = "blocked";
  group.items[0].requirements.push({ requirement_id: "r2", text: "<img src=x>", status: "unknown" });
  group.items[0].current_results[0].read_status = "unread";
  const sameItemIdInAnotherProject = { ...group, id: "p2" };
  const result = overview({ state: "visible", unread_status: "unknown", groups: [group, sameItemIdInAnotherProject],
    visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 },
  });

  const panel = buildCurrentWorkOverviewPanel(result, { open: true });
  assert.match(panel.bodyHtml, /data-current-work-item-id="\[&quot;project&quot;,&quot;p\\&quot;&lt;1&quot;,&quot;item-1&quot;\]"/);
  assert.doesNotMatch(panel.bodyHtml, /data-selected="1"|is-selected/);
  assert.match(panel.bodyHtml, /<h5>要求（2）<\/h5>/);
  assert.match(panel.bodyHtml, /受阻/);
  assert.match(panel.bodyHtml, /未读结果/);
  assert.match(panel.bodyHtml, /未读结果数未知/);
  assert.match(panel.bodyHtml, /&lt;script&gt;task&lt;\/script&gt;/);
  assert.match(panel.bodyHtml, /&lt;img src=x&gt;/);
  assert.match(panel.bodyHtml, /项目 · p&quot;&lt;1/);

  const firstGroupKey = JSON.stringify(["project", 'p"<1', "item-1"]);
  const restored = buildCurrentWorkOverviewPanel(result, { open: true, expandedRequirementItemIds: new Set([firstGroupKey]) });
  assert.ok(((restored.bodyHtml.match(/data-selected="1"/g) ?? []).length) >= 1, "selected item reveals its detail panes");
  assert.match(restored.bodyHtml, /is-selected/);
  assert.doesNotMatch(restored.bodyHtml, /<details\b/, "no accordion fold markup");
});

const topbarSource = new URL("../src/shells/unified/topbar.ts", import.meta.url);
const topbarJs = ts.transpileModule(readFileSync(topbarSource, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const topbarExports = {};
new Function("exports", "require", topbarJs)(topbarExports, () => ({ escapeHtml: value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;") }));
const state = (currentWorkOverview) => ({ proposals: [], intentLabel: "", checkerLabel: "", memoryLabel: "", memoryCount: 0,
  pendingMemoryCount: 0, projectLabel: "", contextLabel: "", sessionCount: 0, headerCtas: [], currentWorkOverview });
test("topbar conditionally renders the structured entry that toggles the in-shell current-work panel", () => {
  const listeners = new Map();
  const elements = new Map();
  const makeButton = selector => ({ addEventListener: (event, listener) => listeners.set(selector, listener) });
  elements.set("#unified-request-current-work", makeButton("#unified-request-current-work"));
  const container = { innerHTML: "", classList: { add() {}, remove() {} }, querySelector: selector => elements.get(selector) ?? null, querySelectorAll: () => [] };
  topbarExports.renderTopbarV2(container, state({ visible: true, open: true, label: "当前工作", bodyHtml: "<b>live</b>",
    canExpand: true, moreLabel: "展开更多", loadingMore: false }), {
    onToggleCurrentWorkOverview() {}, onLoadMoreCurrentWorkOverview() {}, onRequestCurrentWorkOverview() {},
  });
  assert.match(container.innerHTML, /id="unified-request-current-work"/);
  assert.match(container.innerHTML, /aria-controls="unified-current-work-popover"/);
  assert.match(container.innerHTML, /在主界面展开或收起当前工作/);
  assert.doesNotMatch(container.innerHTML, /id="unified-current-work-more"|id="unified-current-work-ask"|class="unified-current-work-popover"/);
  assert.doesNotMatch(container.innerHTML, /cached cards|快照时间/);
  topbarExports.renderTopbarV2(container, state(undefined), { onRequestCurrentWorkOverview() {} });
  assert.doesNotMatch(container.innerHTML, /unified-request-current-work|查询当前概况/);
});
test("group and item details use precise conservative status labels", () => {
  const group = completedViewedGroup();
  group.status = "unknown";
  group.status_counts = { unknown: 1, in_progress: 1, unexpected: 1 };
  group.requirement_count = 3;
  group.items = [
    { ...group.items[0], title: "unknown item", status: "unknown", requirements: [
      { requirement_id: "u", text: "unknown requirement", status: "unknown" },
      { requirement_id: "x", text: "unrecognized requirement", status: "unexpected" },
    ] },
    { ...group.items[0], item_id: "i2", title: "in progress item", status: "in_progress", requirements: [
      { requirement_id: "p", text: "in progress requirement", status: "in_progress" },
    ] },
    { ...group.items[0], item_id: "i3", title: "unrecognized item", status: "unexpected", requirements: [] },
  ];
  const runningGroup = { ...completedViewedGroup(), id: "p2", status: "in_progress", status_counts: { in_progress: 1 } };
  const panel = buildCurrentWorkOverviewPanel(overview({ state: "visible", groups: [group, runningGroup],
    visibility_counts: { visible_groups: 2, visible_items: 4, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 2, total_visible_groups: 2, total_visible_items: 4 },
  }), { open: true });
  assert.match(panel.bodyHtml, /group-status">结果待确认<\/span>/);
  assert.match(panel.bodyHtml, /group-status">执行状态待确认<\/span>/);
  assert.match(panel.bodyHtml, /结果待确认 1 · 执行状态待确认 1 · 状态待确认 1/);
  assert.match(panel.bodyHtml, /<strong>unknown item<\/strong><span>结果待确认<\/span>/);
  assert.match(panel.bodyHtml, /<li>unknown requirement · 结果待确认<\/li>/);
  assert.match(panel.bodyHtml, /<strong>in progress item<\/strong><span>执行状态待确认<\/span>/);
  assert.match(panel.bodyHtml, /<li>in progress requirement · 执行状态待确认<\/li>/);
  assert.match(panel.bodyHtml, /<strong>unrecognized item<\/strong><span>状态待确认<\/span>/);
  assert.match(panel.bodyHtml, /<li>unrecognized requirement · 状态待确认<\/li>/);
  assert.doesNotMatch(panel.bodyHtml, /等待用户批准|正在执行|执行中/);
});


test("single-requirement history renders safely and exposes a fresh cursor for each authorized page", () => {
  const group = { ...completedViewedGroup(), source_session_id: "synthetic-source", source_revision: 4 };
  group.items[0].requirements[0].history = [{ run_id: "run-1", result_id: "result-1", status: "blocked",
    verification_status: "unknown", summary: "<img src=x onerror=alert(1)>", recorded_at: 1 }];
  group.items[0].requirements[0].history_page = { page_size: 1, offset: 0, has_more: true,
    next_cursor: "synthetic-cursor-2", scope: "this_requirement_only" };
  const result = overview({ state: "visible", aggregation_scope: "selected_project_sessions", selected_project_ids: ["p1"],
    groups: [group], visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 } });

  const firstPage = buildCurrentWorkOverviewPanel(result, { open: true }).bodyHtml;
  assert.match(firstPage, /历史尝试（1\/1\+）/);
  assert.match(firstPage, /data-requirement-history[^>]*data-project-id="p1"[^>]*data-item-id="i1"[^>]*data-requirement-id="r1"[^>]*data-cursor="synthetic-cursor-2"/);
  assert.match(firstPage, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(firstPage, /<img src=x/);

  const finalPage = { ...result, groups: [{ ...group, items: [{ ...group.items[0], requirements: [{
    ...group.items[0].requirements[0],
    history: [{ run_id: "run-2", result_id: "result-2", status: "completed", verification_status: "verified", summary: "synthetic final page", recorded_at: 2 }],
    history_page: { page_size: 1, offset: 1, has_more: false, next_cursor: null, scope: "this_requirement_only" },
  }] }] }] };
  const finalHtml = buildCurrentWorkOverviewPanel(finalPage, { open: true }).bodyHtml;
  assert.match(finalHtml, /历史尝试（1\/2）/);
  assert.match(finalHtml, /synthetic final page/);
  assert.doesNotMatch(finalHtml, /加载下一页历史/);
});

test("history errors remain visible as unavailable without rendering untrusted response text", () => {
  const error = buildCurrentWorkOverviewPanel(overview({ state: "error", error: "<script>unsafe</script>" }), { open: true });
  assert.equal(error.visible, true);
  assert.match(error.bodyHtml, /概况暂不可确认/);
  assert.doesNotMatch(error.bodyHtml, /<script>/);
});

test("source snapshot overview presents capture provenance without claiming live state", () => {
  const group = {kind:"linked_source_snapshot",id:"linked-source-"+"a".repeat(64),visibility:"visible",status:"unknown",status_counts:{unknown:1},item_count:1,requirement_count:2,attention_status:"unknown",attention_reason:"superseded_snapshot_unviewed",
    source_snapshot:{scope:"authorized_source_session_only",mapping_claim:"not_asserted",project_id:"p<1>",source_session_id:"source-1",source_revision:3,captured_at:"2026-10-30T01:00:00Z",copy_id:"a".repeat(64),intent_state:"reference_queued",card_id:null,viewed_at:null,superseded_snapshot_count:2,superseded_unviewed_count:1},
    items:[{item_id:"i",title:"源事项",status:"unknown",status_at_capture:"completed",status_counts:{unknown:1},current_results:[],requirements:[
      {requirement_id:"r1",text:"源要求",status:"unknown",status_at_capture:"in_progress",worker_facts:[]},
      {requirement_id:"r2",text:"未知要求",status:"unknown",status_at_capture:"future_status",worker_facts:[]}]}]};
  const result=overview({state:"visible",groups:[group],visibility_counts:{visible_groups:1,visible_items:1,collapsed_groups:0,collapsed_items:0},page:{...basePage,total_groups:1,total_visible_groups:1,total_visible_items:1}});
  const panel=buildCurrentWorkOverviewPanel(result,{open:true});
  assert.equal(panel.visible,true);
  assert.match(panel.bodyHtml,/来源会话/); assert.match(panel.bodyHtml,/source-1/); assert.match(panel.bodyHtml,/p&lt;1&gt;/);
  assert.match(panel.bodyHtml,/2026-10-30T01:00:00Z/); assert.match(panel.bodyHtml,/捕获时/);
  assert.match(panel.bodyHtml,/不代表当前映射/); assert.match(panel.bodyHtml,/未验证/);
  assert.match(panel.bodyHtml,/查看状态未确认/);
  const viewed = buildCurrentWorkOverviewPanel({...result,groups:[{...group,source_snapshot:{...group.source_snapshot,viewed_at:"2026-10-30T02:00:00Z"}}]},{open:true});
  assert.match(viewed.bodyHtml,/卡片查看时间.*2026-10-30T02:00:00Z/);
  assert.match(viewed.bodyHtml,/未验证/);
  assert.match(panel.bodyHtml,/旧快照.*1.*查看计时尚未确认/); assert.match(panel.bodyHtml,/源要求.*执行状态待确认/);
  assert.match(panel.bodyHtml,/未知要求.*状态待确认/); assert.doesNotMatch(panel.bodyHtml,/已接收.*已查看/);
});


test("source snapshot detail paging merges only the same snapshot without duplicate rows", () => {
  const id = "linked-source-" + "a".repeat(64);
  const page = (offset, hasMore) => ({kind:"items",group_kind:"linked_source_snapshot",group_id:id,item_id:null,offset,total:2,has_more:hasMore,next_cursor:hasMore?"next":null});
  const item = i => ({item_id:`i${i}`,title:`来源${i}`,status:"unknown",status_at_capture:"unknown",status_counts:{unknown:1},requirements:[],current_results:[]});
  const group = {kind:"linked_source_snapshot",id,visibility:"visible",status:"unknown",status_counts:{unknown:1},item_count:2,requirement_count:0,items:[item(1)],items_page:page(0,true),source_snapshot:{scope:"authorized_source_session_only",mapping_claim:"not_asserted",project_id:"p",source_session_id:"s",source_revision:1,captured_at:"2026-10-30T01:00:00Z",copy_id:"a".repeat(64),intent_state:"pending",card_id:null,viewed_at:null,superseded_snapshot_count:0,superseded_unviewed_count:0}};
  const current=overview({state:"visible",snapshot_id:"snap",groups:[group]});
  const next=overview({...current,groups:[{...group,items:[item(2)],items_page:page(1,false)}],detail_page:page(1,false)});
  const merged=overviewModule.mergeCurrentWorkOverviewDetailPage(current,next);
  assert.deepEqual(merged.groups[0].items.map(x=>x.item_id),["i1","i2"]);
  assert.equal(overviewModule.mergeCurrentWorkOverviewDetailPage(merged,next),null);
  assert.equal(overviewModule.mergeCurrentWorkOverviewDetailPage(current,{...next,snapshot_id:"other"}),null);
  assert.match(buildCurrentWorkOverviewPanel(merged,{open:true}).bodyHtml,/来源2/);
});

test("schedule preview is shown only for selected-project scope and remains read-only", () => {
  const preview = {
    state: "visible",
    candidates: [{ project_id: "<project>", item_id: "item-1", requirement_id: "ready&safe", priority: 4 }],
    blocked: [{ project_id: "project-b", item_id: "item-2", requirement_id: "waiting", priority: 2,
      reasons: ["dependency is pending", "<script>unsafe</script>"] }],
    limitations: ["Some selected projects are unavailable"],
    omissions: ["project-c was omitted"],
  };
  const selected = buildCurrentWorkOverviewPanel(overview({
    state: "visible", aggregation_scope: "selected_project_sessions", schedule_preview: preview,
    visibility_limitations: ["project_sessions_partial"],
    groups: [completedViewedGroup()],
    visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 },
  }), { open: true });
  assert.match(selected.bodyHtml, /只读调度预览/);
  assert.match(selected.bodyHtml, /不会启动任务，也不代表运行授权/);
  assert.match(selected.bodyHtml, /&lt;project&gt; · item-1 · ready&amp;safe · 优先级 4/);
  assert.match(selected.bodyHtml, /dependency is pending；&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.match(selected.bodyHtml, /Some selected projects are unavailable/);
  assert.match(selected.bodyHtml, /部分事项按保守范围展示/);
  assert.doesNotMatch(selected.bodyHtml, /<script>/);

  const local = buildCurrentWorkOverviewPanel(overview({
    state: "visible", schedule_preview: preview, groups: [completedViewedGroup()],
    visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 },
  }), { open: true });
  assert.doesNotMatch(local.bodyHtml, /只读调度预览|ready&amp;safe/,
    "a preview-shaped payload cannot be exposed without selected-project aggregation scope");

  const unavailable = buildCurrentWorkOverviewPanel(overview({
    state: "error", aggregation_scope: "selected_project_sessions",
    schedule_preview: { state: "unavailable", candidates: [], blocked: [], limitations: ["Preview unavailable"] },
  }), { open: true });
  assert.match(unavailable.bodyHtml, /概况暂不可确认/);
  assert.match(unavailable.bodyHtml, /当前没有可显示的候选/);
  assert.match(unavailable.bodyHtml, /Preview unavailable/);
  assert.doesNotMatch(unavailable.bodyHtml, /data-work-record-control/);
});

test("schedule tab empty state explains read-only multi-project preview entry", () => {
  const panel = buildCurrentWorkOverviewPanel(overview({
    state: "visible",
    groups: [completedViewedGroup()],
    visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 },
    page: { ...basePage, total_groups: 1, total_visible_groups: 1, total_visible_items: 1 },
  }), { open: true });
  assert.match(panel.bodyHtml, /只读预览/);
  assert.match(panel.bodyHtml, /选择项目来源/);
  assert.match(panel.bodyHtml, /查看所选项目/);
  assert.match(panel.bodyHtml, /不会自动开跑/);
  assert.match(panel.bodyHtml, /按预览启动/);
  assert.doesNotMatch(panel.bodyHtml, /当前没有调度预览；选择项目来源后会显示在这里。/);

  const extras = `<section data-selected-project-overview aria-label="选择项目来源"><h3>选择项目来源（2）</h3></section>`;
  const shell = renderCurrentWorkShellPanel(panel, extras, "schedule");
  assert.match(shell, /data-cw-segment="schedule"/);
  assert.match(shell, /data-cw-schedule-slot/);
  assert.match(shell, /选择项目来源（2）/);
});

test("source snapshot timer labels remain conservative and collapsed rows do not reappear", () => {
 const group={kind:"linked_source_snapshot",id:"linked-source-"+"a".repeat(64),visibility:"visible",status:"unknown",status_counts:{unknown:1},item_count:1,requirement_count:1,attention_status:"unknown",attention_reason:"captured_requirement_unresolved",visibility_timer_status:"waiting",source_snapshot:{scope:"authorized_source_session_only",mapping_claim:"not_asserted",project_id:"p",source_session_id:"s",source_revision:1,captured_at:"2026-10-30T01:00:00Z",copy_id:"a".repeat(64),intent_state:"reference_queued",card_id:null,viewed_at:null,superseded_snapshot_count:0,superseded_unviewed_count:0},items:[{item_id:"i",title:"捕获事项",status:"unknown",status_at_capture:"in_progress",status_counts:{unknown:1},requirements:[{requirement_id:"r",text:"捕获要求",status:"unknown",status_at_capture:"in_progress",worker_facts:[]}],current_results:[]}]};
 const mk = g=>overview({state:"visible",groups:[g],visibility_counts:{visible_groups:1,visible_items:1,collapsed_groups:0,collapsed_items:0},page:{...basePage,total_groups:1,total_visible_groups:1,total_visible_items:1}});
 for(const [timer,label] of [["waiting",/查看计时尚未完成/],["not_viewed",/明确查看尚未确认/],["unavailable",/查看计时证据不可用/],["unknown",/查看计时状态未知/],["elapsed",/查看计时已到期/]]) {
  const html=buildCurrentWorkOverviewPanel(mk({...group,visibility_timer_status:timer}),{open:true}).bodyHtml;
  assert.match(html,label);assert.match(html,/不代表当前映射/);assert.match(html,/未验证/);assert.doesNotMatch(html,/工作已完成|结果已验证/);
 }
 const collapsed=buildCurrentWorkOverviewPanel(mk({...group,visibility:"collapsed",attention_status:"none",attention_reason:"elapsed_terminal_capture",visibility_timer_status:"elapsed"}),{open:true});
 assert.doesNotMatch(collapsed.bodyHtml,/捕获事项/);
});

test("empty structured queue still mirrors pending batch authorization from the live session", () => {
  const panel = buildCurrentWorkOverviewPanel(overview({
    state: "visible",
    unread_status: "unknown",
    visibility_counts: emptyCounts,
    page: basePage,
  }), {
    open: true,
    liveSession: {
      confirmPending: true,
      confirmOverlay: {
        requestId: "confirm-batch",
        preview: "确认向 2 个项目派发 worker：music、luna",
      },
      turnActive: true,
      executionState: "running",
      blocks: [],
    },
  });
  assert.equal(panel.visible, true);
  assert.match(panel.label, /等待确认/);
  assert.doesNotMatch(panel.bodyHtml, /暂无可见工作组/);
  assert.match(panel.bodyHtml, /主会话授权/);
  assert.match(panel.bodyHtml, /music、luna/);
  assert.match(panel.bodyHtml, /授权在左侧对话输入区处理/);
});

test("companion orchestration work-instruction appears before work records exist", () => {
  const panel = buildCurrentWorkOverviewPanel(overview({
    state: "no_records",
    unread_status: "known",
    visibility_counts: emptyCounts,
    page: basePage,
  }), {
    open: true,
    liveSession: {
      confirmPending: false,
      confirmOverlay: null,
      turnActive: true,
      executionState: "running",
      blocks: [{
        kind: "work-instruction",
        text: "同时推进 music 与 luna 的边界测试",
        state: "orchestration_active",
      }],
    },
  });
  assert.match(panel.bodyHtml, /并行多项目编排/);
  assert.match(panel.bodyHtml, /各项目施工中/);
  assert.match(panel.bodyHtml, /music 与 luna/);
  assert.doesNotMatch(panel.bodyHtml, /暂无可见工作组/);
});

test("post-turn work-record confirms are not mirrored into the live queue", () => {
  const panel = buildCurrentWorkOverviewPanel(overview({
    state: "visible",
    unread_status: "known",
    groups: [],
    visibility_counts: emptyCounts,
    page: basePage,
  }), {
    open: true,
    liveSession: {
      confirmPending: false,
      confirmOverlay: null,
      turnActive: false,
      executionState: "settled",
      blocks: [{
        kind: "confirm",
        confirmKind: "work_record_post_turn",
        requestId: "post-turn",
        preview: "确认写入本次会话工作记录",
      }],
    },
  });
  assert.match(panel.bodyHtml, /暂无可见工作组/);
});
