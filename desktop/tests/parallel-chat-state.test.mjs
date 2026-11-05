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
const { createChatSession } = load(new URL("../src/shells/chat-state.ts", import.meta.url));
const sessionId = "session-1";
const runId = "run-work-1";
const requestId = "12345678-1234-4234-8234-123456789abc";
function setup() {
  const chat = createChatSession({ showProcess: true, confirmInBlocks: true });
  chat.handleEvent({ type: "session.banner", session_id: sessionId, topics: [], turn_mode: "agent", turn_mode_label: "动手", phase: "S4" });
  chat.handleEvent({ type: "execution.state", run_id: runId, state: "running", sequence: 1 });
  chat.handleEvent({ type: "turn.start", run_id: runId, intent: "execute", intent_label: "后台工作" });
  return chat;
}

 test("parallel chat is accepted only by matching chat.start and does not own work turn", () => {
  const chat = setup();
  const turnCounter = chat.model.turnCounter;
  assert.equal(chat.addParallelChatRequest("顺便问个问题", requestId, runId), true);
  const block = chat.model.blocks.find((item) => item.kind === "parallel-chat");
  assert.equal(block.deliveryState, "pending");
  assert.equal(chat.model.turnCounter, turnCounter);

  chat.handleEvent({ type: "confirm.request", request_id: "confirm-1", preview: "确认工作操作", allow_approve_all: false, run_id: runId });
  const workStateBefore = { runId: chat.model.executionRunId, state: chat.model.executionState, sequence: chat.model.executionSequence, confirmPending: chat.model.confirmPending, turnCounter: chat.model.turnCounter };
  assert.equal(chat.handleEvent({ type: "chat.start", request_id: requestId, work_run_id: "stale-run", session_id: sessionId }), false);
  assert.equal(block.deliveryState, "pending");
  assert.equal(chat.handleEvent({ type: "chat.start", request_id: requestId, work_run_id: runId, session_id: sessionId }), true);
  assert.equal(block.deliveryState, "delivered");
  assert.deepEqual({ runId: chat.model.executionRunId, state: chat.model.executionState, sequence: chat.model.executionSequence, confirmPending: chat.model.confirmPending, turnCounter: chat.model.turnCounter }, workStateBefore);
});

test("parallel chat deltas and terminal errors update only their matching same-chat block", () => {
  const chat = setup();
  chat.addParallelChatRequest("问题一", requestId, runId);
  chat.handleEvent({ type: "chat.start", request_id: requestId, work_run_id: runId, session_id: sessionId });
  assert.equal(chat.handleEvent({ type: "chat.delta", request_id: requestId, work_run_id: "other-run", session_id: sessionId, text: "stale" }), false);
  assert.equal(chat.handleEvent({ type: "chat.delta", request_id: requestId, work_run_id: runId, session_id: sessionId, text: "你好" }), true);
  const block = chat.model.blocks.find((item) => item.kind === "parallel-chat");
  assert.equal(block.responseText, "你好");
  assert.equal(chat.handleEvent({ type: "chat.done", request_id: requestId, work_run_id: runId, session_id: sessionId, text: "你好，已经回答。", finish_reason: "stop" }), true);
  assert.equal(block.deliveryState, "done");
  assert.equal(block.responseText, "你好，已经回答。");
  assert.equal(chat.handleEvent({ type: "chat.error", request_id: "12345678-1234-4234-8234-000000000000", work_run_id: runId, session_id: sessionId, code: "chat_failed", message: "不应串到其他请求" }), false);

  const failedId = "12345678-1234-4234-8234-000000000001";
  chat.addParallelChatRequest("问题二", failedId, runId);
  assert.equal(chat.handleEvent({ type: "chat.error", request_id: failedId, work_run_id: runId, session_id: sessionId, code: "chat_failed", message: "回复生成失败" }), true);
  const failed = chat.model.blocks.find((item) => item.kind === "parallel-chat" && item.requestId === failedId);
  assert.equal(failed.deliveryState, "error");
  assert.equal(failed.errorMessage, "回复生成失败");
  assert.equal(chat.model.executionState, "running");

  const cancellableId = "12345678-1234-4234-8234-000000000002";
  chat.addParallelChatRequest("问题三", cancellableId, runId);
  chat.handleEvent({ type: "chat.start", request_id: cancellableId, work_run_id: runId, session_id: sessionId });
  assert.equal(chat.requestParallelChatCancel(cancellableId), true);
  assert.equal(chat.handleEvent({ type: "chat.cancel.result", request_id: cancellableId, accepted: false }), true);
  const cancellable = chat.model.blocks.find((item) => item.kind === "parallel-chat" && item.requestId === cancellableId);
  assert.equal(cancellable.cancelRequested, false);
  assert.equal(chat.model.executionState, "running");
});


test("backend chat classification converts the pending shared message into the same-session reply", () => {
  const chat = setup();
  assert.equal(chat.addWorkInstructionRequest("顺便问个问题", requestId, sessionId, runId), true);
  assert.equal(chat.handleEvent({ type: "chat.start", request_id: requestId, work_run_id: runId, session_id: sessionId }), true);
  const block = chat.model.blocks.find((item) => item.requestId === requestId);
  assert.equal(block.kind, "parallel-chat");
  assert.equal(block.deliveryState, "delivered");
  assert.equal(block.promptText, "顺便问个问题");
  assert.equal(chat.handleEvent({ type: "chat.done", request_id: requestId, work_run_id: runId, session_id: sessionId, text: "答复", finish_reason: "stop" }), true);
  assert.equal(block.responseText, "答复");
  assert.equal(chat.model.executionState, "running");
});


test("tool prompts correlate responses to the exact request, run, and session without consuming ordinary chat", () => {
  const chat = setup();
  const promptEvent = { type: "prompt.request", request_id: requestId, run_id: runId, session_id: sessionId, prompt: "请输入目标路径" };
  assert.equal(chat.handleEvent({ ...promptEvent, session_id: "stale-session" }), false);
  assert.equal(chat.handleEvent({ ...promptEvent, run_id: "stale-run" }), false);
  assert.equal(chat.handleEvent(promptEvent), true);
  assert.equal(chat.model.pendingToolPrompt.requestId, requestId);
  assert.equal(chat.beginToolPromptResponse(requestId, runId, sessionId, "src/main.ts"), true);
  const block = chat.model.blocks.find((item) => item.kind === "tool-prompt");
  assert.equal(block.state, "sending");
  assert.equal(chat.handleEvent({ type: "prompt.response.result", request_id: requestId, run_id: "stale-run", session_id: sessionId, accepted: true }), false);
  assert.equal(chat.handleEvent({ type: "prompt.response.result", request_id: requestId, run_id: runId, session_id: sessionId, accepted: false }), true);
  assert.equal(block.state, "rejected");
  assert.equal(chat.model.pendingToolPrompt.requestId, requestId, "rejected input remains bound for safe retry");
  assert.equal(chat.beginToolPromptResponse(requestId, runId, sessionId, "src/main.ts"), true);
  assert.equal(chat.handleEvent({ type: "prompt.response.result", request_id: requestId, run_id: runId, session_id: sessionId, accepted: true }), true);
  assert.equal(block.state, "accepted");
  assert.equal(block.responseText, "src/main.ts");
  assert.equal(chat.model.pendingToolPrompt, null);
  assert.equal(chat.model.executionState, "running");
});
