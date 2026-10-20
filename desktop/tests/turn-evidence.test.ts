import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeTurnEvidenceEvent,
  renderTurnEvidenceSummary,
} from "../src/shells/unified/turn-evidence.ts";

const run = normalizeTurnEvidenceEvent({
  type: "turn.evidence",
  run_id: "run-new",
  total_count: 2,
  items: [
    {
      tool: "structured_test",
      ok: true,
      call_id: "test-call",
      test: {
        status: "failed",
        suite: "pytest",
        duration_ms: 1234,
        failed: [{ file: "tests/<unsafe>.py", line: 9, test: "test_<script>bad</script>", message: "secret output should be omitted" }],
      },
      paths: ["tests/<unsafe>.py"],
    },
    {
      tool: "interactive_terminal",
      ok: true,
      call_id: "terminal-call",
      terminal_session_id: "it-0123456789abcdef",
      terminal_state: "running",
    },
  ],
}, "run-new");


test("accepts only evidence tied to the active run", () => {
  assert.ok(run);
  assert.equal(run.runId, "run-new");
  assert.equal(normalizeTurnEvidenceEvent({
    type: "turn.evidence",
    run_id: "run-old",
    items: [{ tool: "write_text", ok: true }],
  }, "run-new"), null);
});

test("renders test failure honestly even when the tool invocation itself succeeded", () => {
  assert.ok(run);
  const html = renderTurnEvidenceSummary({
    runId: run.runId,
    totalCount: run.totalCount,
    items: run.items,
    running: false,
    finishReason: "completed",
    expanded: true,
  });

  assert.match(html, /测试未通过/);
  assert.match(html, /tests\/&lt;unsafe&gt;\.py:9/);
  assert.match(html, /test_&lt;script&gt;bad&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /secret output should be omitted/);
  assert.match(html, /2 项操作/);
  assert.match(html, /turn-evidence-terminal/);
  assert.match(html, /it-0123456789abcdef/);
});

test("defaults to one collapsed summary and never claims tests passed when no test evidence exists", () => {
  const html = renderTurnEvidenceSummary({
    runId: "run-3",
    totalCount: 1,
    items: [{ tool: "patch_file", ok: true, paths: ["src/app.ts"] }],
    running: false,
    finishReason: "completed",
    expanded: false,
  });

  assert.match(html, /aria-expanded="false"/);
  assert.doesNotMatch(html, /sidebar-turn-summary-details/);
  assert.match(html, /未提供测试证据/);
  assert.doesNotMatch(html, /验证通过/);
});



test("keeps the evidence summary to one quiet row while a run is in progress", () => {
  const html = renderTurnEvidenceSummary({
    runId: "run-live",
    totalCount: 1,
    items: [{ tool: "structured_test", ok: true, test: { status: "passed", failed: [] } }],
    running: true,
    finishReason: "",
    expanded: true,
  });
  assert.match(html, /运行中/);
  assert.match(html, /disabled/);
  assert.doesNotMatch(html, /sidebar-turn-summary-details/);
});
