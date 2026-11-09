import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { buildScheduleLaunchMessage, renderScheduleLaunchForm, scheduledWorkerCandidates } from "../src/shells/unified/schedule-launch.ts";

const result = {
  type: "work-records.overview.result", session_id: "s1", request_id: "r1", state: "visible",
  aggregation_scope: "selected_project_sessions", selected_project_ids: ["p1"], schedule_preview: {
    state: "visible", candidates: [{ project_id: "p1", item_id: "i1", requirement_id: "q1", priority: 4 }],
    blocked: [], limitations: [], omissions: [],
  }, groups: [{ kind: "project", id: "p1", visibility: "visible", status: "queued",
    status_counts: { queued: 1 }, item_count: 1, requirement_count: 1, items: [{ item_id: "i1", title: "Task",
      status: "queued", status_counts: { queued: 1 }, priority: 4, current_results: [],
      requirements: [{ requirement_id: "q1", text: "Fix the exact bug", status: "queued" }] }], items_page: null }],
  unread_important_results: [], unread_status: "known", visibility_limitations: [],
  visibility_counts: { visible_groups: 1, visible_items: 1, collapsed_groups: 0, collapsed_items: 0 }, snapshot_id: "snap",
  page: { page_size: 6, offset: 0, total_groups: 1, total_visible_groups: 1, total_visible_items: 1,
    total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null },
} as any;

test("schedule launcher ties visible candidate to exact projected text and emits explicit bounded user request", () => {
  assert.equal(scheduledWorkerCandidates(result)[0].task, "Fix the exact bug");
  const markup = renderScheduleLaunchForm(result);
  assert.match(markup, /data-schedule-launch-send/);
  assert.match(markup, /data-schedule-read/);
  assert.match(markup, /data-schedule-task/);
  assert.match(markup, /来源要求：Fix the exact bug/);
  const message = buildScheduleLaunchMessage(result, 0, "Fix the exact bug", "PROJECT.md\nREADME.md", "NOTES.md");
  assert.match(message, /run_project_workers/);
  assert.match(message, /task 必须精确为本次任务/);
  assert.match(message, /project_id、item_id、requirement_id/);
  assert.match(message, /PROJECT\.md、README\.md/);
  assert.match(message, /NOTES\.md/);
  assert.throws(() => buildScheduleLaunchMessage(result, 0, "", "PROJECT.md", "NOTES.md"), /填写/);
  assert.throws(() => buildScheduleLaunchMessage(result, 0, "different task", "PROJECT.md", "NOTES.md"), /来源要求原文/);
  assert.throws(() => buildScheduleLaunchMessage(result, 0, "Fix the exact bug", "", "NOTES.md"), /1–12/);
  assert.throws(() => buildScheduleLaunchMessage(result, 0, "Fix the exact bug", "PROJECT.md", "../outside"), /越界/);
  const completedHead = { ...result, groups: [{ ...result.groups[0], items: [{ ...result.groups[0].items[0], requirements: [{ ...result.groups[0].items[0].requirements[0], status: "completed" }] }] }] };
  assert.deepEqual(scheduledWorkerCandidates(completedHead), []);
});

test("schedule launch leaves the composer draft untouched and reports synchronous send failures", () => {
  const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  const handlerStart = source.indexOf('scheduleForm?.querySelector<HTMLButtonElement>("[data-schedule-launch-send]")');
  const handlerEnd = source.indexOf("data-run-selected-project-history", handlerStart + 1);
  assert.notEqual(handlerStart, -1);
  const handler = source.slice(handlerStart, handlerEnd);
  assert.doesNotMatch(handler, /input\.value\s*=/, "launching must not replace or clear a draft in the composer");
  assert.match(handler, /catch \(error\)[\s\S]*setStatus\(`启动请求未发送：/,
    "send failures must be surfaced as not sent rather than reported as successful");
});

test("schedule launcher fails closed for stale, truncated, or mismatched projections", () => {
  assert.deepEqual(scheduledWorkerCandidates(null), []);
  assert.deepEqual(scheduledWorkerCandidates({ ...result, schedule_preview: { ...result.schedule_preview, omissions: ["omitted"] } }), []);
  assert.deepEqual(scheduledWorkerCandidates({ ...result, groups: [] }), []);
  assert.equal(renderScheduleLaunchForm({ ...result, state: "expired" }), "");
});
