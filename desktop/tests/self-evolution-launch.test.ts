import assert from "node:assert/strict";
import { test } from "node:test";
import path from "node:path";
import {
  buildSelfEvolutionSidecarEnvironment,
  resolveSelfEvolutionDisposableTarget,
  validateSelfEvolutionLaunchOptIn,
} from "../electron/self-evolution-launch.ts";

const stableRoot = "F:\\my-agent-main";

test("self-evolution profile remains disabled without explicit static-AST opt-in", () => {
  assert.equal(validateSelfEvolutionLaunchOptIn({
    platform: "win32", staticAstEnabled: false, nativeApprovalEnabled: false,
    packaged: false, fixedHost: false, testHost: false,
  }), false);
});

test("static-AST opt-in selects only the one-time disposable sibling target", () => {
  assert.equal(validateSelfEvolutionLaunchOptIn({
    platform: "win32", staticAstEnabled: true, nativeApprovalEnabled: false,
    packaged: false, fixedHost: false, testHost: false,
  }), true);
  assert.equal(resolveSelfEvolutionDisposableTarget(stableRoot), "F:\\my-agent-disposable");
  assert.equal(resolveSelfEvolutionDisposableTarget(stableRoot, "f:\\MY-AGENT-DISPOSABLE"), "F:\\my-agent-disposable");
  assert.throws(() => resolveSelfEvolutionDisposableTarget(stableRoot, "F:\\other-target"), /disposable sibling/);
});

test("ordinary sidecar environment preserves inherited MY_AGENT_ROOT", () => {
  const result = buildSelfEvolutionSidecarEnvironment({ MY_AGENT_ROOT: "C:\\other-agent", KEEP: "yes" }, stableRoot);
  assert.equal(result.MY_AGENT_ROOT, "C:\\other-agent");
  assert.equal(result.KEEP, "yes");
});

test("explicit experimental profile pins MY_AGENT_ROOT and strips Electron-only opt-ins", () => {
  const result = buildSelfEvolutionSidecarEnvironment({
    MY_AGENT_ROOT: "C:\\other-agent",
    MY_AGENT_EXPERIMENTAL_SELF_EVOLUTION_STATIC_AST: "1",
    MY_AGENT_SELF_EVOLUTION_EXPERIMENT_TARGET_ROOT: "F:\\my-agent-disposable",
    MY_AGENT_SELF_EVOLUTION_NATIVE_APPROVAL: "1",
    KEEP: "yes",
  }, stableRoot, undefined, true);
  assert.equal(result.MY_AGENT_ROOT, stableRoot);
  assert.equal(result.KEEP, "yes");
  assert.equal(result.MY_AGENT_EXPERIMENTAL_SELF_EVOLUTION_STATIC_AST, undefined);
  assert.equal(result.MY_AGENT_SELF_EVOLUTION_EXPERIMENT_TARGET_ROOT, undefined);
  assert.equal(result.MY_AGENT_SELF_EVOLUTION_NATIVE_APPROVAL, undefined);
});

test("launch opt-in fails closed for packaged, fixed, test, and non-Windows hosts", () => {
  for (const override of [
    { packaged: true }, { fixedHost: true }, { testHost: true }, { platform: "linux" as NodeJS.Platform },
  ]) {
    assert.throws(() => validateSelfEvolutionLaunchOptIn({
      platform: "win32", staticAstEnabled: true, nativeApprovalEnabled: false,
      packaged: false, fixedHost: false, testHost: false, ...override,
    }));
  }
});
