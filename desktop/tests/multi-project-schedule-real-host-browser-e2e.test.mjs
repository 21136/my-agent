/**
 * Win32 Playwright: companion 当前工作 schedule tab → real schedule.dispatch → real host.
 * Mock model HTTP only. Opt-in: REAL_MULTI_PROJECT_SCHEDULE_OPT_IN=1
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";
import {
  edge,
  pw,
  runMultiProjectScheduleRealHostGuiE2e,
} from "./multi-project-schedule-real-host-browser-shared.mjs";

const guiOptIn = process.env.REAL_MULTI_PROJECT_SCHEDULE_OPT_IN === "1"
  && process.env.REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN === "1";

test("companion schedule GUI: two projects, real host dispatch, distinct outcomes", {
  skip: !guiOptIn || process.platform !== "win32" || !existsSync(pw) || !existsSync(edge)
    ? "Win32 opt-in: REAL_MULTI_PROJECT_SCHEDULE_OPT_IN=1 and REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN=1"
    : false,
  timeout: 360000,
}, async t => {
  await runMultiProjectScheduleRealHostGuiE2e(t, {
    fixtureScriptName: "multi-project-schedule-real-host-fixture.py",
    assertReady: ready => {
      assert.equal(ready.synthetic_model_transport, true);
    },
    assertWorkerOutcomes: statuses => {
      assert.equal(statuses["mp-host-alpha"], "model_turn_finished");
      assert.equal(statuses["mp-host-beta"], "failed");
    },
  });
});
