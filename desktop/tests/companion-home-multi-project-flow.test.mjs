/**
 * Companion home multi-project flow: WS confirm sequence + 当前工作 queue mirror (#40–#43).
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

const {
  createChatSession,
  chatSessionAwaitingUserConfirm,
  resolveActiveOrdinaryConfirmRequestId,
  primaryPendingConfirmPreview,
} = chatState;
const { buildCurrentWorkOverviewPanel } = overview;

const emptyCounts = { visible_groups: 0, visible_items: 0, collapsed_groups: 0, collapsed_items: 0 };
const basePage = { page_size: 6, offset: 0, total_groups: 0, total_visible_groups: 0, total_visible_items: 0,
  total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null };
const emptyOverview = (overrides = {}) => ({
  type: "work-records.overview.result",
  session_id: "companion-home",
  request_id: "11111111-1111-4111-8111-111111111111",
  state: "no_records",
  groups: [],
  unread_important_results: [],
  unread_status: "known",
  visibility_limitations: [],
  visibility_counts: emptyCounts,
  snapshot_id: null,
  page: basePage,
  ...overrides,
});

function companionSession() {
  const chat = createChatSession({ showProcess: true, confirmInBlocks: true });
  chat.handleEvent({
    type: "session.banner",
    session_id: "companion-home",
    topics: [],
    turn_mode: "agent",
    turn_mode_label: "动手",
    phase: "S4",
  });
  chat.handleEvent({ type: "execution.state", run_id: "run-companion", state: "running", sequence: 1 });
  chat.handleEvent({ type: "turn.start", run_id: "run-companion", intent: "execute", intent_label: "后台工作" });
  return chat;
}

test("companion flow: trash confirm → agree succeeds without stale-card rejection", () => {
  const chat = companionSession();
  const trashPreview = "软删除到 _trash workspace 项目\nids=random-number-a, random-number-b, random-number-c";
  chat.handleEvent({
    type: "confirm.request",
    session_id: "companion-home",
    run_id: "run-companion",
    request_id: "trash-three-random",
    preview: trashPreview,
    allow_approve_all: false,
  });
  assert.equal(resolveActiveOrdinaryConfirmRequestId(chat.model), "trash-three-random");
  assert.equal(chat.submitConfirm("trash-three-random", "y"), true);
  assert.equal(chat.model.confirmOverlay?.resolved, "提交中…");
  chat.handleEvent({
    type: "confirm.done",
    session_id: "companion-home",
    request_id: "trash-three-random",
    choice: "y",
  });
  assert.equal(chat.model.confirmPending, false);
});

test("companion flow: after trash, batch worker auth confirm is actionable on main chat", () => {
  const chat = companionSession();
  chat.handleEvent({
    type: "confirm.request",
    session_id: "companion-home",
    run_id: "run-companion",
    request_id: "batch-auth-three",
    preview: 'PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n{"schema_version":1,"tasks":[]}',
    allow_approve_all: false,
  });
  assert.equal(chat.model.confirmPending, true);
  assert.equal(chatSessionAwaitingUserConfirm(chat.model), true);
  assert.equal(chat.submitConfirm("batch-auth-three", "y"), true);
  assert.equal(chat.model.confirmOverlay?.requestId, "batch-auth-three");
});

test("companion flow: 当前工作 mirrors pending batch auth when overview is empty", () => {
  const chat = companionSession();
  const batchPreview = "确认向 3 个项目派发 worker：random-number-a、random-number-b、random-number-c";
  chat.handleEvent({
    type: "confirm.request",
    session_id: "companion-home",
    run_id: "run-companion",
    request_id: "batch-live",
    preview: batchPreview,
    allow_approve_all: false,
  });
  const panel = buildCurrentWorkOverviewPanel(emptyOverview(), {
    open: true,
    liveSession: {
      confirmPending: chat.model.confirmPending,
      confirmOverlay: chat.model.confirmOverlay,
      turnActive: chat.model.turnActive,
      executionState: "running",
      blocks: chat.model.blocks,
    },
  });
  assert.match(panel.label, /等待确认/);
  assert.doesNotMatch(panel.bodyHtml, /暂无可见工作组/);
  assert.match(panel.bodyHtml, /主会话授权/);
  assert.match(panel.bodyHtml, /random-number/);
});

test("companion flow: deferred history after turn end keeps trash confirm submittable", () => {
  const chat = companionSession();
  chat.handleEvent({
    type: "confirm.request",
    session_id: "companion-home",
    run_id: "run-companion",
    request_id: "trash-after-turn",
    preview: "软删除到 _trash workspace 项目",
    allow_approve_all: false,
  });
  chat.handleEvent({
    type: "session.history",
    session_id: "companion-home",
    items: [{ role: "user", text: "历史轮次" }],
  });
  chat.handleEvent({ type: "turn.end", run_id: "run-companion", ok: true, finish_reason: "completed" });
  assert.equal(resolveActiveOrdinaryConfirmRequestId(chat.model), "trash-after-turn");
  assert.equal(primaryPendingConfirmPreview(chat.model), "软删除到 _trash workspace 项目");
  assert.equal(chat.submitConfirm("trash-after-turn", "y"), true);
});

test("companion flow: stale trash card rejected while newer batch auth remains active", () => {
  const chat = companionSession();
  chat.handleEvent({
    type: "confirm.request",
    session_id: "companion-home",
    run_id: "run-companion",
    request_id: "old-trash",
    preview: "软删除旧批次",
    allow_approve_all: false,
  });
  chat.handleEvent({
    type: "confirm.request",
    session_id: "companion-home",
    run_id: "run-companion",
    request_id: "new-batch-auth",
    preview: 'PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n{}',
    allow_approve_all: false,
  });
  assert.equal(chat.submitConfirm("old-trash", "y"), false);
  assert.equal(chat.submitConfirm("new-batch-auth", "y"), true);
});
