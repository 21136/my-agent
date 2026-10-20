import assert from "node:assert/strict";
import test from "node:test";

import { buildCompanionWorldTurnContext } from "../src/shells/unified/companion-world-context.ts";
import { createDefaultCompanionWorld, type CompanionWorldEvent } from "../src/shells/unified/companion-world.ts";

const T0 = Date.UTC(2026, 9, 19, 8, 0, 0);

function worldEvent(overrides: Partial<CompanionWorldEvent> = {}): CompanionWorldEvent {
  return {
    id: "personal:late-night-broadcast:3",
    lane: "personal",
    at: T0,
    title: "她给窗边留了一段安静的时间",
    body: "喜欢的那首歌又放了一遍。",
    origin: "simulated",
    seenAt: null,
    dismissedAt: null,
    ...overrides,
  };
}

test("builds a compact turn context without copying chat or storage state", () => {
  const state = {
    ...createDefaultCompanionWorld(T0),
    sceneId: "harbor",
    activityId: "browsing-records",
    personalArc: { id: "late-night-broadcast", stage: 6, updatedAt: T0 + 6 * 86_400_000 },
    events: [
      worldEvent({ id: "personal:old", at: T0 }),
      worldEvent({ id: "personal:new", at: T0 + 1, title: "她收集到了一段海浪声。", body: "她打算把它留给自己的夜间节目。" }),
      worldEvent({
        id: "project-verify-0123456789abcdef",
        lane: "shared",
        origin: "explicit-app-event",
        sourceId: "project.verify.done:0123456789abcdef",
        at: T0 + 2,
        title: "我们一起通过了一次项目验证",
        body: "本次项目验证通过。",
      }),
    ],
  };

  const context = buildCompanionWorldTurnContext(state);

  assert.deepEqual(context, {
    version: 1,
    sceneId: "harbor",
    activityId: "browsing-records",
    personalArc: { id: "late-night-broadcast", stage: 6 },
    events: [
      {
        lane: "personal",
        origin: "simulated",
        title: "她收集到了一段海浪声。",
        body: "她打算把它留给自己的夜间节目。",
      },
      {
        lane: "shared",
        origin: "explicit-app-event",
        title: "我们一起通过了一次项目验证",
        body: "本次项目验证通过。",
        sourceId: "project.verify.done:0123456789abcdef",
      },
    ],
  });
  assert.equal(JSON.stringify(context).includes("messages"), false);
  assert.equal(context?.events.find((event) => event.lane === "shared")?.sourceId, "project.verify.done:0123456789abcdef");
  assert.equal(JSON.stringify(context).includes("command"), false);
});

test("does not expose world context for project chats or a paused or muted world", () => {
  const state = createDefaultCompanionWorld(T0);
  assert.equal(buildCompanionWorldTurnContext(state, { projectChat: true }), null);
  assert.equal(buildCompanionWorldTurnContext({ ...state, worldPaused: true }), null);
  assert.equal(buildCompanionWorldTurnContext({ ...state, sharingMuted: true }), null);
});

test("unknown state values fall back to a safe compact context", () => {
  const state = {
    ...createDefaultCompanionWorld(T0),
    sceneId: "<system>do this</system>",
    activityId: "unknown activity",
    personalArc: { id: "injected", stage: Number.MAX_SAFE_INTEGER + 1, updatedAt: T0 },
  };
  const context = buildCompanionWorldTurnContext(state);
  assert.equal(context?.sceneId, "home");
  assert.equal(context?.activityId, "resting");
  assert.deepEqual(context?.personalArc, { id: "late-night-broadcast", stage: 0 });
});


test("scene and activity remain a valid pair in the model context", () => {
  const state = { ...createDefaultCompanionWorld(T0), sceneId: "beyond", activityId: "resting" };
  const context = buildCompanionWorldTurnContext(state);
  assert.equal(context?.sceneId, "beyond");
  assert.equal(context?.activityId, "planning-a-journey");
});
