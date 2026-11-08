import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const ts = createRequire(import.meta.url)("typescript");
const url = new URL("../src/shells/unified/merged-history-pages.ts", import.meta.url);
const exports = {};
const js = ts.transpileModule(readFileSync(url, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
new Function("exports", js)(exports);
const { appendMergedHistoryPage, clearMergedHistory } = exports;
const state = () => ({ rows: [], page: null, sources: [] });
const sources = [{ project_id: "p1", session_id: "s1", revision: 1 }, { project_id: "p2", session_id: "s2", revision: 4 }];
const row = (result_id, project_id = "p1", session_id = "s1") => ({ result_id, project_id, session_id, item_id: "i", requirement_id: "r", run_id: `run-${result_id}`, status: "completed", summary: "x", verification_status: "verified", recorded_at: 1 });
const page = (offset, has_more, next_cursor) => ({ scope: "selected_projects_history", page_size: 10, offset, has_more, next_cursor, total: null });

test("merged history accepts only continuous cursor pages and keeps short terminal page", () => {
  const first = appendMergedHistoryPage(state(), page(0, true, "cursor-1"), sources, Array.from({ length: 10 }, (_, i) => row(`r${i}`)));
  assert.ok(first);
  assert.equal(appendMergedHistoryPage(first, page(10, false, null), sources, [row("last")], "wrong-cursor"), null);
  assert.equal(appendMergedHistoryPage(first, page(10, false, null), sources, [row("last")], "cursor-1").rows.length, 11);
  assert.equal(appendMergedHistoryPage(first, page(9, false, null), sources, [row("last")], "cursor-1"), null);
  assert.equal(appendMergedHistoryPage(first, page(10, true, "cursor-2"), sources, [], "cursor-1"), null);
});

test("merged history rejects duplicate identities and source/session/revision drift", () => {
  assert.equal(appendMergedHistoryPage(state(), page(0, false, null), sources, [row("same"), row("same")]), null);
  const first = appendMergedHistoryPage(state(), page(0, true, "cursor-1"), sources, Array.from({ length: 10 }, (_, i) => row(`r${i}`)));
  assert.ok(first);
  assert.equal(appendMergedHistoryPage(first, page(10, false, null), sources.map(s => s.project_id === "p1" ? { ...s, session_id: "new-session" } : s), [row("last")], "cursor-1"), null);
  assert.deepEqual(clearMergedHistory(first), state());
});
