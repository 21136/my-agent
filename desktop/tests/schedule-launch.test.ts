import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { buildScheduleDispatchWorkers, buildScheduleLaunchMessage, renderScheduleLaunchForm, scheduledWorkerCandidates } from "../src/shells/unified/schedule-launch.ts";

const result = {
  type: "work-records.overview.result", session_id: "s1", request_id: "r1", state: "visible",
  aggregation_scope: "selected_project_sessions", selected_project_ids: ["p1", "p2"], schedule_preview: {
    state: "visible", candidates: [
      { project_id: "p1", item_id: "i1", requirement_id: "q1", priority: 4, task: "Fix the exact bug",
        suggested_read_targets: ["PROJECT.md"], suggested_write_targets: ["NOTES.md"] },
      { project_id: "p2", item_id: "i2", requirement_id: "q2", priority: 3, task: "Patch README link",
        suggested_read_targets: ["README.md"], suggested_write_targets: ["README.md"] },
    ],
    blocked: [], limitations: [], omissions: [],
  }, groups: [
    { kind: "project", id: "p1", visibility: "visible", status: "queued",
      status_counts: { queued: 1 }, item_count: 1, requirement_count: 1, items: [{ item_id: "i1", title: "Task",
        status: "queued", status_counts: { queued: 1 }, priority: 4, current_results: [],
        requirements: [{ requirement_id: "q1", text: "Fix the exact bug", status: "queued" }] }], items_page: null },
    { kind: "project", id: "p2", visibility: "visible", status: "queued",
      status_counts: { queued: 1 }, item_count: 1, requirement_count: 1, items: [{ item_id: "i2", title: "Doc",
        status: "queued", status_counts: { queued: 1 }, priority: 3, current_results: [],
        requirements: [{ requirement_id: "q2", text: "Patch README link", status: "queued" }] }], items_page: null },
  ],
  unread_important_results: [], unread_status: "known", visibility_limitations: [],
  visibility_counts: { visible_groups: 2, visible_items: 2, collapsed_groups: 0, collapsed_items: 0 }, snapshot_id: "snap",
  page: { page_size: 6, offset: 0, total_groups: 2, total_visible_groups: 2, total_visible_items: 2,
    total_collapsed_groups: 0, total_collapsed_items: 0, has_more: false, next_cursor: null },
} as any;

test("schedule dispatch payload matches authorized candidate and explicit paths", () => {
  const payload = buildScheduleDispatchWorkers(result, [{
    candidateIndex: 0, taskText: "Fix the exact bug", readText: "PROJECT.md", writeText: "NOTES.md",
  }]);
  assert.equal(payload.workers[0].task, "Fix the exact bug");
  assert.deepEqual(payload.workers[0].write_targets, ["NOTES.md"]);
  assert.deepEqual(payload.workers[0].read_targets, ["PROJECT.md"]);
});

test("parallel batch builds two distinct project workers from preview order", () => {
  const batch = scheduledWorkerCandidates(result);
  assert.equal(batch.length, 2);
  assert.deepEqual(batch.map(row => row.projectId), ["p1", "p2"]);
  const payload = buildScheduleDispatchWorkers(result, [
    { candidateIndex: 0, taskText: batch[0].task, readText: "PROJECT.md", writeText: "NOTES.md" },
    { candidateIndex: 1, taskText: batch[1].task, readText: "README.md", writeText: "README.md" },
  ]);
  assert.equal(payload.workers.length, 2);
  assert.equal(new Set(payload.workers.map(worker => worker.project_id)).size, 2);
});

test("schedule launcher prefills task and paths and exposes one-click dispatch CTA", () => {
  assert.equal(scheduledWorkerCandidates(result)[0].task, "Fix the exact bug");
  const markup = renderScheduleLaunchForm(result);
  assert.match(markup, /data-schedule-launch-send/);
  assert.match(markup, /按预览启动/);
  assert.match(markup, /data-schedule-read/);
  assert.match(markup, /data-schedule-task/);
  assert.match(markup, /来源要求：Fix the exact bug/);
  assert.match(markup, /并行/);
  assert.doesNotMatch(markup, /data-schedule-candidate/);
  const message = buildScheduleLaunchMessage(result, 0, "Fix the exact bug", "PROJECT.md\nREADME.md", "NOTES.md");
  assert.match(message, /run_project_workers/);
  assert.throws(() => buildScheduleLaunchMessage(result, 0, "", "PROJECT.md", "NOTES.md"), /任务必须与来源要求/);
  assert.throws(() => buildScheduleLaunchMessage(result, 0, "different task", "PROJECT.md", "NOTES.md"), /任务必须与来源要求/);
  assert.throws(() => buildScheduleDispatchWorkers(result, [{ candidateIndex: 0, taskText: "Fix the exact bug", readText: "", writeText: "NOTES.md" }]), /1–12/);
  assert.throws(() => buildScheduleDispatchWorkers(result, [{ candidateIndex: 0, taskText: "Fix the exact bug", readText: "PROJECT.md", writeText: "../outside" }]), /越界/);
  const completedHead = {
    ...result,
    schedule_preview: { ...result.schedule_preview, candidates: [result.schedule_preview.candidates[1]] },
    groups: [{ ...result.groups[0], items: [{ ...result.groups[0].items[0], requirements: [{ ...result.groups[0].items[0].requirements[0], status: "completed" }] }] }, result.groups[1]],
  };
  assert.deepEqual(scheduledWorkerCandidates(completedHead).map(row => row.projectId), ["p2"]);
});

test("schedule launch uses direct work-records.schedule.dispatch instead of chat", () => {
  const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  const handlerStart = source.indexOf('scheduleForm?.querySelector<HTMLButtonElement>("[data-schedule-launch-send]")');
  const handlerEnd = source.indexOf("data-run-selected-project-history", handlerStart + 1);
  assert.notEqual(handlerStart, -1);
  const handler = source.slice(handlerStart, handlerEnd);
  assert.match(handler, /dispatchScheduleWorkers/);
  assert.doesNotMatch(handler, /sendMessage\(/);
});

test("schedule launcher fails closed for stale, truncated, or mismatched projections", () => {
  assert.deepEqual(scheduledWorkerCandidates(null), []);
  assert.deepEqual(scheduledWorkerCandidates({ ...result, schedule_preview: { ...result.schedule_preview, omissions: ["omitted"] } }), []);
  assert.deepEqual(scheduledWorkerCandidates({ ...result, groups: [] }), []);
  assert.equal(renderScheduleLaunchForm({ ...result, state: "expired" }), "");
});
