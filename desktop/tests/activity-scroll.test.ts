import assert from "node:assert/strict";
import test from "node:test";

const scrollModule = import("../src/shells/unified/activity-scroll.ts").catch(() => undefined);
type Bounds = { top: number; bottom: number };
type DeltaHelper = (container: Bounds, target: Bounds, inset?: number) => number;

async function getDelta(container: Bounds, target: Bounds): Promise<number> {
  const module = await scrollModule as { activityRevealScrollDelta?: DeltaHelper } | undefined;
  assert.ok(module, "activity scroll helper module should be available");
  assert.equal(typeof module.activityRevealScrollDelta, "function", "activity scroll helper should export reveal geometry");
  return module.activityRevealScrollDelta!(container, target);
}

test("does not recenter a tool record that is already visible in the chat", async () => {
  assert.equal(await getDelta({ top: 100, bottom: 900 }, { top: 850, bottom: 874 }), 0);
});

test("scrolls only enough to reveal a tool record below the chat viewport", async () => {
  assert.equal(await getDelta({ top: 100, bottom: 900 }, { top: 910, bottom: 930 }), 42);
});

test("scrolls only enough to reveal a tool record above the chat viewport", async () => {
  assert.equal(await getDelta({ top: 100, bottom: 900 }, { top: 80, bottom: 110 }), -32);
});
