import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

const ts = createRequire(import.meta.url)("typescript");
const cache = new Map();
function load(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const exports = {};
  cache.set(url.href, exports);
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function("exports", "require", js)(exports, id => load(new URL(id.endsWith(".ts") ? id : `${id}.ts`, url)));
  return exports;
}
const { IncidentRepairState } = load(new URL("../src/shells/unified/incident-repair.ts", import.meta.url));

const detected = (incident_id = "incident-a", repair_available = true) => ({
  type: "incident.detected", incident_id, kind: "tool_result_not_saved",
  effect: "tool_result_not_saved", repair_available,
});
const status = (incident_id, request_id, status) => ({
  type: "incident.repair.status", incident_id, request_id, status,
  reason: "private backend detail must not enter the UI state",
});

test("incident repair requires explicit availability and sends only the scoped request", () => {
  const state = new IncidentRepairState();
  assert.equal(state.acceptDetected(detected("no-repair", false)), true);
  assert.equal(state.view().status, "unavailable");
  assert.equal(state.begin("request-a"), null);
  assert.equal(state.acceptDetected(detected()), true);
  assert.deepEqual(state.begin("request-a"), {
    type: "incident.repair", incident_id: "incident-a", request_id: "request-a",
  });
  assert.equal(state.view().status, "requesting");
  assert.equal(state.sendFailed("request-a"), true);
  assert.equal(state.view().status, "send_failed");
  assert.deepEqual(state.begin("request-b"), {
    type: "incident.repair", incident_id: "incident-a", request_id: "request-b",
  });
  assert.equal(state.begin("request-c"), null, "duplicate action is suppressed until the server responds");
});

test("repair status is bound to both incident and request and never retains backend reason", () => {
  const state = new IncidentRepairState();
  state.acceptDetected(detected());
  state.begin("request-a");
  assert.equal(state.acceptStatus(status("incident-b", "request-a", "published")), false);
  assert.equal(state.acceptStatus(status("incident-a", "request-b", "published")), false);
  assert.equal(state.view().status, "requesting");
  state.acceptDetected(detected());
  assert.equal(state.view().status, "requesting", "duplicate detection must not reset in-flight repair state");
  for (const value of ["running", "candidate", "published", "blocked", "failed"]) {
    const isolated = new IncidentRepairState();
    isolated.acceptDetected(detected());
    isolated.begin("request-a");
    assert.equal(isolated.acceptStatus(status("incident-a", "request-a", value)), true);
    assert.equal(isolated.view().status, value);
    assert.equal(Object.hasOwn(isolated.view(), "reason"), false);
  }
  const candidate = new IncidentRepairState();
  candidate.acceptDetected(detected());
  candidate.begin("request-c");
  assert.equal(candidate.acceptStatus(status("incident-a", "request-c", "candidate")), true);
  assert.equal(candidate.acceptStatus(status("incident-a", "request-c", "running")), false, "status cannot regress from candidate");
  assert.equal(candidate.acceptStatus(status("incident-a", "request-c", "published")), true);
  assert.equal(candidate.acceptStatus(status("incident-a", "request-c", "failed")), false, "terminal status cannot be replaced");
  assert.equal(state.acceptStatus(status("incident-a", "request-a", "unknown")), false);
  state.reset();
  assert.equal(state.view(), null, "session boundaries clear the renderer-only incident projection");
});

test("malformed and unrelated incidents are ignored", () => {
  const state = new IncidentRepairState();
  assert.equal(state.acceptDetected({ ...detected(), effect: "other" }), false);
  assert.equal(state.acceptDetected({ ...detected(), repair_available: "yes" }), false);
  assert.equal(state.view(), null);
});

test("unified shell keeps incident repair inside activity UI, not transcript blocks", () => {
  const shell = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  assert.match(shell, /data-incident-repair/);
  assert.match(shell, /client\.send\(payload\)/);
  assert.match(shell, /case "incident\.detected"[\s\S]*?incidentRepair\.acceptDetected\(event\)/);
  assert.match(shell, /case "incident\.repair\.status"[\s\S]*?incidentRepair\.acceptStatus\(event\)/);
  assert.match(shell, /candidate: "候选方案待确认"/);
  assert.match(shell, /published: "已发布修复"/);
  assert.doesNotMatch(shell, /chat\.handleEvent\(event\)[\s\S]{0,100}incidentRepair/);
});
