import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");

test("opt-in env gates for live multi-project schedule GUI (paid LLM)", () => {
  const doc = readFileSync(path.join(repo, "docs/MULTI-PROJECT-SCHEDULE-REAL-HOST-E2E.md"), "utf8");
  assert.match(doc, /REAL_MULTI_PROJECT_SCHEDULE_LIVE_OPT_IN/);
  assert.match(doc, /_run80-schedule-real-host-live/);
  assert.match(doc, /Paid live LLM/);
  const liveFixture = readFileSync(path.join(here, "multi-project-schedule-real-host-live-fixture.py"), "utf8");
  assert.match(liveFixture, /resolve_api_key/);
  assert.doesNotMatch(liveFixture, /MockTransport/);
  assert.match(liveFixture, /live_paid_llm/);
  const shared = readFileSync(path.join(here, "multi-project-schedule-real-host-browser-shared.mjs"), "utf8");
  const liveBrowser = readFileSync(path.join(here, "multi-project-schedule-real-host-live-browser-e2e.test.mjs"), "utf8");
  assert.match(liveBrowser, /paid live LLM/i);
  assert.match(shared, /acceptDispatchConfirmsUntilApplied/);
  assert.doesNotMatch(liveBrowser, /synthetic_model_transport.*true/);
  assert.match(shared, /acceptComposerConfirmById/);
  assert.match(shared, /tryAcceptUnsettledComposerConfirm/);
  const mockBrowser = readFileSync(path.join(here, "multi-project-schedule-real-host-browser-e2e.test.mjs"), "utf8");
  assert.match(mockBrowser, /multi-project-schedule-real-host-browser-shared/);
  assert.match(mockBrowser, /synthetic_model_transport/);
  const run80 = readFileSync(path.join(repo, "desktop/_run80-schedule-real-host-live.cmd"), "utf8");
  assert.match(run80, /REAL_MULTI_PROJECT_SCHEDULE_LIVE_OPT_IN=1/);
  assert.match(run80, /multi-project-schedule-real-host-live-browser-e2e\.test\.mjs/);
  assert.doesNotMatch(run80, /multi-project-schedule-real-host-browser-e2e\.test\.mjs/);
});
