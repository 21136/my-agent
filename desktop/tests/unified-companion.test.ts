import assert from "node:assert/strict";
import test from "node:test";

import {
  PET_AVATAR_STATES,
  petAvatarSrc,
  petTurnState,
  renderPetAvatarHtml,
  type PetTurnSummary,
} from "../src/shells/unified/companion.ts";

const base: PetTurnSummary = {
  live: false,
  toolRunning: false,
  thinkingLive: false,
  replyStreaming: false,
  hasReply: false,
  toolFailed: false,
  hasNotice: false,
  confirmPending: false,
};

test("live turn: thinking while waiting, coding while a tool runs", () => {
  assert.equal(petTurnState({ ...base, live: true }), "thinking");
  assert.equal(petTurnState({ ...base, live: true, thinkingLive: true }), "thinking");
  assert.equal(petTurnState({ ...base, live: true, toolRunning: true }), "coding");
  assert.equal(petTurnState({ ...base, live: true, replyStreaming: true }), "idle");
  assert.equal(petTurnState({ ...base, live: true, toolRunning: true, confirmPending: true }), "idle");
});

test("finished turn settles to done, or error when it ended without a reply", () => {
  assert.equal(petTurnState({ ...base, hasReply: true }), "done");
  assert.equal(petTurnState({ ...base, hasReply: true, toolFailed: true }), "done");
  assert.equal(petTurnState({ ...base, toolFailed: true }), "error");
  assert.equal(petTurnState({ ...base, hasNotice: true }), "error");
  assert.equal(petTurnState(base), "done");
});

test("avatar assets resolve relative to the page, with a static still for reduced motion", () => {
  assert.equal(petAvatarSrc("coding"), "./pet-assets/avatar/pet-coding.webp");
  assert.equal(petAvatarSrc("done", true), "./pet-assets/avatar/pet-done-still.webp");
  for (const state of PET_AVATAR_STATES) {
    const html = renderPetAvatarHtml(state);
    assert.match(html, new RegExp(`data-pet-state="${state}"`));
    assert.match(html, /prefers-reduced-motion: reduce/);
    assert.match(html, new RegExp(`pet-${state}-still\\.webp`));
  }
});