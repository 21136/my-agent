/**
 * Companion 小魔王空间: pending work-record / verification confirms use composer bar, not in-chat cards.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const ts = createRequire(import.meta.url)("typescript");
const cache = new Map();
function load(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const exports = {};
  cache.set(url.href, exports);
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function("exports", "require", js)(exports, (id) => load(new URL(id.endsWith(".ts") ? id : `${id}.ts`, url)));
  return exports;
}

const chatState = load(new URL("../src/shells/chat-state.ts", import.meta.url));
const overview = load(new URL("../src/shells/unified/current-work-overview.ts", import.meta.url));
const preview = load(new URL("../src/shells/unified/confirm-preview.ts", import.meta.url));

const {
  canSubmitCompanionComposerConfirm,
  chatSessionAwaitingUserConfirm,
  companionHomeComposerOwnsOrdinaryConfirm,
  composerShouldShowOrdinaryConfirmActions,
  createChatSession,
  ordinaryConfirmWouldRenderInChat,
  primaryPendingConfirmPreview,
  resolveCompanionComposerConfirmRequestId,
} = chatState;

const { buildCurrentWorkOverviewPanel } = overview;

const companionUi = {
  mainFocus: "chat",
  perspective: "project",
};

function companionSession() {
  const chat = createChatSession({ showProcess: true, confirmInBlocks: false });
  chat.handleEvent({
    type: "session.banner",
    session_id: "companion-home",
    topics: [],
    turn_mode: "agent",
    turn_mode_label: "动手",
    phase: "S4",
  });
  return chat;
}

const emptyOverview = () => ({
  type: "work-records.overview.result",
  session_id: "companion-home",
  request_id: "11111111-1111-4111-8111-111111111111",
  state: "no_records",
  groups: [],
  unread_important_results: [],
  unread_status: "known",
  visibility_limitations: [],
  visibility_counts: { visible_groups: 0, visible_items: 0, collapsed_groups: 0, collapsed_items: 0 },
  snapshot_id: null,
  page: { page_size: 6, offset: 0, total_groups: 0, total_visible_groups: 0, total_visible_items: 0,
    total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null },
});

test("companion queues work_record_post_turn on composer, not in-chat card", () => {
  const chat = companionSession();
  assert.equal(companionHomeComposerOwnsOrdinaryConfirm(companionUi), true);
  chat.handleEvent({
    type: "confirm.request",
    confirm_kind: "work_record_post_turn",
    session_id: "companion-home",
    request_id: "post-turn-a",
    preview: "确认写入工作记录：推进文档",
    allow_approve_all: false,
  });
  const composerUi = {
    chatRendersOrdinaryConfirm: false,
    overlayRendersOrdinaryConfirm: false,
    companionComposerPrimary: true,
  };
  assert.equal(chatSessionAwaitingUserConfirm(chat.model, { companionComposerPrimary: true }), true);
  assert.equal(resolveCompanionComposerConfirmRequestId(chat.model, true), "post-turn-a");
  assert.equal(ordinaryConfirmWouldRenderInChat(chat.model, { companionComposerPrimary: true }), false);
  assert.equal(
    composerShouldShowOrdinaryConfirmActions(chat.model, composerUi),
    true,
  );
  assert.match(primaryPendingConfirmPreview(chat.model, { companionComposerPrimary: true }), /确认写入工作记录/);
  assert.equal(canSubmitCompanionComposerConfirm(chat.model, "post-turn-a"), true);
  const stripDetail = preview.renderComposerScopedConfirmDetailHtml(chat.model.blocks[0]);
  assert.match(stripDetail, /确认写入工作记录/);
  assert.doesNotMatch(stripDetail, /unified-confirm-kicker/);
});

test("ordinary tool confirm precedes queued post-turn confirms on companion", () => {
  const chat = companionSession();
  chat.handleEvent({
    type: "confirm.request",
    session_id: "companion-home",
    run_id: "run-a",
    request_id: "tool-first",
    preview: "软删除到 _trash workspace 项目",
    allow_approve_all: false,
  });
  chat.handleEvent({
    type: "confirm.request",
    confirm_kind: "work_record_post_turn",
    session_id: "companion-home",
    request_id: "post-turn-b",
    preview: "确认保存第二条结果",
    allow_approve_all: false,
  });
  assert.equal(resolveCompanionComposerConfirmRequestId(chat.model, true), "tool-first");
  chat.handleEvent({
    type: "confirm.done",
    session_id: "companion-home",
    request_id: "tool-first",
    choice: "y",
  });
  assert.equal(resolveCompanionComposerConfirmRequestId(chat.model, true), "post-turn-b");
});

test("companion FIFO: post_turn precedes verification after ordinary settles", () => {
  const chat = companionSession();
  const verification = {
    session_id: "companion-home",
    project_id: "proj",
    item_id: "item-a",
    requirement_id: "req-a",
    project_name: "Harness Project",
    requirement_text: "核对 PROJECT.md 写入",
    pytest_target: "tests/test_harness.py::test_target",
    worker_run_id: "worker-run",
    reservation_id: "reservation",
  };
  chat.handleEvent({ type: "execution.state", run_id: "run-queue", state: "running", sequence: 1 });
  chat.handleEvent({
    type: "confirm.request",
    session_id: "companion-home",
    run_id: "run-queue",
    request_id: "tool-first",
    preview: "软删除到 _trash workspace 项目",
    allow_approve_all: false,
  });
  chat.handleEvent({
    type: "confirm.request",
    confirm_kind: "work_record_post_turn",
    session_id: "companion-home",
    request_id: "post-turn-mid",
    preview: "确认写入工作记录：推进文档结果",
    allow_approve_all: false,
  });
  chat.handleEvent({
    type: "confirm.request",
    session_id: "companion-home",
    run_id: "run-queue",
    request_id: "verify-last",
    confirm_subtype: "project_worker_requirement_verification",
    preview: "运行单目标测试以记录自报告结果",
    allow_approve_all: false,
    project_worker_verification: verification,
  });
  chat.handleEvent({
    type: "confirm.done",
    session_id: "companion-home",
    run_id: "run-queue",
    request_id: "tool-first",
    choice: "y",
  });
  assert.equal(resolveCompanionComposerConfirmRequestId(chat.model, true), "post-turn-mid");
  assert.match(primaryPendingConfirmPreview(chat.model, { companionComposerPrimary: true }), /确认写入工作记录/);
  assert.equal(canSubmitCompanionComposerConfirm(chat.model, "post-turn-mid"), true);
  chat.handleEvent({
    type: "confirm.done",
    confirm_kind: "work_record_post_turn",
    session_id: "companion-home",
    request_id: "post-turn-mid",
    choice: "y",
  });
  assert.equal(resolveCompanionComposerConfirmRequestId(chat.model, true), "verify-last");
});

test("overview live queue hints composer while post-turn confirm is pending", () => {
  const chat = companionSession();
  chat.handleEvent({
    type: "confirm.request",
    confirm_kind: "work_record_post_turn",
    session_id: "companion-home",
    request_id: "post-turn-c",
    preview: "确认工作结果记录",
    allow_approve_all: false,
  });
  const panel = buildCurrentWorkOverviewPanel(emptyOverview(), {
    open: true,
    liveSession: {
      confirmPending: chat.model.confirmPending,
      confirmOverlay: chat.model.confirmOverlay,
      turnActive: chat.model.turnActive,
      executionState: chat.model.executionState,
      blocks: chat.model.blocks,
    },
  });
  assert.match(panel.label, /等待确认/);
  assert.match(panel.bodyHtml, /主会话授权/);
  assert.match(panel.bodyHtml, /授权在左侧对话输入区处理/);
  assert.equal(
    composerShouldShowOrdinaryConfirmActions(chat.model, {
      chatRendersOrdinaryConfirm: false,
      overlayRendersOrdinaryConfirm: false,
      companionComposerPrimary: true,
    }),
    true,
  );
});
