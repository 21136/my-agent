import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const ts = createRequire(import.meta.url)("typescript");
const tsCache = new Map();
function loadTs(relativeOrUrl) {
  const url = relativeOrUrl instanceof URL ? relativeOrUrl : new URL(relativeOrUrl, import.meta.url);
  if (tsCache.has(url.href)) return tsCache.get(url.href);
  const exports = {};
  tsCache.set(url.href, exports);
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const localRequire = specifier => specifier.startsWith(".")
    ? loadTs(new URL(specifier.endsWith(".ts") ? specifier : `${specifier}.ts`, url))
    : createRequire(import.meta.url)(specifier);
  new Function("exports", "require", js)(exports, localRequire);
  return exports;
}
const {
  collectCurrentWorkLiveQueueEntries,
  currentWorkLiveConfirmPending,
  renderCurrentWorkLiveQueueHtml,
} = loadTs("../src/shells/unified/current-work-live-session.ts");

const idle = {
  confirmPending: false,
  confirmOverlay: null,
  turnActive: false,
  executionState: "idle",
  blocks: [],
};

test("mirrors ordinary pending confirms and removes duplicate/resolved requests", () => {
  const input = {
    ...idle,
    confirmOverlay: { requestId: "ordinary-1", preview: "普通工具授权", allowApproveAll: false },
    blocks: [
      { kind: "confirm", requestId: "ordinary-1", preview: "重复", allowApproveAll: false },
      { kind: "confirm", requestId: "ordinary-done", preview: "已处理", allowApproveAll: false, resolved: "已拒绝" },
    ],
  };
  assert.equal(currentWorkLiveConfirmPending(input), true);
  const [entry] = collectCurrentWorkLiveQueueEntries(input);
  assert.equal(entry.status, "等待确认");
  assert.match(entry.detailHtml, /普通工具授权/);
  assert.doesNotMatch(entry.detailHtml, /重复|已处理/);
});

test("shows live orchestration instructions only while dynamically active", () => {
  const input = {
    ...idle,
    turnActive: true,
    executionState: "running",
    blocks: [
      { kind: "work-instruction", requestId: "wi-1", sessionId: "s", workRunId: "r", text: "推进 <alpha>", state: "orchestration_active" },
      { kind: "work-instruction", requestId: "wi-2", sessionId: "s", workRunId: "r", text: "已送达", state: "delivered" },
    ],
  };
  const entries = collectCurrentWorkLiveQueueEntries(input);
  assert.equal(entries.length, 1);
  assert.match(entries[0].detailHtml, /推进 &lt;alpha&gt;/);
  assert.doesNotMatch(entries[0].detailHtml, /已送达/);
});

test("falls back to the active execution row when no live queue blocks exist", () => {
  const input = { ...idle, turnActive: true, executionState: "queued" };
  assert.equal(collectCurrentWorkLiveQueueEntries(input)[0].status, "状态待确认");
  assert.match(renderCurrentWorkLiveQueueHtml(input), /data-cw-live-entry="live-execution"/);
});
