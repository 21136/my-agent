import assert from "node:assert/strict";
import test from "node:test";

import { mergeSessionLists } from "../src/api/ws.ts";

test("mergeSessionLists dedupes identical session ids from stable and sandbox", () => {
  const stable = [
    { session_id: "a", title: "one", updated_at: "2026-10-21T10:00:00Z" },
    { session_id: "b", title: "two", updated_at: "2026-10-20T10:00:00Z" },
  ];
  const sandbox = [
    { session_id: "a", title: "one copy", updated_at: "2026-10-21T10:00:00Z", execution_lane: "sandbox" as const },
    { session_id: "c", title: "sandbox only", updated_at: "2026-10-19T10:00:00Z", execution_lane: "sandbox" as const },
  ];
  const merged = mergeSessionLists(stable, sandbox);
  assert.equal(merged.length, 3);
  assert.deepEqual(merged.map((s) => s.session_id).sort(), ["a", "b", "c"]);
  const a = merged.find((s) => s.session_id === "a")!;
  assert.equal(a.execution_lane, "stable");
});

test("mergeSessionLists prefers sandbox row when newer", () => {
  const stable = [{ session_id: "x", title: "old", updated_at: "2026-10-01T00:00:00Z" }];
  const sandbox = [
    {
      session_id: "x",
      title: "new",
      updated_at: "2026-10-02T00:00:00Z",
      execution_lane: "sandbox" as const,
    },
  ];
  const merged = mergeSessionLists(stable, sandbox);
  assert.equal(merged.length, 1);
  assert.equal(merged[0].title, "new");
  assert.equal(merged[0].execution_lane, "sandbox");
});
