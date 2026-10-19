import assert from "node:assert/strict";
import test from "node:test";

import {
  PET_AVATAR_STATES,
  buildCompanionSyncPayload,
  buildReunionStickyNote,
  calibrateCompanionFirstMetFromSessions,
  loadCompanionBond,
  localDateKey,
  parseSessionCreatedAtMs,
  petAvatarSrc,
  petTurnState,
  recordCompanionCare,
  renderPetAvatarHtml,
  touchCompanionVisit,
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

test("localDateKey uses local calendar date instead of UTC slice", () => {
  const sample = new Date(2026, 9, 19, 0, 15, 0);
  assert.equal(localDateKey(sample), "2026-10-19");
});

test("companion bond validates corrupt storage, caps lateNightDays, and preserves reunion gap on care", () => {
  const store = new Map<string, string>();
  (globalThis as unknown as { localStorage: Storage }).localStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => { store.set(k, v); },
    removeItem: (k: string) => { store.delete(k); },
    clear: () => { store.clear(); },
    key: () => null,
    length: 0,
  };

  const over400Days = Array.from({ length: 400 }, (_, i) => {
    const d = new Date(2025, 0, 1 + i);
    return localDateKey(d);
  });
  store.set(
    "ma_companion_bond_v1",
    JSON.stringify({
      firstMetAt: -999,
      lastSeenAt: Number.NaN,
      prevSeenAt: -10,
      lateNightDays: [...over400Days, "invalid-date", over400Days[0]],
      careCount: -5,
      coffeeCount: Number.NaN,
      glassesCount: 3.9,
      musicCount: -1,
      stickers: ["", "  ", "深夜党"],
    }),
  );

  const loaded = loadCompanionBond();
  assert.equal(loaded.careCount, 0);
  assert.equal(loaded.coffeeCount, 0);
  assert.equal(loaded.glassesCount, 3);
  assert.equal(loaded.musicCount, 0);
  assert.equal(loaded.lateNightDays.length, 365);
  assert.deepEqual(loaded.stickers, ["深夜党"]);

  // Simulate opening the app 26 hours after last visit at 00:30 local time (cross-midnight)
  const t0 = new Date(2026, 9, 17, 22, 10, 0).getTime();
  const t1 = new Date(2026, 9, 19, 0, 30, 0).getTime();
  store.set(
    "ma_companion_bond_v1",
    JSON.stringify({
      ...loaded,
      firstMetAt: t0,
      prevSeenAt: t0,
      lastSeenAt: t0,
      lateNightDays: ["2026-10-17"],
      partnerNote: "",
    }),
  );

  const visited = touchCompanionVisit(t1);
  assert.equal(visited.prevSeenAt, t0);
  assert.equal(visited.lastSeenAt, t1);
  assert.ok(visited.lateNightDays.includes("2026-10-19"));

  // Clicking care button 2 minutes later must NOT overwrite prevSeenAt
  const t2 = t1 + 2 * 60_000;
  const { bond: afterCare } = recordCompanionCare("coffee", t2);
  assert.equal(afterCare.prevSeenAt, t0);
  afterCare.partnerNote = "";
  const reunion = buildReunionStickyNote(afterCare);
  assert.match(reunion, /小时|天/);

  // Calibrate firstMetAt from session_id YYYYMMDD prefix (and ignore updated_at)
  assert.equal(parseSessionCreatedAtMs("20260912-abcd1234"), new Date(2026, 8, 12, 0, 0, 0, 0).getTime());
  const calibrated = calibrateCompanionFirstMetFromSessions([
    { session_id: "nondate-id", updated_at: "2020-01-01T00:00:00Z" },
    { session_id: "20260912-abcd1234", updated_at: "2026-10-18T12:00:00Z" },
  ]);
  assert.equal(calibrated.firstMetAt, new Date(2026, 8, 12, 0, 0, 0, 0).getTime());

  const syncPayload = buildCompanionSyncPayload(t2);
  assert.equal(syncPayload.type, "companion.sync");
  assert.ok(Array.isArray(syncPayload.stickers) && syncPayload.stickers.length > 0);
  assert.ok(syncPayload.days_together >= 1);
});