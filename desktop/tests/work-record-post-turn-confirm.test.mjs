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
  new Function("exports", "require", js)(exports, (id) => {
    assert.ok(id.startsWith("."));
    return load(new URL(id.endsWith(".ts") ? id : `${id}.ts`, url));
  });
  return exports;
}
const { createChatSession } = load(new URL("../src/shells/chat-state.ts", import.meta.url));

function session() {
  const chat = createChatSession({ showProcess: true, confirmInBlocks: false });
  chat.handleEvent({
    type: "session.banner", session_id: "session-a", topics: [], turn_mode: "agent",
    turn_mode_label: "动手", phase: "S4",
  });
  return chat;
}

function postTurnRequest(sessionId = "session-a", requestId = "post-turn-1") {
  return {
    type: "confirm.request", confirm_kind: "work_record_post_turn", session_id: sessionId,
    request_id: requestId, preview: "确认写入本次会话工作记录", allow_approve_all: false,
  };
}

test("post-turn confirmation uses an existing chat block without activating a turn", () => {
  const chat = session();
  chat.handleEvent(postTurnRequest());
  const block = chat.model.blocks.find((item) => item.kind === "confirm");
  assert.ok(block);
  assert.equal(block.requestId, "post-turn-1");
  assert.equal(chat.model.confirmOverlay, null);
  assert.equal(chat.model.confirmPending, false);
  assert.equal(chat.model.turnActive, false);
  assert.equal(chat.model.turnActivityStartedAt, 0);
});

test("post-turn response and matching completion do not activate or otherwise alter a turn", () => {
  const chat = session();
  chat.handleEvent(postTurnRequest());
  const before = {
    turnActive: chat.model.turnActive,
    turnFinished: chat.model.turnFinished,
    startedAt: chat.model.turnActivityStartedAt,
    turnCounter: chat.model.turnCounter,
    executionRunId: chat.model.executionRunId,
  };
  assert.equal(chat.submitConfirm("post-turn-1", "y"), true);
  assert.deepEqual({
    turnActive: chat.model.turnActive,
    turnFinished: chat.model.turnFinished,
    startedAt: chat.model.turnActivityStartedAt,
    turnCounter: chat.model.turnCounter,
    executionRunId: chat.model.executionRunId,
  }, before);
  assert.equal(chat.handleEvent({
    type: "confirm.done", confirm_kind: "work_record_post_turn", session_id: "session-a",
    request_id: "post-turn-1", choice: "y",
  }), true);
  assert.deepEqual({
    turnActive: chat.model.turnActive,
    turnFinished: chat.model.turnFinished,
    startedAt: chat.model.turnActivityStartedAt,
    turnCounter: chat.model.turnCounter,
    executionRunId: chat.model.executionRunId,
  }, before);
  assert.equal(chat.model.blocks.find((item) => item.kind === "confirm")?.resolved, "已同意，执行中…");
});

test("ordinary turn confirmations do not expire a pending post-turn block", () => {
  const chat = session();
  chat.handleEvent(postTurnRequest());
  chat.handleEvent({
    type: "confirm.request", request_id: "ordinary-2", preview: "普通确认", allow_approve_all: false,
  });
  assert.equal(chat.model.blocks.find((item) => item.kind === "confirm" && item.requestId === "post-turn-1")?.resolved, undefined);
  assert.equal(chat.model.confirmOverlay?.requestId, "ordinary-2");
});

test("unbound, mismatched, stale, and duplicate post-turn events are ignored", () => {
  const chat = session();
  assert.equal(chat.handleEvent(postTurnRequest("session-b", "wrong-session")), false);
  assert.equal(chat.handleEvent({ ...postTurnRequest(), session_id: undefined }), false);
  assert.equal(chat.handleEvent({
    type: "confirm.done", confirm_kind: "work_record_post_turn", session_id: "session-a",
    request_id: "unbound", choice: "y",
  }), false);
  chat.handleEvent(postTurnRequest());
  assert.equal(chat.handleEvent({
    type: "confirm.done", confirm_kind: "work_record_post_turn", session_id: "session-b",
    request_id: "post-turn-1", choice: "n",
  }), false);
  assert.equal(chat.handleEvent({
    type: "confirm.done", session_id: "session-a", request_id: "post-turn-1", choice: "n",
  }), false, "a special confirmation cannot complete without its protocol tag");
  const correct = {
    type: "confirm.done", confirm_kind: "work_record_post_turn", session_id: "session-a",
    request_id: "post-turn-1", choice: "n",
  };
  assert.equal(chat.handleEvent(correct), true);
  assert.equal(chat.handleEvent(correct), false);
  assert.equal(chat.model.blocks.find((item) => item.kind === "confirm")?.resolved, "已跳过");
});

test("session change clears post-turn bindings and stale completion cannot resolve a new block", () => {
  const chat = session();
  chat.handleEvent(postTurnRequest());
  chat.handleEvent({
    type: "session.banner", session_id: "session-b", topics: [], turn_mode: "agent",
    turn_mode_label: "动手", phase: "S4",
  });
  assert.equal(chat.model.blocks.some((item) => item.kind === "confirm"), false);
  assert.equal(chat.handleEvent({
    type: "confirm.done", confirm_kind: "work_record_post_turn", session_id: "session-a",
    request_id: "post-turn-1", choice: "y",
  }), false);
  assert.equal(chat.model.turnActive, false);
});

test("ordinary confirm requests retain existing overlay, response, and done behavior", () => {
  const chat = session();
  chat.handleEvent({
    type: "confirm.request", request_id: "ordinary-1", preview: "普通确认", allow_approve_all: false,
  });
  assert.equal(chat.model.confirmPending, true);
  assert.equal(chat.model.confirmOverlay?.requestId, "ordinary-1");
  assert.equal(chat.model.blocks.some((item) => item.kind === "confirm"), false);
  assert.equal(chat.submitConfirm("ordinary-1", "y"), true);
  assert.equal(chat.model.turnActive, true);
  chat.handleEvent({ type: "confirm.done", request_id: "ordinary-1", choice: "y" });
  assert.equal(chat.model.confirmPending, false);
});
