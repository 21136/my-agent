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

test("independent control confirmation after a settled turn leaves composer idle", () => {
  const chat = session();
  chat.handleEvent({ type: "execution.state", run_id: "previous-run", state: "settled", sequence: 3 });
  chat.handleEvent({ type: "turn.end", run_id: "previous-run", ok: true, finish_reason: "completed" });
  assert.equal(chat.model.turnActive, false);
  assert.equal(chat.model.turnFinished, true);

  assert.equal(chat.handleEvent({
    type: "confirm.request", request_id: "copy-item-control", preview: "确认复制事项",
    allow_approve_all: false,
  }), true);
  assert.equal(chat.submitConfirm("copy-item-control", "y"), true);
  assert.equal(chat.model.turnActive, false);
  assert.equal(chat.model.confirmSubmitting, false);
  assert.equal(chat.isWorking(), false);
  assert.equal(chat.handleEvent({
    type: "confirm.done", request_id: "copy-item-control", choice: "y",
  }), true);
  assert.equal(chat.model.turnActive, false);
  assert.equal(chat.isWorking(), false);
});

test("late independent control completion cannot cross a session switch", () => {
  const chat = session();
  chat.handleEvent({ type: "execution.state", run_id: "previous-run", state: "settled", sequence: 3 });
  chat.handleEvent({ type: "turn.end", run_id: "previous-run", ok: true, finish_reason: "completed" });
  assert.equal(chat.handleEvent({
    type: "confirm.request", session_id: "session-a", request_id: "late-control",
    preview: "确认复制事项", allow_approve_all: false,
  }), true);

  chat.handleEvent({
    type: "session.banner", session_id: "session-b", topics: [], turn_mode: "agent",
    turn_mode_label: "动手", phase: "S4",
  });
  assert.equal(chat.model.confirmOverlay, null);
  assert.equal(chat.handleEvent({
    type: "confirm.done", session_id: "session-a", request_id: "late-control", choice: "y",
  }), false);
  assert.equal(chat.model.confirmPending, false);
  assert.equal(chat.model.turnActive, false);
});

