import assert from "node:assert/strict";
import test from "node:test";

import {
  advanceCompanionWorld,
  createDefaultCompanionWorld,
  normalizeCompanionWorld,
  recordExplicitSharedEvent,
} from "../src/shells/unified/companion-world.ts";
import { verifiedProjectEventToSharedEvent } from "../src/shells/unified/companion-world-app-events.ts";

const DAY_MS = 24 * 60 * 60 * 1000;
const T0 = Date.UTC(2026, 9, 19, 8, 0, 0);

test("default world starts isolated from chat and bond counters", () => {
  const state = createDefaultCompanionWorld(T0);

  assert.equal(state.schemaVersion, 1);
  assert.equal(state.lastSimulatedAt, T0);
  assert.equal(state.sceneId, "home");
  assert.equal(state.activityId, "resting");
  assert.deepEqual(state.personalArc, {
    id: "late-night-broadcast",
    stage: 0,
    updatedAt: T0,
  });
  assert.deepEqual(state.events, []);
  assert.equal(state.worldPaused, false);
  assert.equal(state.sharingMuted, false);
  assert.equal("messages" in state, false);
  assert.equal("careCount" in state, false);
});

test("offline progression advances only complete days and is idempotent", () => {
  const initial = createDefaultCompanionWorld(T0);
  const first = advanceCompanionWorld(initial, T0 + DAY_MS + 30 * 60 * 1000);

  assert.equal(first.state.personalArc.stage, 1);
  assert.equal(first.state.lastSimulatedAt, T0 + DAY_MS);
  assert.equal(first.addedEvents.length, 0);

  const repeated = advanceCompanionWorld(first.state, T0 + DAY_MS + 30 * 60 * 1000);
  assert.equal(repeated.state.personalArc.stage, 1);
  assert.equal(repeated.addedEvents.length, 0);
});

test("a long absence coalesces missed milestones into at most one personal event", () => {
  const initial = createDefaultCompanionWorld(T0);
  const advanced = advanceCompanionWorld(initial, T0 + 12 * DAY_MS);

  assert.equal(advanced.state.personalArc.stage, 12);
  assert.equal(advanced.state.lastSimulatedAt, T0 + 12 * DAY_MS);
  assert.equal(advanced.addedEvents.length, 1);
  assert.equal(advanced.addedEvents[0].lane, "personal");
  assert.equal(advanced.addedEvents[0].origin, "simulated");
  assert.equal(advanced.addedEvents[0].at, T0 + 12 * DAY_MS);
  assert.equal(advanced.state.events.length, 1);
});

test("paused world does not progress or catch up after the pause", () => {
  const initial = { ...createDefaultCompanionWorld(T0), worldPaused: true };
  const paused = advanceCompanionWorld(initial, T0 + 10 * DAY_MS);

  assert.equal(paused.state.personalArc.stage, 0);
  assert.equal(paused.state.lastSimulatedAt, T0 + 10 * DAY_MS);
  assert.deepEqual(paused.addedEvents, []);

  const resumed = advanceCompanionWorld({ ...paused.state, worldPaused: false }, T0 + 10 * DAY_MS + DAY_MS);
  assert.equal(resumed.state.personalArc.stage, 1);
});

test("unknown schema and malformed values normalize to safe world state", () => {
  const normalized = normalizeCompanionWorld({
    schemaVersion: 99,
    lastSimulatedAt: Number.NaN,
    sceneId: "<script>",
    activityId: "",
    personalArc: null,
    events: [{ id: "x", lane: "shared", origin: "explicit-app-event", sourceId: "" }],
    worldPaused: "yes",
    sharingMuted: 0,
  }, T0);

  assert.deepEqual(normalized, createDefaultCompanionWorld(T0));
});

test("explicit shared events require a source and duplicate IDs are ignored", () => {
  const initial = createDefaultCompanionWorld(T0);
  const input = { id: "project-done-1", title: "一起完成了一个节点", body: "这条来自应用已确认的完成事件。", sourceId: "turn:42" };
  const missingSource = recordExplicitSharedEvent(initial, { ...input, sourceId: "  " }, T0 + 1000);
  assert.deepEqual(missingSource, initial);

  const recorded = recordExplicitSharedEvent(initial, input, T0 + 1000);
  assert.equal(recorded.events.length, 1);
  assert.equal(recorded.events[0].lane, "shared");
  assert.equal(recorded.events[0].origin, "explicit-app-event");
  assert.equal(recorded.events[0].sourceId, "turn:42");
  assert.equal(recorded.events[0].seenAt, null);

  const duplicate = recordExplicitSharedEvent(recorded, input, T0 + 2000);
  assert.equal(duplicate.events.length, 1);
});

test("explicit application provenance can only back shared-lane events", () => {
  const base = createDefaultCompanionWorld(T0);
  const normalized = normalizeCompanionWorld({
    ...base,
    events: [{
      id: "bad-provenance",
      lane: "personal",
      at: T0,
      title: "not a personal simulation",
      body: "An explicit app event must stay in the shared lane.",
      origin: "explicit-app-event",
      sourceId: "turn:7",
      seenAt: null,
      dismissedAt: null,
    }],
  }, T0);

  assert.deepEqual(normalized.events, []);
});

test("only a successful explicit project verification can become a shared event", () => {
  const event = {
    type: "project.verify.done" as const,
    ok: true,
    passed: true,
    exit_code: 0,
    command: "npm test",
    path: "src/index.ts",
  };
  const shared = verifiedProjectEventToSharedEvent(event);

  assert.ok(shared);
  assert.equal(shared.title, "我们一起通过了一次项目验证");
  assert.equal(shared.body, "本次项目验证通过。");
  assert.match(shared.sourceId, /^project\.verify\.done:[a-f0-9]{16}$/);
  assert.doesNotMatch(shared.sourceId, /src\/index|npm test|project-42/);
  assert.deepEqual(verifiedProjectEventToSharedEvent(event), shared);
  assert.equal(verifiedProjectEventToSharedEvent({ ...event, ok: false }), null);
  assert.equal(verifiedProjectEventToSharedEvent({ ...event, passed: false }), null);

});



