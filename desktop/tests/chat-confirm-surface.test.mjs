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

const { chatSessionAwaitingUserConfirm, createChatSession, primaryPendingConfirmPreview } = load(
  new URL("../src/shells/chat-state.ts", import.meta.url),
);

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
