import assert from "node:assert/strict";
import { test } from "node:test";
import { createManagedReviewController, serializeManagedReview } from "../src/shells/unified/shell-dev-review.ts";

const report = {
  format: 1, mode: "managed_source_review_v1", review_digest: "a".repeat(64), execution: false, readyForMerge: false, readyForLaunch: false,
  stable: { head: "h", inventory: { entries: [], excluded: [], limitations: [] } },
  sandbox: { head: "s", inventory: { entries: [], excluded: [], limitations: [] } },
  history: { paths: [], commits: [] }, effectiveDelta: { basis: "merge_base_to_sandbox_head", paths: [] }, blockers: [], limitations: [],
};

test("controller resets synchronously before inspect then publishes a validated report", async () => {
  const events: string[] = [];
  const button = { disabled: false };
  const output = { textContent: "old full report", hidden: false };
  let resolve!: (value: { ok: true; report: typeof report }) => void;
  const controller = createManagedReviewController({
    inspect: () => new Promise((done) => { resolve = done; }), button, output, isDesktop: true,
    onReset: () => events.push("reset"), onReport: (value) => { assert.equal(value, report); events.push("report"); },
  });
  const pending = controller.inspect();
  assert.deepEqual(events, ["reset"]);
  assert.equal(output.textContent, "");
  resolve({ ok: true, report });
  await pending;
  assert.deepEqual(events, ["reset", "report"]);
});

test("controller clears stale catalog on errors and web preview without invoking inspect", async () => {
  const events: string[] = [];
  const output = { textContent: "old actionable catalog", hidden: false };
  const options = { button: { disabled: false }, output, isDesktop: true, onReset: () => events.push("reset"), onReport: () => events.push("report") };
  const failed = createManagedReviewController({ ...options, inspect: async () => ({ ok: false as const, message: "failed safely" }) });
  await failed.inspect();
  assert.equal(output.textContent, "failed safely");
  assert.deepEqual(events, ["reset"]);
  const web = createManagedReviewController({ ...options, isDesktop: false, inspect: async () => { throw new Error("must not run"); } });
  await web.inspect();
  assert.match(output.textContent, /需要 Electron/);
  assert.equal(events.length, 2);
});

test("malformed successful report falls back to safe full text and does not publish stale catalog", async () => {
  const events: string[] = [];
  const output = { textContent: "old catalog", hidden: false };
  const controller = createManagedReviewController({
    inspect: async () => ({ ok: true, report: { ...report, review_digest: "bad" } }),
    button: { disabled: false }, output, isDesktop: true,
    onReset: () => events.push("reset"), onReport: () => events.push("report"),
  });
  await controller.inspect();
  assert.deepEqual(events, ["reset"]);
  assert.equal(output.hidden, false);
  assert.match(output.textContent, /完整报告/);
  assert.match(output.textContent, /"review_digest": "bad"/);
});

test("legacy no-callback controller still serializes minimal historical packets", async () => {
  const minimal = { review_digest: "abc", history: { paths: ["old.ts"] }, effectiveDelta: { paths: ["new.ts"] } };
  const output = { textContent: "", hidden: true };
  const controller = createManagedReviewController({ inspect: async () => ({ ok: true, report: minimal }), button: { disabled: false }, output, isDesktop: true });
  await controller.inspect();
  assert.equal(output.hidden, false);
  assert.equal(output.textContent, serializeManagedReview(minimal));
});

test("throwing reset callback cannot strand the busy button or prevent safe fallback", async () => {
  let calls = 0;
  const button = { disabled: false };
  const output = { textContent: "stale", hidden: false };
  const controller = createManagedReviewController({
    inspect: async () => { calls++; return { ok: false as const, message: "safe error" }; },
    button, output, isDesktop: true, onReset: () => { throw new Error("reset failure"); },
  });
  await controller.inspect();
  assert.equal(calls, 1);
  assert.equal(button.disabled, false);
  assert.equal(output.textContent, "safe error");
});