test("independent confirmation overlapping a new execution does not settle that execution", () => {
  const chat = session();
  chat.handleEvent({ type: "execution.state", run_id: "previous-run", state: "settled", sequence: 3 });
  chat.handleEvent({ type: "turn.end", run_id: "previous-run", ok: true, finish_reason: "completed" });
  assert.equal(chat.handleEvent({
    type: "confirm.request", session_id: "session-a", request_id: "copy-control",
    preview: "确认复制事项", allow_approve_all: false,
  }), true);

  chat.handleEvent({ type: "execution.state", run_id: "new-run", state: "running", sequence: 4 });
  assert.equal(chat.model.turnActive, true);
  assert.equal(chat.submitConfirm("copy-control", "y"), true);
  assert.equal(chat.model.turnActive, true);
  assert.equal(chat.model.confirmSubmitting, false);
  assert.equal(chat.handleEvent({
    type: "confirm.done", session_id: "session-a", request_id: "copy-control", choice: "y",
  }), true);
  assert.equal(chat.model.turnActive, true);
  assert.equal(chat.model.executionRunId, "new-run");
  assert.equal(chat.model.executionState, "running");
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
test("same-session ordinary confirmation with a different run id is rejected as stale", () => {
  const chat = session();
  chat.handleEvent({ type: "execution.state", run_id: "visible-run", state: "running", sequence: 2 });
  const request = {
    type: "confirm.request", request_id: "independent-record-confirm", session_id: "session-a",
    run_id: "confirmation-owner-run", preview: "CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM\n创建当前会话独立工作记录",
    allow_approve_all: false,
  };
  assert.equal(chat.handleEvent(request), false);
  assert.equal(chat.model.confirmOverlay, null);
  assert.equal(chat.model.confirmPending, false);
});

test("a confirmation bound to the visible session and run is actionable, but an old request is not", () => {
  const chat = session();
  chat.handleEvent({ type: "execution.state", run_id: "visible-run", state: "running", sequence: 2 });
  const current = {
    type: "confirm.request", request_id: "current-bound-confirm", session_id: "session-a",
    run_id: "visible-run", preview: "当前运行确认", allow_approve_all: false,
  };
  assert.equal(chat.handleEvent(current), true);
  assert.equal(chat.model.confirmOverlay?.requestId, current.request_id);
  assert.equal(chat.submitConfirm(current.request_id, "y"), true);
  assert.equal(chat.model.turnActive, true, "execution approval still activates the in-flight turn");
  assert.equal(chat.handleEvent({
    type: "confirm.done", request_id: current.request_id, session_id: "session-a",
    run_id: "visible-run", choice: "y",
  }), true);
  assert.equal(chat.model.turnActive, true, "execution completion keeps the in-flight turn active");
  assert.equal(chat.handleEvent({
    ...current, request_id: "old-run-confirm", run_id: "old-run", preview: "过期确认",
  }), false);
  assert.equal(chat.submitConfirm("old-run-confirm", "y"), false);
  assert.equal(chat.model.confirmOverlay?.requestId, current.request_id);
});

test("same-session confirmation with a different session is still rejected", () => {
  const chat = session();
  chat.handleEvent({ type: "execution.state", run_id: "visible-run", state: "running", sequence: 2 });
  assert.equal(chat.handleEvent({
    type: "confirm.request", request_id: "other-session-confirm", session_id: "session-b",
    run_id: "visible-run", preview: "not for this session", allow_approve_all: false,
  }), false);
  assert.equal(chat.model.confirmPending, false);
});


test("completion for a previous confirmation does not clear the current confirmation", () => {
  const chat = createChatSession({ showProcess: true, confirmInBlocks: true });
  chat.handleEvent({
    type: "session.banner", session_id: "session-a", topics: [], turn_mode: "agent",
    turn_mode_label: "动手", phase: "S4",
  });
  chat.handleEvent({ type: "execution.state", run_id: "current-run", state: "running", sequence: 2 });
  assert.equal(chat.handleEvent({
    type: "confirm.request", request_id: "older-confirm", session_id: "session-a",
    run_id: "current-run", preview: "旧确认", allow_approve_all: false,
  }), true);
  assert.equal(chat.handleEvent({
    type: "confirm.request", request_id: "current-confirm", session_id: "session-a",
    run_id: "current-run", preview: "当前确认", allow_approve_all: false,
  }), true);
  assert.equal(chat.model.confirmOverlay?.requestId, "current-confirm");
  assert.equal(chat.model.confirmPending, true);

  assert.equal(chat.handleEvent({
    type: "confirm.done", request_id: "older-confirm", session_id: "session-a",
    run_id: "current-run", choice: "timeout",
  }), true);
  assert.equal(chat.model.confirmOverlay?.requestId, "current-confirm");
  assert.equal(chat.model.confirmPending, true);
  assert.equal(chat.model.blocks.find(block => block.kind === "confirm" && block.requestId === "current-confirm")?.resolved, undefined);
});
test("same-session confirmation events from a non-current run are rejected", () => {
  const chat = createChatSession({ showProcess: true, confirmInBlocks: true });
  chat.handleEvent({
    type: "session.banner", session_id: "session-a", topics: [], turn_mode: "agent",
    turn_mode_label: "动手", phase: "S4",
  });
  chat.handleEvent({ type: "execution.state", run_id: "current-run", state: "running", sequence: 2 });
  assert.equal(chat.handleEvent({
    type: "confirm.done", request_id: "already-finished", session_id: "session-a",
    run_id: "older-run", choice: "timeout",
  }), false);
  assert.equal(chat.handleEvent({
    type: "confirm.request", request_id: "already-finished", session_id: "session-a",
    run_id: "older-run", preview: "late replay", allow_approve_all: false,
  }), false);
  assert.equal(chat.model.confirmPending, false);
  assert.equal(chat.model.confirmOverlay, null);
  assert.equal(chat.model.blocks.some(block => block.kind === "confirm" && block.requestId === "already-finished"), false);
});

test("same-session banner refresh preserves an in-flight ordinary confirmation", () => {
  const chat = session();
  chat.handleEvent({
    type: "confirm.request", request_id: "tool-confirm", preview: "执行命令", allow_approve_all: false,
  });
  assert.equal(chat.model.confirmPending, true);
  chat.handleEvent({
    type: "session.banner", session_id: "session-a", topics: [], turn_mode: "agent",
    turn_mode_label: "动手", phase: "S4",
  });
  assert.equal(chat.model.confirmPending, true);
  assert.equal(chat.model.confirmOverlay?.requestId, "tool-confirm");
});

test("confirm.done for a pending request is not dropped as a stale run event", () => {
  const chat = session();
  chat.handleEvent({ type: "execution.state", run_id: "visible-run", state: "running", sequence: 2 });
  chat.handleEvent({
    type: "confirm.request", request_id: "bind-confirm", session_id: "session-a",
    run_id: "visible-run", preview: "运行命令", allow_approve_all: false,
  });
  chat.handleEvent({ type: "execution.state", run_id: "next-run", state: "running", sequence: 3 });
  assert.equal(chat.handleEvent({
    type: "confirm.done", request_id: "bind-confirm", session_id: "session-a",
    run_id: "visible-run", choice: "timeout",
  }), true);
  assert.equal(chat.model.confirmPending, false);
  assert.equal(chat.model.confirmOverlay?.resolved, "确认超时");
});
