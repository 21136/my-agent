import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ts = require("typescript");
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
const sessionId = "session-a";
const runId = "run-a";
const requestId = "12345678-1234-4234-8234-123456789abc";

function setup() {
  const chat = createChatSession({ showProcess: true, confirmInBlocks: true });
  chat.handleEvent({ type: "session.banner", session_id: sessionId, topics: [], turn_mode: "agent", turn_mode_label: "动手", phase: "S4" });
  chat.handleEvent({ type: "execution.state", run_id: runId, state: "running", sequence: 1 });
  chat.handleEvent({ type: "turn.start", run_id: runId, intent: "execute", intent_label: "后台工作" });
  return chat;
}

test("companion orchestration bypasses work-instruction queue label path", () => {
  const chat = setup();
  assert.equal(chat.addWorkInstructionRequest("同时写两个项目", requestId, sessionId, runId), true);
  const block = chat.model.blocks.find((item) => item.kind === "work-instruction");
  assert.equal(block.state, "sending");
  assert.equal(
    chat.handleEvent({
      type: "companion.orchestration.state",
      request_id: requestId,
      run_id: runId,
      session_id: sessionId,
      state: "dispatching",
    }),
    true,
  );
  assert.equal(block.state, "orchestration_dispatching");
  assert.equal(
    chat.handleEvent({
      type: "companion.orchestration.state",
      request_id: requestId,
      run_id: runId,
      session_id: sessionId,
      state: "active",
    }),
    true,
  );
  assert.equal(block.state, "orchestration_active");
  assert.equal(
    chat.handleEvent({
      type: "work.instruction.state",
      request_id: requestId,
      run_id: runId,
      session_id: sessionId,
      state: "delivered",
      delivery_stage: "next_model_request_context",
    }),
    true,
  );
  assert.equal(block.state, "orchestration_active");
  assert.equal(
    chat.model.blocks.some((item) => item.kind === "work-instruction" && item.state === "queued"),
    false,
  );
});
