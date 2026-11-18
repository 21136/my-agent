import path from "node:path";

export type SelfEvolutionLaunchOptIn = {
  platform: NodeJS.Platform;
  staticAstEnabled: boolean;
  nativeApprovalEnabled: boolean;
  packaged: boolean;
  fixedHost: boolean;
  testHost: boolean;
};

/** Keep the experimental Electron profile fail-closed and separate from release/fixed hosts. */
export function validateSelfEvolutionLaunchOptIn(input: SelfEvolutionLaunchOptIn): boolean {
  if (input.nativeApprovalEnabled && !input.staticAstEnabled) {
    throw new TypeError("native selected-apply approval requires the explicit static-AST experimental profile");
  }
  if (!input.staticAstEnabled) return false;
  if (input.platform !== "win32") throw new TypeError("experimental self-evolution is Windows-only");
  if (input.packaged || input.fixedHost || input.testHost) {
    throw new TypeError("experimental self-evolution is unavailable in packaged, fixed, or test hosts");
  }
  return true;
}

/** Resolve the single disposable target as a sibling, never from inherited/configured ambient roots. */
export function resolveSelfEvolutionDisposableTarget(sourceRoot: string, configuredTarget?: string): string {
  if (!path.isAbsolute(sourceRoot)) throw new TypeError("self-evolution source root must be absolute");
  const expected = path.resolve(path.dirname(sourceRoot), "my-agent-disposable");
  if (configuredTarget !== undefined && path.resolve(configuredTarget).toLowerCase() !== expected.toLowerCase()) {
    throw new TypeError("self-evolution target must be the one-time disposable sibling of the stable root");
  }
  return expected;
}

/** Preserve inherited root by default; pin it only for explicit experimental startup. */
export function buildSelfEvolutionSidecarEnvironment(
  base: NodeJS.ProcessEnv,
  sidecarRoot: string,
  nativeApproval?: { url: string; token: string },
  pinAgentRootForExperimentalProfile = false,
): NodeJS.ProcessEnv {
  if (!path.isAbsolute(sidecarRoot)) throw new TypeError("sidecar root must be absolute");
  const env: NodeJS.ProcessEnv = {
    ...base,
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };
  if (pinAgentRootForExperimentalProfile) env.MY_AGENT_ROOT = path.resolve(sidecarRoot);
  delete env.MY_AGENT_EXPERIMENTAL_SELF_EVOLUTION_STATIC_AST;
  delete env.MY_AGENT_SELF_EVOLUTION_NATIVE_APPROVAL;
  delete env.MY_AGENT_SELF_EVOLUTION_EXPERIMENT_TARGET_ROOT;
  delete env.MY_AGENT_SELF_EVOLUTION_ENROLL_TRUST_ROOT;
  delete env.MY_AGENT_SELF_EVOLUTION_NATIVE_URL;
  delete env.MY_AGENT_SELF_EVOLUTION_NATIVE_TOKEN;
  if (nativeApproval) {
    if (!/^http:\/\/127\.0\.0\.1:\d+\/self-evolution\/confirm$/.test(nativeApproval.url)
      || !/^[a-f0-9]{64}$/.test(nativeApproval.token)) throw new TypeError("native approval child environment values are invalid");
    env.MY_AGENT_SELF_EVOLUTION_NATIVE_URL = nativeApproval.url;
    env.MY_AGENT_SELF_EVOLUTION_NATIVE_TOKEN = nativeApproval.token;
  }
  return env;
}
