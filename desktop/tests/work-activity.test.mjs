import assert from "node:assert/strict";
import test from "node:test";

const modulePromise = import("../src/shells/unified/work-activity.ts").catch(() => undefined);

async function project(blocks) {
  const module = await modulePromise;
  assert.ok(module, "work activity projection module should load");
  assert.equal(typeof module.projectRecentWorkActivity, "function");
  return module.projectRecentWorkActivity(blocks);
}

test("projects only real tool actions and exact tool paths, newest first", async () => {
  const rows = await project([
    { kind: "process", turnKey: "turn-1", reasoning: "private reasoning", entries: [
      { kind: "tool", callId: "a", tool: "file_read", summary: "Read config", status: "ok", startedAt: 10, path: "desktop/config.json" },
      { kind: "think", id: "private", text: "do not show", phase: "pinned", startedAt: 15 },
      { kind: "tool", callId: "b", tool: "file_write", summary: "Updated UI", status: "running", startedAt: 20 },
    ] },
  ]);
  assert.deepEqual(rows.map(row => row.callId), ["b", "a"]);
  assert.equal(rows[1].path, "desktop/config.json");
  assert.equal(rows[0].path, "");
  assert.ok(!JSON.stringify(rows).includes("private reasoning"));
});

test("returns an explicit empty projection and caps the recent list", async () => {
  assert.deepEqual(await project([]), []);
  const rows = await project([{ kind: "process", entries: Array.from({ length: 8 }, (_, i) => ({
    kind: "tool", callId: String(i), tool: "read", summary: `Read ${i}`, status: "ok", startedAt: i,
  })) }]);
  assert.equal(rows.length, 5);
  assert.deepEqual(rows.map(row => row.callId), ["7", "6", "5", "4", "3"]);
});


