import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { ProjectWorkerProgressStatus, type ProjectWorkerActivityEvent } from "../src/shells/unified/project-worker-progress.ts";

const context = { sessionId: "session-a", runId: "run-a", executionState: "running", turnActive: true } as const;
function event(overrides: Partial<ProjectWorkerActivityEvent> = {}): ProjectWorkerActivityEvent {
  return {
    type: "activity.update", run_id: "run-a", session_id: "session-a", activity_source: "project_workers", batch_seq: 1,
    phase: "started", project_id: "alpha", started: 1, returned: 0, total: 3,
    text: "ignored host text", ...overrides,
  };
}

test("project worker status renders only safe started/returned facts for the active run and session", () => {
  const status = new ProjectWorkerProgressStatus();
  assert.equal(status.accept(event(), context, 100), "项目 alpha 已启动 · 已启动 1/3，已返回 0/3");
  assert.equal(status.accept(event({ phase: "returned", started: 1, returned: 1 }), context, 200), "项目 alpha 已返回 · 已启动 1/3，已返回 1/3");
  assert.equal(status.current(context, 201), "项目 alpha 已返回 · 已启动 1/3，已返回 1/3");
});

test("unsafe worker project identifiers are rendered only as a generic project", () => {
  const status = new ProjectWorkerProgressStatus();
  assert.equal(status.accept(event({ project_id: "项目" }), context, 100), "某项目 已启动 · 已启动 1/3，已返回 0/3");
});

test("project worker status rejects wrong session/run, terminal runs, malformed and unrelated activity", () => {
  const status = new ProjectWorkerProgressStatus();
  assert.equal(status.accept(event(), { ...context, sessionId: "session-b" }, 100), null);
  assert.equal(status.accept(event(), { ...context, runId: "run-b" }, 100), null);
  assert.equal(status.accept(event(), { ...context, executionState: "settled" }, 100), null);
  assert.equal(status.accept(event(), { ...context, turnActive: false }, 100), null);
  assert.equal(status.accept(event({ activity_source: "other" }), context, 100), null);
  assert.equal(status.accept(event({ project_id: "../../private/path" }), context, 100), null);
  assert.equal(status.accept(event({ started: 0 }), context, 100), null);
  assert.equal(status.accept(event({ returned: 2 }), context, 100), null);
  assert.equal(status.accept(event({ batch_seq: 0 }), context, 100), null);
  assert.equal(status.accept(event({ batch_seq: 1.5 }), context, 100), null);
  assert.equal(status.current({ ...context, executionState: "idle" }, 101), null);
});

test("project worker status ignores replayed or regressing counts and expires", () => {
  const status = new ProjectWorkerProgressStatus(1_000);
  assert.ok(status.accept(event(), context, 100));
  assert.equal(status.accept(event({ project_id: "beta" }), context, 200), null, "duplicate started count cannot replace the current project");
  assert.equal(status.accept(event({ phase: "returned", started: 1, returned: 1 }), context, 200)?.includes("已返回 1/3"), true);
  assert.equal(status.accept(event({ phase: "started", project_id: "beta", started: 2, returned: 1, total: 4 }), context, 250), null, "batch total cannot change inside a run");
  assert.equal(status.accept(event({ phase: "returned", started: 1, returned: 0 }), context, 300), null);
  assert.equal(status.accept(event({ started: 2, total: 4 }), context, 350), null, "batch total cannot change inside a run");
  assert.equal(status.accept({ ...event(), path: "workspace/private.txt" } as ProjectWorkerActivityEvent, context, 400), null, "unexpected fields are rejected");
  assert.equal(status.current(context, 1_201), null);
  assert.equal(status.current(context, 1_202), null);
});

test("unified shell routes project worker activity to the existing status element only", () => {
  const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  const start = source.indexOf('case "activity.update":');
  const end = source.indexOf('case "execution.state":', start);
  assert.ok(start >= 0 && end > start, "activity update handler must precede execution state handling");
  const handler = source.slice(start, end);
  assert.ok(handler.indexOf("chat.handleEvent(event)") < handler.indexOf("showProjectWorkerProgress(event)"), "existing stale-run filter must run before display");
  assert.doesNotMatch(handler, /pushUserMessage|blocks\.push|renderChat\(/);
  const displayStart = source.indexOf("function showProjectWorkerProgress(");
  const displayEnd = source.indexOf("function patchCompanionCareCounts(", displayStart);
  const display = source.slice(displayStart, displayEnd);
  assert.match(display, /sessionId:\s*chat\.model\.sessionId/);
  assert.match(display, /runId:\s*chat\.model\.executionRunId/);
  assert.match(display, /executionState:\s*chat\.model\.executionState/);
  assert.match(display, /turnActive:\s*chat\.model\.turnActive/);
  assert.match(display, /statusEl\.textContent = progressText/);
  assert.match(display, /PROJECT_WORKER_PROGRESS_TTL_MS/);
  const runStateStart = source.indexOf('case "execution.state":', end);
  const runStateEnd = source.indexOf('case "tool.start":', runStateStart);
  assert.match(source.slice(runStateStart, runStateEnd), /event\.state !== "running"[\s\S]*?clearProjectWorkerProgressStatus\(\)/);
  const turnEndStart = source.indexOf('case "turn.end":', runStateEnd);
  const turnEndEnd = source.indexOf('case "turn.notice":', turnEndStart);
  assert.match(source.slice(turnEndStart, turnEndEnd), /clearProjectWorkerProgressStatus\(\)/);
});

test("successive batches in one run reset counts and reject earlier delayed snapshots even after expiry", () => {
  const status = new ProjectWorkerProgressStatus(100);
  assert.ok(status.accept(event({ batch_seq: 1, started: 2, returned: 1, total: 3 }), context, 1));
  assert.equal(status.accept(event({ batch_seq: 2, started: 1, returned: 0, total: 1 }), context, 2),
    "项目 alpha 已启动 · 已启动 1/1，已返回 0/1");
  assert.equal(status.accept(event({ batch_seq: 1, phase: "returned", started: 3, returned: 2, total: 3 }), context, 3), null);
  status.clear(); // the shell timer clears the visible snapshot
  assert.equal(status.current(context, 103), null);
  assert.equal(status.accept(event({ batch_seq: 1, started: 3, returned: 1, total: 3 }), context, 104), null);
  assert.ok(status.accept(event({ batch_seq: 2, phase: "returned", started: 1, returned: 1, total: 1 }), context, 105));
});
