import assert from "node:assert/strict";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

test("preview lane uses sandbox desktop when worktree layout exists", async () => {
  const root = path.join(tmpdir(), `ma-sandbox-test-${Date.now()}`);
  const sandbox = path.join(root, ".worktrees", "sandbox");
  const sandboxDesktop = path.join(sandbox, "desktop");
  mkdirSync(path.join(sandbox, "agent-core"), { recursive: true });
  mkdirSync(sandboxDesktop, { recursive: true });
  writeFileSync(path.join(sandboxDesktop, "package.json"), "{}", "utf-8");

  const prev = process.env.MY_AGENT_SANDBOX_ROOT;
  process.env.MY_AGENT_SANDBOX_ROOT = sandbox;
  const { resolvePreviewLanePaths } = await import("../scripts/sandbox-paths.mjs");
  const lane = resolvePreviewLanePaths();
  if (prev === undefined) delete process.env.MY_AGENT_SANDBOX_ROOT;
  else process.env.MY_AGENT_SANDBOX_ROOT = prev;

  assert.equal(lane.active, true);
  assert.equal(lane.previewCwd, sandboxDesktop);
  rmSync(root, { recursive: true, force: true });
});
