import assert from "node:assert/strict";
import { test } from "node:test";
import { createManagedReviewHandler, resolveManagedReviewRoots } from "../electron/managed-review-bridge.ts";

const report = { format: 1, mode: "managed_source_review_v1", review_digest: "d".repeat(64), history: { paths: ["old.ts"] }, effectiveDelta: { basis: "merge_base_to_sandbox_head", paths: ["net.ts"] }, sandbox: { inventory: { entries: [{ path: "local.ts", state: "raw_changed" }] } }, blockers: [], execution: false, readyForMerge: false, readyForLaunch: false };
const roots = { stableRoot: "C:\\work\\project", sandboxRoot: "C:\\work\\project\\.worktrees\\sandbox" };

test("rejects untrusted sender before inspector runs", async () => {
  let calls = 0;
  const handler = createManagedReviewHandler({ isTrusted: () => false, hasArgs: () => false, resolveRoots: async () => roots, inspect: async () => { calls++; return report; } });
  assert.deepEqual(await handler({}), { ok: false, message: "仅允许可信工作台主页面查看源码审查报告。" });
  assert.equal(calls, 0);
});

test("rejects any renderer arguments", async () => {
  let calls = 0;
  const handler = createManagedReviewHandler({ isTrusted: () => true, hasArgs: (args) => args.length > 0, resolveRoots: async () => roots, inspect: async () => { calls++; return report; } });
  assert.deepEqual(await handler("C:/attacker", { stableRoot: "x" }), { ok: false, message: "审查接口不接受渲染器参数。" });
  assert.equal(calls, 0);
});

test("fixture context is unsupported and never calls resolver or inspector", async () => {
  let calls = 0;
  const handler = createManagedReviewHandler({ isTrusted: () => true, hasArgs: () => false, isFixture: true, resolveRoots: async () => { calls++; return roots; }, inspect: async () => { calls++; return report; } });
  assert.deepEqual(await handler(), { ok: false, message: "固定或测试 Host 不支持管理源码审查。" });
  assert.equal(calls, 0);
});

test("derives only the fixed sandbox sibling and rejects ambiguous layouts by inspector identity", async () => {
  const stable = await resolveManagedReviewRoots("C:/work/project", async () => ({ stable: { identity: { worktreeRoot: "C:/work/project" } }, sandbox: { identity: { worktreeRoot: "C:/work/project/.worktrees/sandbox" } } }));
  assert.equal(stable.stableRoot.replaceAll("\\", "/"), roots.stableRoot.replaceAll("\\", "/")); assert.equal(stable.sandboxRoot.replaceAll("\\", "/"), roots.sandboxRoot.replaceAll("\\", "/"));
  const sandbox = await resolveManagedReviewRoots("C:/work/project/.worktrees/sandbox", async () => ({ stable: { identity: { worktreeRoot: "C:/work/project" } }, sandbox: { identity: { worktreeRoot: "C:/work/project/.worktrees/sandbox" } } }));
  assert.equal(sandbox.stableRoot.replaceAll("\\", "/"), roots.stableRoot.replaceAll("\\", "/")); assert.equal(sandbox.sandboxRoot.replaceAll("\\", "/"), roots.sandboxRoot.replaceAll("\\", "/"));
  await assert.rejects(resolveManagedReviewRoots("C:/other/repo", async () => ({ stable: { identity: { worktreeRoot: "C:/different" } }, sandbox: { identity: { worktreeRoot: "C:/different/.worktrees/sandbox" } } })), /identity mismatch/);
});

test("sanitizes inspector failures", async () => {
  const handler = createManagedReviewHandler({ isTrusted: () => true, hasArgs: () => false, resolveRoots: async () => roots, inspect: async () => { throw new Error("C:/secret/config token=abc"); } });
  const result = await handler();
  assert.deepEqual(result, { ok: false, message: "源码审查暂不可用，请检查受支持的工作区布局。" });
  assert.doesNotMatch(result.message, /secret|token|C:\\?/);
});



test("bridge busy gate prevents overlapping inspector calls", async () => {
  let release!: (value: typeof report) => void; let calls = 0;
  const handler = createManagedReviewHandler({ isTrusted: () => true, hasArgs: () => false, resolveRoots: async () => roots, inspect: () => { calls++; return new Promise((resolve) => { release = resolve; }); } });
  const pending = handler();
  assert.deepEqual(await handler(), { ok: false, message: "源码审查正在进行，请稍后重试。" });
  assert.equal(calls, 1); release(report); await pending;
});
