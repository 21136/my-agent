import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const ts = createRequire(import.meta.url)("typescript");
const exports = {};
const url = new URL("../src/shells/unified/work-card-presentation.ts", import.meta.url);
const js = ts.transpileModule(readFileSync(url, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
new Function("exports", js)(exports);
const { WorkCardPresentationRegistry, renderWorkCardSnapshot, failPendingHistoryCardRead, requestHistoryCardRead, renderWorkCardEntry, isExplicitResultDetailExpansion, receiveWorkCardPresentation, buildWorkCardResultViewRequest } = exports;

const SESSION = "20261030-abcdef12";
const PRESENTATION = "7cfe09c0-4f54-4d04-9f14-9815b4374e58";
const CARD_ID = `wc-${"a".repeat(64)}`;
const card = (requirements = []) => ({
  card_id: CARD_ID,
  saved_at: "2026-10-30T01:00:00Z",
  viewed_at: null,
  snapshot: {
    kind: "project", session_id: SESSION, project_id: "demo-project",
    captured_at: "2026-10-30T00:59:00Z", completeness: "complete",
    item_count: 1, requirement_count: requirements.length, result_count: 0,
    omitted_item_count: 0, omitted_requirement_count: 0, omitted_result_count: 0,
    incomplete_reason: null, persisted: false, history_complete: true,
    missing_current_result_count: 0,
    cards: [{ session_id: SESSION, project_id: "demo-project", item_id: "item-1", title: "项目 <unsafe>", status: "in_progress", source_refs: [{ kind: "project", id: "src-1" }], requirements }],
  },
});
const req = (i, status = "unknown") => ({
  session_id: SESSION, project_id: "demo-project", item_id: "item-1",
  requirement_id: `req-${i}`, text: `要求 ${i} <script>alert(${i})</script>`, status,
  source_ref: { kind: "requirement", id: `req-${i}` }, latest_result: null,
  result_count: 0, omitted_result_count: 0, current_result_missing: false,
  current_run_id: null, latest_result_is_current: null,
});
const linkedCopy = (overrides = {}) => ({
  card_id: CARD_ID, saved_at: "2026-10-30T01:00:00Z", viewed_at: null,
  snapshot: {
    kind: "linked_item_copy", session_id: SESSION, captured_at: "2026-10-30T00:59:00Z",
    copy_id: "b".repeat(64), completeness_scope: "item_requirement", project_id: "demo-project",
    source_session_id: "20261030-12345678", source_item_digest: "c".repeat(64),
    binding_id: "binding-1", item_id: "item-1", item_title: "Linked item",
    item_status_at_capture: "completed", requirement_id: "req-1", requirement_text: "Linked requirement",
    requirement_status_at_capture: "completed", worker_id: "worker-1", worker_run_id: "worker-run-1",
    host_start: { reservation_id: "reservation-1", parent_run_id: "parent-run-1", turn_epoch: 3 },
    host_lifecycle: { sequence: 3, status: "model_turn_finished", finish_reason: "completed" },
    operation_result: "unknown", verification: "not_verified", ...overrides,
  },
});
const linkedProjectCopy = (itemCount = 2, requirementsPerItem = 3) => {
  const items = Array.from({ length: itemCount }, (_, i) => ({
    item_id: `item-${i + 1}`, title: `来源事项 ${i + 1}`, status: i === 0 ? "completed" : "in_progress",
    requirements: Array.from({ length: requirementsPerItem }, (_, j) => ({
      requirement_id: `req-${i + 1}-${j + 1}`, text: `来源要求 ${i + 1}-${j + 1}`,
      status: (i + j) % 2 === 0 ? "completed" : "blocked",
    })),
  }));
  const requirements = items.flatMap(item => item.requirements);
  const workerFacts = requirements.slice(0, 7).map((requirement, i) => ({
    binding_id: `binding-${i + 1}`, item_id: items[Math.floor(i / requirementsPerItem)].item_id,
    requirement_id: requirement.requirement_id, worker_id: `worker-${i + 1}`, worker_run_id: `worker-run-${i + 1}`,
    host_start: { reservation_id: `reservation-${i + 1}`, parent_run_id: `parent-run-${i + 1}`, turn_epoch: i + 1 },
    host_lifecycle: { sequence: i + 1, status: i === 1 ? "blocked" : "model_turn_finished", finish_reason: i === 1 ? "paused" : "completed" },
    operation_result: "unknown", verification: "not_verified",
  }));
  const anchor = workerFacts[0];
  return {
    card_id: CARD_ID, saved_at: "2026-10-30T01:00:00Z", viewed_at: null,
    snapshot: {
      kind: "linked_project_session_copy", session_id: SESSION, captured_at: "2026-10-30T00:59:00Z",
      copy_id: "d".repeat(64), binding_id: anchor.binding_id,
      source: {
        scope: "authorized_source_session_only", mapping_claim: "not_asserted", project_id: "demo-project",
        source_session_id: "20261030-12345678", source_revision: 17, items, worker_facts: workerFacts,
      },
    },
  };
};

test("received receipt is silent on success and result-view handler appears once", () => {
  const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  assert.equal([...source.matchAll(/case "work-card\.result\.view\.done":/g)].length, 1);
  const start = source.indexOf('case "work-card.received.done":');
  const end = source.indexOf('case "work-card.result.view.done":', start);
  assert.ok(start >= 0 && end > start);
  const handler = source.slice(start, end);
  assert.doesNotMatch(handler, /服务端已持久化客户端接收回执/);
  assert.match(handler, /statusText\.startsWith\("卡片接收回执发送失败"\)/);
  assert.match(handler, /服务端未确认客户端接收/);
});
test("backlog hint is one session-scoped status update and never requests or adds cards", () => {
  const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  const start = source.indexOf('case "work-card.receipt.backlog":');
  const end = source.indexOf('case "work-card.received.done":', start);
  assert.ok(start >= 0 && end > start);
  const handler = source.slice(start, end);
  assert.match(handler, /event\.session_id !== chat\.model\.sessionId/);
  assert.match(handler, /setStatus\(/);
  assert.doesNotMatch(handler, /listWorkCards|getWorkCard|queryWorkCards|blocks\.push/);
});
test("live present is session-scoped, strictly identified, and deduplicated", () => {
  const registry = new WorkCardPresentationRegistry(SESSION);
  const event = { type: "work-card.present", session_id: SESSION, presentation_id: PRESENTATION, card_id: CARD_ID, card: card() };
  assert.equal(registry.present(event).kind, "added");
  assert.equal(registry.present(event).kind, "duplicate");
  assert.equal(registry.present({ ...event, session_id: "other" }).kind, "rejected");
  assert.equal(registry.present({ ...event, presentation_id: `${PRESENTATION}\n` }).kind, "rejected");
  assert.equal(registry.present({ ...event, card_id: `wc-${"b".repeat(64)}` }).kind, "rejected");
});

test("presentation duplicates require exact card binding and merge an exact historical loading reference", () => {
  const registry = new WorkCardPresentationRegistry(SESSION);
  const event = { type: "work-card.present", session_id: SESSION, presentation_id: PRESENTATION, card_id: CARD_ID, card: card() };
  assert.equal(registry.present(event).kind, "added");
  const otherCardId = `wc-${"b".repeat(64)}`;
  assert.equal(registry.present({ ...event, card_id: otherCardId, card: { ...card(), card_id: otherCardId } }).kind, "rejected");
  assert.equal(registry.reference({presentationId:PRESENTATION,cardId:otherCardId,text:"conflicting history"}),null);
  registry.setSession("new-session");registry.setSession(SESSION);
  const history = registry.reference({ presentationId: PRESENTATION, cardId: CARD_ID, text: "history" });
  assert.equal(history.status, "loading");
  assert.equal(registry.present(event).kind, "resolved");
  assert.equal(registry.get(PRESENTATION).status, "resolved");
  assert.equal(registry.get(PRESENTATION).card.card_id, CARD_ID);
  assert.equal(registry.present({ ...event, card_id: otherCardId, card: { ...card(), card_id: otherCardId } }).kind, "rejected");
  assert.equal(registry.reference({presentationId:PRESENTATION,cardId:otherCardId,text:"conflicting history"}),null);
});
test("receipt follows accepted/merged exact presentation, retries after send failure, and is not a view", () => {
  const registry = new WorkCardPresentationRegistry(SESSION);
  const event = { type: "work-card.present", session_id: SESSION, presentation_id: PRESENTATION, card_id: CARD_ID, card: card() };
  const merged = new Set();const sent = [];let sendFails = true;
  const options = {
    registry, event,
    mergeReference: entry => { merged.add(`${entry.sessionId}:${entry.presentationId}:${entry.cardId}`); },
    sendReceived: identity => { sent.push(identity); if (sendFails) throw new Error("offline"); },
  };
  const first = receiveWorkCardPresentation(options);
  assert.equal(first.mutation.kind, "added");assert.equal(first.receiptAttempt, "failed");assert.equal(merged.size, 1);assert.equal(sent.length, 1);
  sendFails = false;
  const replay = receiveWorkCardPresentation(options);
  assert.equal(replay.mutation.kind, "duplicate");assert.equal(replay.receiptAttempt, "attempted");assert.equal(merged.size, 1);assert.equal(sent.length, 2);
  const otherCardId = `wc-${"b".repeat(64)}`;
  const conflict = receiveWorkCardPresentation({...options,event:{...event,card_id:otherCardId,card:{...card(),card_id:otherCardId}}});
  assert.equal(conflict.mutation.kind, "rejected");assert.equal(conflict.receiptAttempt, "not_eligible");assert.equal(sent.length, 2);

  const history = new WorkCardPresentationRegistry(SESSION);
  history.reference({presentationId:PRESENTATION,cardId:CARD_ID,text:"历史引用"});
  const beforeConflict = sent.length;
  const historyConflict = receiveWorkCardPresentation({...options,registry:history,event:{...event,card_id:otherCardId,card:{...card(),card_id:otherCardId}},sendReceived:identity=>sent.push(identity)});
  assert.equal(historyConflict.receiptAttempt,"not_eligible");assert.equal(sent.length,beforeConflict);
  const mergedHistory = receiveWorkCardPresentation({...options,registry:history,sendReceived:identity=>sent.push(identity)});
  assert.equal(mergedHistory.mutation.kind,"resolved");assert.equal(mergedHistory.receiptAttempt,"attempted");
  assert.equal(history.get(PRESENTATION).card.viewed_at,null,"receipt does not mark the card viewed");
});
test("history reference resolves only in its original current session and missing is explicit", () => {
  const registry = new WorkCardPresentationRegistry(SESSION);
  const block = registry.reference({ presentationId: PRESENTATION, cardId: CARD_ID, text: "工作卡片暂不可用" });
  assert.equal(block.status, "loading");
  assert.equal(registry.resolve(PRESENTATION, "other", card()).kind, "stale");
  assert.equal(registry.get(PRESENTATION).status, "loading");
  assert.equal(registry.resolve(PRESENTATION, SESSION, null).kind, "missing");
  assert.equal(registry.get(PRESENTATION).status, "missing");
});

test("resolved history card is scoped by both presentation and card identity", () => {
  const registry = new WorkCardPresentationRegistry(SESSION);
  registry.reference({ presentationId: PRESENTATION, cardId: CARD_ID, text: "ref" });
  assert.equal(registry.resolve(PRESENTATION, SESSION, { ...card(), card_id: `wc-${"b".repeat(64)}` }).kind, "rejected");
  assert.equal(registry.resolve(PRESENTATION, SESSION, card()).kind, "resolved");
  assert.equal(registry.get(PRESENTATION).status, "resolved");
});

test("switching sessions clears presentations and late results cannot repopulate", () => {
  const registry = new WorkCardPresentationRegistry(SESSION);
  registry.reference({ presentationId: PRESENTATION, cardId: CARD_ID, text: "ref" });
  registry.setSession("other");
  assert.equal(registry.resolve(PRESENTATION, SESSION, card()).kind, "stale");
  assert.equal(registry.get(PRESENTATION), undefined);
});

test("snapshot defaults to five prioritized known statuses and explicitly exposes all requirements", () => {
  const requirements = [req(1), req(2), req(3, "completed"), req(4, "blocked"), req(5), req(6, "in_progress"), req(7, "completed")];
  const html = renderWorkCardSnapshot(card(requirements));
  assert.match(html, /查看全部 7 项/);
  assert.match(html, /2026-10-30T00:59:00Z/);
  assert.match(html, /受阻/);
  assert.match(html, /进行中/);
  assert.equal((html.match(/<li class="work-card-requirement">/g) ?? []).length, 5);
  assert.equal((html.match(/class="work-card-requirement work-card-requirement-all"/g) ?? []).length, 7);
  assert.match(html, /src-1/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /当时状态/);
});

test("unique change focus moves a requirement from beyond the default five and keeps every row expandable", () => {
  const requirements = Array.from({ length: 7 }, (_, index) => req(index + 1, "completed"));
  const focused = requirements[6];
  focused.latest_result = {
    session_id: SESSION, project_id: "demo-project", item_id: "item-1",
    requirement_id: "req-7", result_id: "result-7", run_id: "run-7",
    status: "unknown", summary: "synthetic latest result", verification_status: "not_verified", sources: [],
  };
  const snapshot = card(requirements);
  snapshot.snapshot.change_focus = { item_id: "item-1", requirement_id: "req-7", result_id: "result-7" };

  const html = renderWorkCardSnapshot(snapshot);
  const visible = html.match(/<ol class="work-card-requirements">([\s\S]*?)<\/ol>/)?.[1] ?? "";
  const all = html.match(/<details class="work-card-all">[\s\S]*?<\/ol><\/details>/)?.[0] ?? "";

  assert.match(visible, /req-7/);
  assert.ok(visible.indexOf("req-7") < visible.indexOf("req-1"), "the uniquely bound update is shown first");
  assert.match(visible, /本次更新/);
  assert.equal((html.match(/class="work-card-change-focus"/g) ?? []).length, 2,
    "the same identity is labeled in the default view and complete expansion");
  assert.match(html, /查看全部 7 项/);
  for (let index = 1; index <= 7; index += 1) assert.match(all, new RegExp(`req-${index}`));
});

test("legacy cards and text-only mentions never infer a change focus", () => {
  const requirements = Array.from({ length: 7 }, (_, index) => req(index + 1, "completed"));
  requirements[6].text = "本次更新：请关注这项要求";
  const html = renderWorkCardSnapshot(card(requirements));
  const visible = html.match(/<ol class="work-card-requirements">([\s\S]*?)<\/ol>/)?.[1] ?? "";

  assert.doesNotMatch(html, /class="work-card-change-focus"/);
  assert.doesNotMatch(visible, /req-7/);
  assert.match(html, /查看全部 7 项/);
});

test("ambiguous duplicate focus identity is ignored instead of guessed", () => {
  const requirements = Array.from({ length: 7 }, (_, index) => req(index + 1, "completed"));
  const originalResult = {
    session_id: SESSION, project_id: "demo-project", item_id: "item-1",
    requirement_id: "req-7", result_id: "result-7", run_id: "run-7",
    status: "unknown", summary: "synthetic latest result", verification_status: "not_verified", sources: [],
  };
  requirements[6].latest_result = originalResult;
  const duplicate = { ...requirements[6], latest_result: {
    ...originalResult, result_id: "result-7-duplicate", run_id: "run-7-duplicate",
    summary: "different result under duplicate requirement identity",
  } };
  requirements.push(duplicate);
  const snapshot = card(requirements);
  snapshot.snapshot.requirement_count = requirements.length;
  snapshot.snapshot.change_focus = { item_id: "item-1", requirement_id: "req-7", result_id: "result-7" };

  const html = renderWorkCardSnapshot(snapshot);
  const visible = html.match(/<ol class="work-card-requirements">([\s\S]*?)<\/ol>/)?.[1] ?? "";
  assert.doesNotMatch(html, /class="work-card-change-focus"/);
  assert.doesNotMatch(visible, /req-7/);
});

test("change focus is ignored when the referenced result belongs to another snapshot scope", () => {
  const requirements = Array.from({ length: 7 }, (_, index) => req(index + 1, "completed"));
  requirements[6].latest_result = {
    session_id: "another-session", project_id: "demo-project", item_id: "item-1",
    requirement_id: "req-7", result_id: "result-7", run_id: "run-7",
    status: "unknown", summary: "foreign result", verification_status: "not_verified", sources: [],
  };
  const snapshot = card(requirements);
  snapshot.snapshot.change_focus = { item_id: "item-1", requirement_id: "req-7", result_id: "result-7" };

  const html = renderWorkCardSnapshot(snapshot);
  const visible = html.match(/<ol class="work-card-requirements">([\s\S]*?)<\/ol>/)?.[1] ?? "";
  assert.doesNotMatch(html, /class="work-card-change-focus"/);
  assert.doesNotMatch(visible, /req-7/);
});

test("independent snapshot does not acquire a no-project label", () => {
  const independent = card([req(1)]);
  independent.snapshot.kind = "independent";
  independent.snapshot.project_id = null;
  independent.snapshot.card = { ...independent.snapshot.cards[0], project_id: null, kind: "independent" };

  const html = renderWorkCardSnapshot(independent);
  assert.doesNotMatch(html, /无项目/);
  assert.match(html, /项目 &lt;unsafe&gt;/);
});



test("a synchronous history get failure clears the pending key and exposes the failure", () => {
  const registry = new WorkCardPresentationRegistry(SESSION);
  registry.reference({ presentationId: PRESENTATION, cardId: CARD_ID, text: "ref" });
  const pending = new Map([[CARD_ID, SESSION]]);
  const failed = failPendingHistoryCardRead({
    cardId: CARD_ID, sessionId: SESSION, currentSessionId: SESSION,
    pendingReads: pending, registry, message: "socket send failed",
  });
  assert.equal(failed, true);
  assert.equal(pending.has(CARD_ID), false);
  assert.equal(registry.get(PRESENTATION).status, "unavailable");
  assert.equal(registry.get(PRESENTATION).text, "socket send failed");
});

test("a stale history get failure cannot clear another session's pending read", () => {
  const registry = new WorkCardPresentationRegistry(SESSION);
  registry.reference({ presentationId: PRESENTATION, cardId: CARD_ID, text: "ref" });
  const pending = new Map([[CARD_ID, SESSION]]);
  const failed = failPendingHistoryCardRead({
    cardId: CARD_ID, sessionId: SESSION, currentSessionId: "other",
    pendingReads: pending, registry, message: "late failure",
  });
  assert.equal(failed, false);
  assert.equal(pending.get(CARD_ID), SESSION);
  assert.equal(registry.get(PRESENTATION).status, "loading");
});

test("requirements with five or fewer rows keep result, verification and sources available in row details", () => {
  const requirements = [req(1, "completed"), req(2, "in_progress")];
  requirements[0].latest_result = {
    session_id: SESSION, project_id: "demo-project", item_id: "item-1", requirement_id: "req-1",
    result_id: "result-1", run_id: "run-1", status: "completed", summary: "verified summary",
    verification_status: "passed", sources: [{ kind: "result", id: "result-source-1" }],
  };
  const html = renderWorkCardSnapshot(card(requirements));
  assert.match(html, /查看结果、验证与来源/);
  assert.match(html, /verified summary/);
  assert.match(html, /验证：passed/);
  assert.match(html, /result-source-1/);
  assert.match(html, /<details class="work-card-requirement-detail">/);
  assert.doesNotMatch(html, /work-card-all/);
});

test("a throwing work-card.get request immediately renders an unavailable history card", () => {
  const registry = new WorkCardPresentationRegistry(SESSION);
  registry.reference({ presentationId: PRESENTATION, cardId: CARD_ID, text: "ref" });
  const pending = new Map();
  const outcome = requestHistoryCardRead({
    cardId: CARD_ID, sessionId: SESSION, currentSessionId: SESSION,
    pendingReads: pending, registry,
    request: () => { throw new Error("websocket is not connected"); },
  });
  assert.equal(outcome, "failed");
  assert.equal(pending.has(CARD_ID), false);
  assert.equal(registry.get(PRESENTATION).status, "unavailable");
  assert.match(registry.get(PRESENTATION).text, /websocket is not connected/);
  assert.match(renderWorkCardEntry(registry.get(PRESENTATION)), /历史工作卡片读取失败/);
});


test("explicit result details carry exact presentation and result identity only for recorded results", () => {
  const snapshot = card([req(1, "completed"), req(2)]);
  snapshot.snapshot.cards[0].requirements[0].latest_result = {
    session_id: SESSION, project_id: "demo-project", item_id: "item-1", requirement_id: "req-1",
    result_id: "result-1", run_id: "run-1", status: "completed", summary: "visible result",
    verification_status: "passed", sources: [],
  };
  const registry = new WorkCardPresentationRegistry(SESSION);
  const entry = registry.present({ type: "work-card.present", session_id: SESSION,
    presentation_id: PRESENTATION, card_id: CARD_ID, card: snapshot }).entry;
  const html = renderWorkCardEntry(entry);
  assert.match(html, new RegExp(`data-presentation-id="${PRESENTATION}"`));
  assert.match(html, new RegExp(`data-result-id="result-1"`));
  assert.match(html, new RegExp(`data-requirement-id="req-1"`));
  assert.doesNotMatch(html, /data-result-id="undefined"/);
  const request = exports.buildWorkCardResultViewRequest(entry, "result-1", "req-1", SESSION);
  assert.deepEqual(request, { type: "work-card.result.view", session_id: SESSION,
    presentation_id: PRESENTATION, card_id: CARD_ID, result_id: "result-1", requirement_id: "req-1" });
  assert.equal(exports.buildWorkCardResultViewRequest(entry, "result-1", "req-2", SESSION), null);
  assert.equal(exports.buildWorkCardResultViewRequest(entry, "result-1", "req-1", "other"), null);
});


test("only an uncancelled trusted summary activation that changes closed to open counts as result expansion", () => {
  assert.equal(isExplicitResultDetailExpansion(true, false, false, true), true);
  assert.equal(isExplicitResultDetailExpansion(false, false, false, true), false); // script-dispatched click/toggle
  assert.equal(isExplicitResultDetailExpansion(true, true, false, true), false); // cancelled click
  assert.equal(isExplicitResultDetailExpansion(true, false, true, false), false); // collapse
  assert.equal(isExplicitResultDetailExpansion(true, false, false, false), false); // render/rebuild without open
  assert.equal(isExplicitResultDetailExpansion(false, false, false, true), false); // script-set open
});


test("project-scoped card is validated as one exact project's full projection in the current dialogue", () => {
  const scoped = card(Array.from({ length: 7 }, (_, index) => req(index + 1)));
  scoped.snapshot.kind = "project_scoped";
  scoped.snapshot.project_id = "demo-project";
  scoped.snapshot.cards[0].project_id = "demo-project";
  scoped.snapshot.cards[0].source_refs = [{ kind: "project", id: "demo-project" }];
  scoped.snapshot.cards[0].requirements.forEach((requirement, index) => {
    requirement.project_id = "demo-project";
    requirement.source_ref = { kind: "project", id: "demo-project" };
    requirement.current_result_missing = false;
    requirement.current_run_id = `run-${index + 1}`;
    requirement.latest_result_is_current = true;
    requirement.result_count = 1;
    requirement.latest_result = { session_id: SESSION, project_id: "demo-project", item_id: "item-1",
      requirement_id: requirement.requirement_id, result_id: `result-${index + 1}`, run_id: `run-${index + 1}`,
      status: "unknown", summary: "synthetic lifecycle pending", verification_status: "not_verified", sources: [] };
  });
  scoped.snapshot.result_count = 7;
  const registry = new WorkCardPresentationRegistry(SESSION);
  const event = { type: "work-card.present", session_id: SESSION, presentation_id: PRESENTATION, card_id: CARD_ID, card: scoped };
  const result = registry.present(event);
  assert.equal(result.kind, "added");
  const html = renderWorkCardEntry(result.entry);
  assert.match(html, /project_scoped/);
  assert.match(html, /项目：demo-project/);
  assert.match(html, /查看全部 7 项/);
  assert.equal((html.match(/class="work-card-requirement"/g) ?? []).length, 5);
  assert.equal((html.match(/class="work-card-requirement work-card-requirement-all"/g) ?? []).length, 7);
  assert.equal(result.entry.card.snapshot.cards[0].requirements.length, 7, "rendering keeps the complete snapshot");
  const allRequirements = html.match(/<details class="work-card-all">[\s\S]*?<\/ol><\/details>/)?.[0];
  assert.ok(allRequirements, "all requirements remain explicitly expandable");
  assert.match(allRequirements, /req-7/);

  const foreign = structuredClone(scoped);
  foreign.snapshot.cards[0].requirements[1].project_id = "another-project";
  const otherRegistry = new WorkCardPresentationRegistry(SESSION);
  assert.equal(otherRegistry.present({ ...event, card: foreign }).kind, "rejected");
});

test("linked item copy renders as one captured requirement, separate from worker outcome, with no result lookup", () => {
  const copy = linkedCopy();
  const registry = new WorkCardPresentationRegistry(SESSION);
  const event = { type: "work-card.present", session_id: SESSION, presentation_id: PRESENTATION, card_id: CARD_ID, card: copy };
  const result = registry.present(event);
  assert.equal(result.kind, "added");
  const html = renderWorkCardEntry(result.entry);
  assert.match(html, /来源单事项副本/);
  assert.match(html, /仅一项要求/);
  assert.match(html, /不是项目全览/);
  assert.match(html, /来源事项状态（捕获时）/);
  assert.match(html, /来源要求状态（捕获时）/);
  assert.match(html, /已完成/);
  assert.match(html, /工作器主机回合已结束/);
  assert.match(html, /操作结果未知/);
  assert.match(html, /未验证/);
  assert.match(html, /重新授权/);
  assert.doesNotMatch(html, /查看结果、验证与来源|data-work-card-result-detail|data-result-id/);
  assert.doesNotMatch(html, /查看全部|项目要求全览/);
  assert.equal(buildWorkCardResultViewRequest(result.entry, "result-1", "req-1", SESSION), null);
});

test("linked item copy rejects malformed certainty, identity, lifecycle and cross-session data", () => {
  const registry = new WorkCardPresentationRegistry(SESSION);
  const base = { type: "work-card.present", session_id: SESSION, presentation_id: PRESENTATION, card_id: CARD_ID };
  for (const snapshot of [
    { ...linkedCopy().snapshot, completeness_scope: "project" },
    { ...linkedCopy().snapshot, operation_result: "completed" },
    { ...linkedCopy().snapshot, verification: "verified" },
    { ...linkedCopy().snapshot, session_id: "20261030-deadbeef" },
    { ...linkedCopy().snapshot, copy_id: "not-a-digest" },
    { ...linkedCopy().snapshot, host_start: { ...linkedCopy().snapshot.host_start, turn_epoch: 4 } },
    { ...linkedCopy().snapshot, host_lifecycle: { ...linkedCopy().snapshot.host_lifecycle, sequence: 4 } },
    { ...linkedCopy().snapshot, unexpected: "field" },
  ]) {
    assert.equal(registry.present({ ...base, card: { ...linkedCopy(), snapshot } }).kind, "rejected");
    assert.match(renderWorkCardSnapshot({ ...linkedCopy(), snapshot }, { sessionId: SESSION, presentationId: PRESENTATION, cardId: CARD_ID }), /详情不可用/);
  }
  assert.equal(registry.present({ ...base, session_id: "20261030-deadbeef", card: linkedCopy() }).kind, "rejected");
});

test("linked item copy escapes copied source text and keeps legacy cards accepted", () => {
  const unsafe = linkedCopy();
  unsafe.snapshot.item_title = '<img src=x onerror="run()">';
  unsafe.snapshot.requirement_text = '<script>private()</script>';
  const html = renderWorkCardSnapshot(unsafe, { sessionId: SESSION, presentationId: PRESENTATION, cardId: CARD_ID });
  assert.match(html, /&lt;img src=x onerror=&quot;run\(\)&quot;&gt;/);
  assert.match(html, /&lt;script&gt;private\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<img|<script>/);
  assert.equal(new WorkCardPresentationRegistry(SESSION).present({
    type: "work-card.present", session_id: SESSION, presentation_id: PRESENTATION, card_id: CARD_ID, card: card(),
  }).kind, "added", "legacy project cards remain compatible");
});

test("linked project-session copy renders bounded captured requirements separately from worker receipts", () => {
  const copy = linkedProjectCopy(2, 4);
  const registry = new WorkCardPresentationRegistry(SESSION);
  const event = { type: "work-card.present", session_id: SESSION,
    presentation_id: PRESENTATION, card_id: CARD_ID, card: copy };
  const result = registry.present(event);
  assert.equal(result.kind, "added");
  const html = renderWorkCardEntry(result.entry);
  assert.match(html, /指定来源会话快照/);
  assert.match(html, /不代表项目全部历史/);
  assert.match(html, /未断言当前映射/);
  assert.match(html, /来源事项状态（捕获时）/);
  assert.match(html, /来源要求状态（捕获时）/);
  assert.match(html, /来源修订（捕获时）：17/);
  assert.match(html, /工作器宿主生命周期/);
  assert.match(html, /操作结果未知/);
  assert.match(html, /未验证/);
  assert.match(html, /宿主回合结束和来源状态都不表示本次操作完成/);
  assert.doesNotMatch(html, /当前对话中的项目要求全览|最近结果：|data-work-card-result-detail|data-result-id|result_id/);
  assert.match(html, /查看全部 8 项要求/);
  assert.equal((html.match(/class="work-card-requirement"/g) ?? []).length, 5);
  assert.equal((html.match(/class="work-card-requirement work-card-requirement-all"/g) ?? []).length, 8);
  const full = html.match(/<details class="work-card-all">[\s\S]*?<\/details>/)?.[0];
  assert.ok(full);
  assert.match(full, /来源要求 2-4/);
  assert.match(html, /工作器宿主报告受阻/);
});

test("linked project-session copy rejects malformed, cross-session, mapped, or success-claim snapshots", () => {
  const base = linkedProjectCopy();
  const snapshot = base.snapshot;
  const bad = [
    { ...snapshot, session_id: "20261031-deadbeef" },
    { ...snapshot, completeness: "complete" },
    { ...snapshot, source: { ...snapshot.source, mapping_claim: "current" } },
    { ...snapshot, source: { ...snapshot.source, scope: "project_history" } },
    { ...snapshot, source: { ...snapshot.source, source_revision: -1 } },
    { ...snapshot, source: { ...snapshot.source, worker_facts: snapshot.source.worker_facts.map((fact, i) => i ? fact : { ...fact, operation_result: "completed" }) } },
    { ...snapshot, source: { ...snapshot.source, worker_facts: snapshot.source.worker_facts.map((fact, i) => i ? fact : { ...fact, verification: "verified" }) } },
    { ...snapshot, source: { ...snapshot.source, worker_facts: snapshot.source.worker_facts.map((fact, i) => i ? fact : { ...fact, requirement_id: "missing-req" }) } },
    { ...snapshot, source: { ...snapshot.source, worker_facts: snapshot.source.worker_facts.map((fact, i) => i ? fact : { ...fact, host_lifecycle: { ...fact.host_lifecycle, sequence: 99 } }) } },
    { ...snapshot, source: { ...snapshot.source, extra: "field" } },
  ];
  const registry = new WorkCardPresentationRegistry(SESSION);
  for (const invalid of bad) {
    assert.equal(registry.present({ type: "work-card.present", session_id: SESSION,
      presentation_id: PRESENTATION, card_id: CARD_ID, card: { ...base, snapshot: invalid } }).kind, "rejected");
    assert.match(renderWorkCardSnapshot({ ...base, snapshot: invalid },
      { sessionId: SESSION, presentationId: PRESENTATION, cardId: CARD_ID }), /详情不可用/);
  }
  assert.equal(registry.present({ type: "work-card.present", session_id: "20261031-deadbeef",
    presentation_id: PRESENTATION, card_id: CARD_ID, card: base }).kind, "rejected");
});

test("linked project-session copy safely escapes source content and adds no result detail target", () => {
  const unsafe = linkedProjectCopy(1, 1);
  unsafe.snapshot.source.items[0].title = '<img src=x onerror="run()">';
  unsafe.snapshot.source.items[0].requirements[0].text = '<script>doNotRun()</script>';
  const html = renderWorkCardSnapshot(unsafe, { sessionId: SESSION, presentationId: PRESENTATION, cardId: CARD_ID });
  assert.match(html, /&lt;img src=x onerror=&quot;run\(\)&quot;&gt;/);
  assert.match(html, /&lt;script&gt;doNotRun\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<img|<script>|data-work-card-result-detail|result_id/);
  assert.equal(buildWorkCardResultViewRequest({ sessionId: SESSION, presentationId: PRESENTATION,
    cardId: CARD_ID, status: "resolved", card: unsafe }, "result-1", "req-1", SESSION), null);
});

test("project worker lifecycle machine JSON becomes bounded readable evidence, never completion", () => {
  const scoped = card([req(1)]);
  scoped.snapshot.kind = "project_scoped";
  scoped.snapshot.project_id = "demo-project";
  const requirement = scoped.snapshot.cards[0].requirements[0];
  scoped.snapshot.cards[0].project_id = "demo-project";
  scoped.snapshot.cards[0].source_refs = [{ kind: "project", id: "demo-project" }];
  requirement.project_id = "demo-project";
  requirement.source_ref = { kind: "project", id: "demo-project" };
  requirement.latest_result = {
    session_id: SESSION, project_id: "demo-project", item_id: "item-1", requirement_id: "req-1",
    result_id: "result-1", run_id: "run-1", status: "unknown", verification_status: "not_verified", sources: [],
    summary: JSON.stringify({
      kind: "project_worker_host_lifecycle", result_status: "unknown", project_id: "demo-project",
      task_id: `task-${"a".repeat(64)}`, worker_id: "worker-secret-id", worker_run_id: "run-1",
      reservation_id: "reservation-secret-id", parent_run_id: "22222222-2222-4222-8222-222222222222",
      parent_epoch: 2, host_status: "model_turn_finished", acceptance_status: "not_run",
      write_receipts_available: true,
      write_receipts: [{ path: "src/output.ts", status: "written", verification: "readback", characters_read_back: 42,
        error_code: "", readback_succeeded: true }],
    }),
  };
  const html = renderWorkCardSnapshot(scoped);
  assert.match(html, /主机回合已结束/);
  assert.match(html, /src\/output\.ts/);
  assert.match(html, /读回 42 字符/);
  assert.match(html, /验收未运行/);
  assert.doesNotMatch(html, /worker-secret-id|reservation-secret-id|parent_run_id|project_worker_host_lifecycle/);
  assert.doesNotMatch(html, /状态：已完成|最近结果：.*completed/);
});

test("malformed lifecycle JSON is suppressed instead of leaking machine fields", () => {
  const snapshot = card([req(1)]);
  snapshot.snapshot.cards[0].requirements[0].latest_result = {
    session_id: SESSION, project_id: "demo-project", item_id: "item-1", requirement_id: "req-1",
    result_id: "result-1", run_id: "run-1", status: "unknown", verification_status: "not_verified", sources: [],
    summary: JSON.stringify({ kind: "project_worker_host_lifecycle", worker_id: "must-not-leak", host_status: "finished" }),
  };
  const html = renderWorkCardSnapshot(snapshot);
  assert.match(html, /机器回执未通过校验/);
  assert.doesNotMatch(html, /must-not-leak/);
});

test("linked snapshot explicit detail action is session-bound and never marks view on send failure", () => {
  for (const copy of [linkedCopy(), linkedProjectCopy()]) {
    const registry = new WorkCardPresentationRegistry(SESSION);
    const entry = registry.present({type:"work-card.present",session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD_ID,card:copy}).entry;
    const html = renderWorkCardEntry(entry);
    assert.match(html, /<details[^>]*data-work-card-snapshot-detail/);
    assert.match(html, /<summary>查看详情<\/summary>/);
    const expected = {type:"work-card.snapshot.view",session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD_ID};
    assert.deepEqual(exports.buildWorkCardSnapshotViewRequest(entry,SESSION),expected);
    assert.equal(exports.buildWorkCardSnapshotViewRequest(entry,"other"),null);
    assert.equal(exports.sendWorkCardSnapshotView(entry,SESSION,()=>{throw new Error("offline")}),"failed");
    assert.equal(entry.card.viewed_at,null);
    let calls=0;
    assert.equal(exports.sendWorkCardSnapshotView(entry,SESSION,(request)=>{calls++;assert.deepEqual(request,expected)}),"sent");
    assert.equal(calls,1); assert.equal(entry.card.viewed_at,null);
  }
  const registry = new WorkCardPresentationRegistry(SESSION);
  const ordinary=registry.present({type:"work-card.present",session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD_ID,card:card()}).entry;
  assert.equal(exports.buildWorkCardSnapshotViewRequest(ordinary,SESSION),null);
});

test("snapshot view handler requires real uncancelled opening and checks active session at toggle", () => {
  const prevElement=globalThis.Element, prevDetails=globalThis.HTMLDetailsElement;
  class Element { closest(selector) { return selector === "summary" ? this : this.detail; } }
  class Details extends Element { constructor(article) { super(); this.open=false; this.article=article; } hasAttribute(name){return name === "data-work-card-snapshot-detail";} closest(){return this.article;} }
  globalThis.Element=Element; globalThis.HTMLDetailsElement=Details;
  try {
    const handlers=new Map(), container={addEventListener:(t,f,c)=>handlers.set(`${t}:${!!c}`,f),removeEventListener(){}};
    const registry=new WorkCardPresentationRegistry(SESSION);
    const entry=registry.present({type:"work-card.present",session_id:SESSION,presentation_id:PRESENTATION,card_id:CARD_ID,card:linkedCopy()}).entry;
    let active=SESSION, sent=0, errors=0;
    exports.bindWorkCardSnapshotViewEvents(container,{currentSessionId:()=>active,getEntry:()=>entry,send:()=>sent++,onError:()=>errors++});
    const detail=new Details({dataset:{sessionId:SESSION,presentationId:PRESENTATION,cardId:CARD_ID}}), summary=new Element(); summary.detail=detail;
    const click=(trusted,prevented=false)=>{const e={target:summary,isTrusted:trusted,defaultPrevented:prevented};handlers.get("click:true")(e);handlers.get("click:false")(e);};
    click(false);detail.open=true;handlers.get("toggle:true")({target:detail});assert.equal(sent,0);
    detail.open=false;click(true,true);detail.open=true;handlers.get("toggle:true")({target:detail});assert.equal(sent,0);
    detail.open=false;click(true);active="other";detail.open=true;handlers.get("toggle:true")({target:detail});assert.equal(sent,0);
    active=SESSION;detail.open=false;click(true);detail.open=true;handlers.get("toggle:true")({target:detail});assert.equal(sent,1);assert.equal(errors,0);
  } finally {globalThis.Element=prevElement;globalThis.HTMLDetailsElement=prevDetails;}
});

test("project-scoped pre-start blocked snapshot renders the requirement as blocked, not started or accepted", () => {
  const blocked = card([req(1, "blocked")]);
  blocked.snapshot.kind = "project_scoped";
  blocked.snapshot.project_id = "demo-project";
  const item = blocked.snapshot.cards[0];
  item.project_id = "demo-project";
  item.status = "unknown"; // Backend keeps the aggregate item unknown; the pre-start requirement/result are blocked.
  item.source_refs = [{ kind: "project", id: "demo-project" }];
  const requirement = item.requirements[0];
  requirement.project_id = "demo-project";
  requirement.status = "blocked";
  requirement.source_ref = { kind: "project", id: "demo-project" };
  requirement.current_result_missing = false;
  requirement.current_run_id = null;
  requirement.latest_result_is_current = null;
  requirement.result_count = 1;
  requirement.latest_result = {
    session_id: SESSION, project_id: "demo-project", item_id: item.item_id,
    requirement_id: requirement.requirement_id, result_id: "prestart-block-result",
    run_id: "prestart-block-run", status: "blocked", summary: JSON.stringify({
      kind: "project_worker_host_lifecycle", result_status: "blocked", session_id: SESSION,
      item_id: item.item_id, requirement_id: requirement.requirement_id, project_id: "demo-project",
      task_id: `task-${"a".repeat(64)}`, worker_id: "private-worker-id",
      worker_run_id: "prestart-block-run", reservation_id: "private-reservation-id",
      parent_run_id: "22222222-2222-4222-8222-222222222222", parent_epoch: 1,
      host_status: "blocked", host_error_code: "project_or_target_scope_invalid",
      worker_execution_started: false, worker_result_claimed: false, acceptance_status: "not_run",
    }), verification_status: "not_verified", sources: [],
  };
  blocked.snapshot.result_count = 1;
  const registry = new WorkCardPresentationRegistry(SESSION);
  const event = { type: "work-card.present", session_id: SESSION,
    presentation_id: PRESENTATION, card_id: CARD_ID, card: blocked };
  const accepted = registry.present(event);
  assert.equal(accepted.kind, "added");
  const html = renderWorkCardEntry(accepted.entry);
  assert.match(html, /受阻/);
  assert.match(html, /工作器尚未启动/);
  assert.match(html, /执行前检查受阻/);
  assert.match(html, /项目或目标范围未通过安全预检/);
  assert.match(html, /验收未运行/);
  assert.doesNotMatch(html, /private-worker-id|private-reservation-id|project_worker_host_lifecycle|host_error_code/);
  assert.doesNotMatch(html, /进行中|已启动|主机回合已结束|已完成|验收通过/);
  for (const unknownCode of ["opaque-internal-code-7d3", "__proto__", "toString"]) {
    const unknownCodeCard = structuredClone(blocked);
    const unknownReceipt = JSON.parse(unknownCodeCard.snapshot.cards[0].requirements[0].latest_result.summary);
    unknownReceipt.host_error_code = unknownCode;
    unknownCodeCard.snapshot.cards[0].requirements[0].latest_result.summary = JSON.stringify(unknownReceipt);
    const unknownHtml = renderWorkCardSnapshot(unknownCodeCard);
    assert.match(unknownHtml, /具体受阻原因无法安全识别/);
    assert.doesNotMatch(unknownHtml, new RegExp(unknownCode));
    assert.doesNotMatch(unknownHtml, /host_error_code/);
  }
  const uuidHex = structuredClone(blocked);
  const hexReceipt = JSON.parse(uuidHex.snapshot.cards[0].requirements[0].latest_result.summary);
  hexReceipt.parent_run_id = hexReceipt.parent_run_id.replaceAll("-", "");
  uuidHex.snapshot.cards[0].requirements[0].latest_result.summary = JSON.stringify(hexReceipt);
  assert.equal(new WorkCardPresentationRegistry(SESSION).present({ ...event, card: uuidHex }).kind, "added");
  const forged = structuredClone(blocked);
  forged.snapshot.cards[0].requirements[0].latest_result.project_id = "another-project";
  const foreignRegistry = new WorkCardPresentationRegistry(SESSION);
  assert.equal(foreignRegistry.present({ ...event, card: forged }).kind, "rejected");
});

test("malformed pre-start blocked lifecycle JSON is hidden instead of being surfaced as a claim", () => {
  const snapshot = card([req(1, "blocked")]);
  snapshot.snapshot.kind = "project_scoped";
  snapshot.snapshot.project_id = "demo-project";
  const item = snapshot.snapshot.cards[0];
  item.project_id = "demo-project";
  item.status = "unknown";
  item.source_refs = [{ kind: "project", id: "demo-project" }];
  const requirement = item.requirements[0];
  requirement.project_id = "demo-project";
  requirement.source_ref = { kind: "project", id: "demo-project" };
  requirement.current_result_missing = false;
  requirement.current_run_id = null;
  requirement.latest_result_is_current = null;
  requirement.result_count = 1;
  requirement.latest_result = {
    session_id: SESSION, project_id: "demo-project", item_id: item.item_id,
    requirement_id: requirement.requirement_id, result_id: "blocked-result",
    run_id: "blocked-run", status: "blocked", verification_status: "not_verified", sources: [],
    summary: JSON.stringify({
      kind: "project_worker_host_lifecycle", result_status: "blocked", session_id: SESSION,
      item_id: item.item_id, requirement_id: requirement.requirement_id, project_id: "demo-project",
      task_id: `task-${"a".repeat(64)}`, worker_id: "must-not-leak",
      worker_run_id: "blocked-run", reservation_id: "private-reservation",
      parent_run_id: "22222222-2222-4222-8222-222222222222", parent_epoch: 1,
      host_status: "blocked", host_error_code: "preflight", worker_execution_started: true,
      worker_result_claimed: false, acceptance_status: "not_run",
    }),
  };
  const html = renderWorkCardSnapshot(snapshot);
  assert.match(html, /机器回执未通过校验/);
  assert.match(html, /受阻/);
  assert.doesNotMatch(html, /must-not-leak|private-reservation|project_worker_host_lifecycle|host_error_code/);
  assert.doesNotMatch(html, /已启动|验收通过|已完成/);
});
