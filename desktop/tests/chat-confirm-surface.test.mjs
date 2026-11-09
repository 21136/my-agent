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

const {
  canSubmitOrdinaryConfirm,
  chatSessionAwaitingUserConfirm,
  createChatSession,
  primaryPendingConfirmPreview,
  resolveActiveOrdinaryConfirmRequestId,
  syncOrdinaryConfirmPending,
} = load(new URL("../src/shells/chat-state.ts", import.meta.url));

function session() {
  const chat = createChatSession({ showProcess: true, confirmInBlocks: true });
  chat.handleEvent({
    type: "session.banner", session_id: "session-a", topics: [], turn_mode: "agent",
    turn_mode_label: "动手", phase: "S4",
  });
  chat.handleEvent({ type: "execution.state", run_id: "run-a", state: "running", sequence: 1 });
  chat.handleEvent({ type: "turn.start", run_id: "run-a", intent: "execute", intent_label: "后台工作" });
  return chat;
}

test("worker verification confirm counts as awaiting user even without confirmPending", () => {
  const chat = session();
  chat.handleEvent({
    type: "confirm.request",
    confirm_subtype: "project_worker_requirement_verification",
    session_id: "session-a",
    run_id: "run-a",
    request_id: "verify-a",
    preview: "backend preview",
    allow_approve_all: false,
    project_worker_verification: {
      session_id: "session-a",
      project_id: "music",
      item_id: "item-a",
      requirement_id: "req-a",
      project_name: "music",
      requirement_text: "run pytest",
      pytest_target: "tests/test_x.py",
      worker_run_id: "worker-a",
      reservation_id: "res-a",
    },
  });
  assert.equal(chat.model.confirmPending, false);
  assert.equal(chatSessionAwaitingUserConfirm(chat.model), true);
  assert.match(primaryPendingConfirmPreview(chat.model), /backend preview/);
});

test("batch authorization overlay counts as awaiting user", () => {
  const chat = session();
  chat.handleEvent({
    type: "confirm.request",
    session_id: "session-a",
    request_id: "batch-a",
    preview: 'PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n{"schema_version":1}',
    allow_approve_all: false,
  });
  assert.equal(chatSessionAwaitingUserConfirm(chat.model), true);
  assert.equal(chat.model.confirmPending, true);
});

test("trash soft-delete confirm submits when overlay was cleared but block remains", () => {
  const chat = session();
  const preview = "软删除到 _trash workspace 项目\nids=alpha, beta, gamma";
  chat.handleEvent({
    type: "confirm.request",
    session_id: "session-a",
    run_id: "run-a",
    request_id: "trash-batch",
    preview,
    allow_approve_all: false,
  });
  assert.equal(resolveActiveOrdinaryConfirmRequestId(chat.model), "trash-batch");
  assert.equal(primaryPendingConfirmPreview(chat.model), preview);
  chat.model.confirmOverlay = null;
  chat.model.confirmPending = false;
  assert.equal(chatSessionAwaitingUserConfirm(chat.model), true);
  assert.equal(resolveActiveOrdinaryConfirmRequestId(chat.model), "trash-batch");
  assert.equal(chat.submitConfirm("trash-batch", "y"), true);
  assert.equal(chat.model.confirmOverlay?.requestId, "trash-batch");
  assert.equal(chat.model.confirmOverlay?.resolved, "提交中…");
});

test("in-flight submit disables another ordinary choice until terminal resolution", () => {
  const chat = session();
  chat.handleEvent({
    type: "confirm.request",
    session_id: "session-a",
    run_id: "run-a",
    request_id: "trash-flight",
    preview: "软删除到 _trash workspace 项目",
    allow_approve_all: false,
  });
  assert.equal(chat.submitConfirm("trash-flight", "y"), true);
  assert.equal(canSubmitOrdinaryConfirm(chat.model, "trash-flight"), false);
  assert.equal(chat.submitConfirm("trash-flight", "y"), false);
  assert.equal(chatSessionAwaitingUserConfirm(chat.model), true);
});

test("deferred session history preserves the live ordinary confirm after turn end", () => {
  const chat = session();
  chat.handleEvent({
    type: "confirm.request",
    session_id: "session-a",
    run_id: "run-a",
    request_id: "history-trash",
    preview: "软删除到 _trash workspace 项目",
    allow_approve_all: false,
  });
  chat.handleEvent({
    type: "session.history",
    session_id: "session-a",
    items: [{ role: "user", text: "历史消息" }, { role: "assistant", text: "历史回复" }],
  });
  assert.equal(chat.model.blocks.some((block) => block.kind === "user" && block.text === "历史消息"), false);
  chat.handleEvent({ type: "turn.end", run_id: "run-a", ok: true, finish_reason: "completed" });
  assert.equal(resolveActiveOrdinaryConfirmRequestId(chat.model), "history-trash");
  assert.equal(chat.model.confirmOverlay?.requestId, "history-trash");
  assert.equal(chat.model.blocks.some((block) => block.kind === "user" && block.text === "历史消息"), true);
  assert.equal(canSubmitOrdinaryConfirm(chat.model, "history-trash"), true);
  assert.equal(chat.submitConfirm("history-trash", "y"), true);
});

test("turn end keeps confirmPending while an unresolved ordinary confirm remains", () => {
  const chat = session();
  chat.handleEvent({
    type: "confirm.request",
    session_id: "session-a",
    run_id: "run-a",
    request_id: "turn-end-trash",
    preview: "软删除到 _trash workspace 项目",
    allow_approve_all: false,
  });
  chat.handleEvent({ type: "turn.end", run_id: "run-a", ok: true, finish_reason: "completed" });
  assert.equal(chat.model.confirmPending, true);
  assert.equal(canSubmitOrdinaryConfirm(chat.model, "turn-end-trash"), true);
  assert.equal(chat.submitConfirm("turn-end-trash", "y"), true);
});

test("stale expired confirm block is not actionable while a newer confirm is pending", () => {
  const chat = session();
  chat.handleEvent({
    type: "confirm.request",
    session_id: "session-a",
    run_id: "run-a",
    request_id: "older-trash",
    preview: "软删除旧批次",
    allow_approve_all: false,
  });
  chat.handleEvent({
    type: "confirm.request",
    session_id: "session-a",
    run_id: "run-a",
    request_id: "current-trash",
    preview: "软删除到 _trash workspace 项目",
    allow_approve_all: false,
  });
  assert.equal(resolveActiveOrdinaryConfirmRequestId(chat.model), "current-trash");
  assert.equal(chat.submitConfirm("older-trash", "y"), false);
  assert.equal(chat.submitConfirm("current-trash", "y"), true);
});
