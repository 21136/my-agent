/**
 * Win32 Playwright: multi-project schedule.dispatch with **paid live LLM** (0x567-flash workers).
 * Not mock HTTP — requires configured provider credentials on the agent root.
 * Opt-in: REAL_MULTI_PROJECT_SCHEDULE_LIVE_OPT_IN=1 + REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN=1
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";
import {
  PROJECTS,
  edge,
  pw,
  runMultiProjectScheduleRealHostGuiE2e,
} from "./multi-project-schedule-real-host-browser-shared.mjs";

const LIVE_TERMINAL_STATUSES = new Set([
  "model_turn_finished",
  "failed",
  "limit_reached",
  "cancelled",
  "blocked",
]);

const liveGuiOptIn = process.env.REAL_MULTI_PROJECT_SCHEDULE_LIVE_OPT_IN === "1"
  && process.env.REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN === "1";

const dispatchConfirmTimeout = Number.parseInt(
  process.env.REAL_MULTI_PROJECT_SCHEDULE_LIVE_DISPATCH_CONFIRM_TIMEOUT_MS ?? "600000",
  10,
);

test("companion schedule GUI: two projects, real host dispatch, live paid LLM workers", {
  skip: !liveGuiOptIn || process.platform !== "win32" || !existsSync(pw) || !existsSync(edge)
    ? "Win32 live opt-in: REAL_MULTI_PROJECT_SCHEDULE_LIVE_OPT_IN=1 and REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN=1"
    : false,
  timeout: Math.max(900_000, dispatchConfirmTimeout + 120_000),
}, async t => {
  await runMultiProjectScheduleRealHostGuiE2e(t, {
    fixtureScriptName: "multi-project-schedule-real-host-live-fixture.py",
    dispatchConfirmTimeout,
    assertReady: ready => {
      assert.equal(ready.live_paid_llm, true);
      assert.equal(ready.synthetic_model_transport, false);
      assert.equal(ready.worker_model_id, "0x567-flash");
    },
    assertWorkerOutcomes: statuses => {
      for (const projectId of PROJECTS) {
        assert.ok(statuses[projectId], `missing worker outcome for ${projectId}`);
        assert.ok(
          LIVE_TERMINAL_STATUSES.has(statuses[projectId]),
          `unexpected worker status for ${projectId}: ${statuses[projectId]}`,
        );
      }
    },
  });
});
