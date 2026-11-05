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

test("stop watchdog reports waiting without manufacturing a terminal state", () => {
  const originalSetTimeout = globalThis.setTimeout;
  let watchdog;
  let watchdogDelay = 0;
  globalThis.setTimeout = (callback, delay, ...args) => {
    if (delay === 45_000) {
      watchdog = () => typeof callback === "function" && callback(...args);
      watchdogDelay = delay;
      return 1;
    }
    return originalSetTimeout(callback, delay, ...args);
  };

  try {
    const chat = createChatSession({ showProcess: true, confirmInBlocks: false });
    chat.handleEvent({ type: "execution.state", run_id: "run-a", state: "running", sequence: 1 });
    assert.equal(chat.requestCancel(), true);
    assert.equal(watchdogDelay, 45_000);
    watchdog?.();

    assert.equal(chat.model.executionState, "running");
    assert.equal(chat.model.cancelRequested, true);
    assert.equal(chat.isWorking(), true);
    assert.equal(chat.model.turnFinished, false);
    assert.equal(chat.model.blocks.some((block) => block.kind === "notice" && /已恢复|已停止/.test(block.text)), false);

    chat.handleEvent({ type: "execution.state", run_id: "run-a", state: "settled", sequence: 2, finish_reason: "cancelled" });
    assert.equal(chat.model.cancelRequested, false);
    assert.equal(chat.isWorking(), false);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
  }
});

test("a rejected run-scoped stop releases only the optimistic stop request", () => {
  const chat = createChatSession({ showProcess: true, confirmInBlocks: false });
  chat.handleEvent({ type: "execution.state", run_id: "run-a", state: "running", sequence: 1 });
  assert.equal(chat.requestCancel(), true);
  assert.equal(chat.handleEvent({ type: "execution.stop.result", run_id: "run-a", accepted: false }), true);
  assert.equal(chat.model.executionState, "running");
  assert.equal(chat.model.cancelRequested, false);
  assert.equal(chat.isWorking(), true);
});
