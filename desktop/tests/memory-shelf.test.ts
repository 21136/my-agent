import assert from "node:assert/strict";
import test from "node:test";

import { memoryShelfLabel } from "../src/shells/unified/memory-shelf-model.ts";

test("memoryShelfLabel empty", () => {
  assert.equal(memoryShelfLabel({ memories: [], pending: [] }), "记忆");
});

test("memoryShelfLabel with accepted only", () => {
  assert.equal(
    memoryShelfLabel({
      memories: [
        { memory_id: "a", topics: ["coding"], summary: "likes dark mode", path: "memories/a.md" },
        { memory_id: "b", topics: [], summary: "night owl", path: "memories/b.md" },
      ],
      pending: [],
    }),
    "记忆 2",
  );
});

test("memoryShelfLabel with pending", () => {
  assert.equal(
    memoryShelfLabel({
      memories: [{ memory_id: "a", topics: [], summary: "x", path: "m.md" }],
      pending: [{ proposal_id: "p1", type: "memory", mode: "create", summary: "y", topics: [] }],
    }),
    "记忆 1 · 待审 1",
  );
});
