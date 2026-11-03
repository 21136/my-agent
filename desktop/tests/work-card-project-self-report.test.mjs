import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const ts = createRequire(import.meta.url)("typescript");
const exports = {};
const url = new URL("../src/shells/unified/work-card-presentation.ts", import.meta.url);
const js = ts.transpileModule(readFileSync(url, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
new Function("exports", js)(exports);
const { renderWorkCardSnapshot } = exports;
const validProjectTest = {
  status: "passed", source: "user_authorized_project_self_report",
  pytest_target: "tests/test_target.py::test_case", exit_code: 0,
};
function projectCard(projectTest, overrides = {}) {
  const result = {
    session_id: "session-a", project_id: "project-a", item_id: "item-a", requirement_id: "requirement-a",
    result_id: "result-a", run_id: "run-a", status: "unknown", summary: "host lifecycle summary",
    verification_status: "not_verified", sources: [],
    ...(projectTest === undefined ? {} : { project_test: projectTest }), ...overrides.result,
  };
  return {
    card_id: `wc-${"a".repeat(64)}`, saved_at: "2026-11-01T12:00:00Z", viewed_at: null,
    snapshot: {
      kind: "project", session_id: "session-a", project_id: "project-a", captured_at: "2026-11-01T12:00:00Z",
      completeness: "complete", item_count: 1, requirement_count: 1, result_count: 1,
      omitted_item_count: 0, omitted_requirement_count: 0, omitted_result_count: 0, incomplete_reason: null,
      persisted: false, history_complete: true, missing_current_result_count: 0,
      cards: [{ session_id: "session-a", project_id: "project-a", item_id: "item-a", title: "Synthetic",
        status: "unknown", source_refs: [], requirements: [{ session_id: "session-a", project_id: "project-a",
          item_id: "item-a", requirement_id: "requirement-a", text: "Synthetic requirement", status: "unknown",
          source_ref: null, latest_result: result, result_count: 1, omitted_result_count: 0,
          current_result_missing: false, current_run_id: "run-a", latest_result_is_current: true, ...overrides.requirement }] }],
    },
  };
}

test("existing work card displays only the current project self-report target, without upgrading acceptance", () => {
  const html = renderWorkCardSnapshot(projectCard(validProjectTest));
  assert.match(html, /项目自报通过，非整体验收/);
  assert.match(html, /tests\/test_target\.py::test_case/);
  assert.doesNotMatch(html, /验收通过|已验证/);
  assert.doesNotMatch(html, /user_authorized_project_self_report|exit_code/);
});

test("legacy cards with no self-report or explicit null stay unchanged", () => {
  for (const value of [undefined, null]) {
    const html = renderWorkCardSnapshot(projectCard(value));
    assert.doesNotMatch(html, /项目自报通过，非整体验收|项目自报告测试记录无法核对/);
  }
});

test("only exact passed self-report on an unknown unverified current run is shown", () => {
  for (const malformed of [
    { ...validProjectTest, source: "model_claim" },
    { ...validProjectTest, exit_code: 1 },
    { ...validProjectTest, pytest_target: "../../outside.py::test_case" },
    { ...validProjectTest, extra: "unexpected" },
  ]) {
    const html = renderWorkCardSnapshot(projectCard(malformed));
    assert.doesNotMatch(html, /项目自报通过，非整体验收/);
  }
  const stale = renderWorkCardSnapshot(projectCard(validProjectTest, { requirement: { latest_result_is_current: false } }));
  assert.doesNotMatch(stale, /项目自报通过，非整体验收/);
  const verified = renderWorkCardSnapshot(projectCard(validProjectTest, { result: { verification_status: "passed" } }));
  assert.doesNotMatch(verified, /项目自报通过，非整体验收/);
  const completed = renderWorkCardSnapshot(projectCard(validProjectTest, { result: { status: "completed" } }));
  assert.doesNotMatch(completed, /项目自报通过，非整体验收/);
});

