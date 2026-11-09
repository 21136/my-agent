import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");

test("opt-in env gates for real multi-project schedule GUI", () => {
  const doc = readFileSync(path.join(repo, "docs/MULTI-PROJECT-SCHEDULE-REAL-HOST-E2E.md"), "utf8");
  assert.match(doc, /REAL_MULTI_PROJECT_SCHEDULE_OPT_IN/);
  assert.match(doc, /REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN/);
  const browser = readFileSync(path.join(here, "multi-project-schedule-real-host-browser-e2e.test.mjs"), "utf8");
  assert.match(browser, /synthetic_model_transport|mock/i);
  assert.match(browser, /work-records\.schedule\.dispatch\.done/);
});

