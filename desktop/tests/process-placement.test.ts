import assert from "node:assert/strict";
import test from "node:test";

import { isProcessBlockEmpty, orderTurnBlocksForDisplay } from "../src/shells/process-placement.ts";

type B = { kind: string; id: string };
const b = (kind: string, id: string): B => ({ kind, id });
const ids = (blocks: B[]) => orderTurnBlocksForDisplay(blocks).map((block) => block.id);

function proc(overrides: Record<string, unknown> = {}) {
  return {
    kind: "process" as const,
    lines: [],
    reasoning: "",
    collapsed: true,
    turnKey: "turn-1",
    tools: [],
    entries: [],
    ...overrides,
  };
}

test("finished turn: 过程 pill renders after the reply", () => {
  assert.deepEqual(ids([b("user", "u"), b("process", "p"), b("assistant", "a")]), ["u", "a", "p"]);
});

test("placement does not depend on collapsed state (toggle cannot move the pill)", () => {
  const collapsed = [b("user", "u"), { ...b("process", "p"), collapsed: true }, b("assistant", "a")];
  const expanded = [b("user", "u"), { ...b("process", "p"), collapsed: false }, b("assistant", "a")];
  assert.deepEqual(ids(collapsed), ids(expanded));
});

test("live (streaming) and finished (assistant.done) turns share the same order", () => {
  const live = ids([b("user", "u"), b("process", "p"), b("assistant-streaming", "a")]);
  const done = ids([b("user", "u"), b("process", "p"), b("assistant", "a")]);
  assert.deepEqual(live, ["u", "a", "p"]);
  assert.deepEqual(done, live);
});

test("restored history order (process re-appended after the reply) gives the same layout", () => {
  assert.deepEqual(ids([b("user", "u"), b("assistant", "a"), b("process", "p")]), ["u", "a", "p"]);
});

test("tools still running / confirm pending / error: process keeps its place", () => {
  assert.deepEqual(ids([b("user", "u"), b("process", "p")]), ["u", "p"]);
  assert.deepEqual(ids([b("user", "u"), b("process", "p"), b("confirm", "c")]), ["u", "p", "c"]);
  assert.deepEqual(ids([b("user", "u"), b("process", "p"), b("notice", "err")]), ["u", "p", "err"]);
});

test("multi-segment turns pair each 过程 with the reply it produced", () => {
  assert.deepEqual(
    ids([b("user", "u"), b("process", "p1"), b("assistant", "a1"), b("process", "p2"), b("assistant", "a2")]),
    ["u", "a1", "p1", "a2", "p2"],
  );
  assert.deepEqual(
    ids([b("user", "u"), b("process", "p"), b("assistant", "a"), b("notice", "chip")]),
    ["u", "a", "p", "chip"],
  );
});

test("empty process blocks are detected (no empty pill)", () => {
  assert.equal(isProcessBlockEmpty(proc()), true);
  assert.equal(isProcessBlockEmpty(proc({ entries: [{ kind: "think", id: "t", text: "  ", phase: "pinned", startedAt: 1 }] })), true);
  assert.equal(isProcessBlockEmpty(proc({ llmPending: true })), false);
  assert.equal(isProcessBlockEmpty(proc({ entries: [{ kind: "think", id: "t", text: "", phase: "streaming", startedAt: 1 }] })), false);
  assert.equal(isProcessBlockEmpty(proc({ entries: [{ kind: "think", id: "t", text: "想一想", phase: "pinned", startedAt: 1 }] })), false);
  assert.equal(
    isProcessBlockEmpty(proc({ entries: [{ kind: "tool", callId: "c", tool: "read_file", summary: "", status: "ok", startedAt: 1 }] })),
    false,
  );
});
