import assert from "node:assert/strict";
import test from "node:test";

import {
  COMPANION_WORLD_STORAGE_KEY,
  loadCompanionWorld,
  saveCompanionWorld,
  selectWorldShareCandidate,
} from "../src/shells/unified/companion-world-store.ts";
import {
  createDefaultCompanionWorld,
  type CompanionWorldEvent,
} from "../src/shells/unified/companion-world.ts";

const T0 = Date.UTC(2026, 9, 19, 8, 0, 0);

class MemoryStorage {
  readonly values = new Map<string, string>();
  readonly reads: string[] = [];
  failWrites = false;

  getItem(key: string): string | null {
    this.reads.push(key);
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    if (this.failWrites) throw new Error("storage unavailable");
    this.values.set(key, value);
  }
}

function event(overrides: Partial<CompanionWorldEvent> = {}): CompanionWorldEvent {
  return {
    id: "personal:late-night-broadcast:3",
    lane: "personal",
    at: T0,
    title: "她把几首喜欢的歌整理成节目单",
    body: "这是一件只发生在她世界里的小事。",
    origin: "simulated",
    seenAt: null,
    dismissedAt: null,
    ...overrides,
  };
}

test("loads only the versioned world key and initializes missing state", () => {
  const storage = new MemoryStorage();
  storage.values.set("ma_companion_bond_v1", JSON.stringify({ careCount: 17 }));

  const loaded = loadCompanionWorld(storage, T0);

  assert.deepEqual(storage.reads, [COMPANION_WORLD_STORAGE_KEY]);
  assert.deepEqual(loaded, createDefaultCompanionWorld(T0));
  assert.deepEqual(JSON.parse(storage.values.get(COMPANION_WORLD_STORAGE_KEY) || "null"), loaded);
  assert.equal(storage.values.get("ma_companion_bond_v1"), JSON.stringify({ careCount: 17 }));
});

test("corrupt world JSON recovers to a valid default without throwing", () => {
  const storage = new MemoryStorage();
  storage.values.set(COMPANION_WORLD_STORAGE_KEY, "{broken");

  const loaded = loadCompanionWorld(storage, T0);

  assert.deepEqual(loaded, createDefaultCompanionWorld(T0));
  assert.deepEqual(JSON.parse(storage.values.get(COMPANION_WORLD_STORAGE_KEY) || "null"), loaded);
});

test("saving returns false instead of blocking the app when storage writes fail", () => {
  const storage = new MemoryStorage();
  storage.failWrites = true;

  assert.equal(saveCompanionWorld(createDefaultCompanionWorld(T0), storage), false);
});

test("share candidate is quiet during every busy, paused, or muted state", () => {
  const base = {
    ...createDefaultCompanionWorld(T0),
    events: [event()],
  };
  const idle = { busy: false, confirmPending: false, composing: false };

  assert.equal(selectWorldShareCandidate(base, idle)?.id, base.events[0].id);
  assert.equal(selectWorldShareCandidate({ ...base, worldPaused: true }, idle), null);
  assert.equal(selectWorldShareCandidate({ ...base, sharingMuted: true }, idle), null);
  assert.equal(selectWorldShareCandidate(base, { ...idle, busy: true }), null);
  assert.equal(selectWorldShareCandidate(base, { ...idle, confirmPending: true }), null);
  assert.equal(selectWorldShareCandidate(base, { ...idle, composing: true }), null);
});

test("seen and dismissed events are not selected again", () => {
  const base = {
    ...createDefaultCompanionWorld(T0),
    events: [
      event({ id: "old-seen", at: T0, seenAt: T0 + 1 }),
      event({ id: "old-dismissed", at: T0 + 2, dismissedAt: T0 + 3 }),
    ],
  };

  assert.equal(selectWorldShareCandidate(base, { busy: false, confirmPending: false, composing: false }), null);
});
