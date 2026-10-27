import { createHash } from "node:crypto";
import { existsSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import * as path from "node:path";
import { ContractError, canonicalJson, parseJsonBytes, type Hash } from "./contracts";
import { verifyControlRelease, type ControlRelease } from "./control-release";
import type { EvolutionSupervisorHostConfig } from "./supervisor-host";
import { T4_RECOVERY_DESCRIPTOR_ARTIFACT_ID, T4_RECOVERY_OWNED_PATHS, T4_RECOVERY_PROFILE, T4_RECOVERY_PROFILE_FIXTURE_ID, T4_RECOVERY_RUNTIME_ID, validateEvolutionTestProfile, type EvolutionTestProfile } from "./test-profile";

const RUN_MARKER = ".evolution-test-run";
const RUN_MARKER_VALUE = "evolution-a";
const PROFILE_FILE = "evolution-test-profile.json";
export const EVOLUTION_TEST_OWNED_PATHS = ["evolve/tool-catalog/INDEX.md"] as const;
export type EvolutionTestHostContext = {
  app_root: string;
  sandbox_root: string;
  run_root: string;
  profile_file: string;
  profile: EvolutionTestProfile;
};
type ManifestRuntime = { path: string; sha256: string };
type ReleaseManifest = { format: 1; files: unknown[]; runtimes: { node: ManifestRuntime; python: ManifestRuntime } };

function fail(message: string): never { throw new ContractError("permission_denied", message); }
function inside(root: string, target: string): boolean {
  const relative = path.relative(root, target);
  return relative !== "" && relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}
function realDirectory(value: string, label: string): string {
  if (!path.isAbsolute(value)) fail(`${label} must be absolute`);
  const info = lstatSync(value);
  if (!info.isDirectory() || info.isSymbolicLink()) fail(`${label} must be a real directory`);
  return realpathSync.native(value);
}
function exactObject(value: unknown, fields: string[], label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) fail(`${label} must be an object`);
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== fields.length || fields.some((field) => !Object.hasOwn(item, field))) fail(`${label} fields are missing or unknown`);
  return item;
}
function samePath(left: string, right: string): boolean {
  const a = path.resolve(left);
  const b = path.resolve(right);
  return process.platform === "win32" ? a.toLowerCase() === b.toLowerCase() : a === b;
}
function fileAt(file: string, label: string): string {
  const info = lstatSync(file);
  if (!info.isFile() || info.isSymbolicLink()) fail(`${label} must be a regular non-link file`);
  const real = realpathSync.native(file);
  if (!samePath(real, file)) fail(`${label} resolved through a link`);
  return real;
}
function sandboxForRunRoot(runRoot: string): string | null {
  let current = path.dirname(runRoot);
  while (current !== path.dirname(current)) {
    if (path.basename(current).toLowerCase() === "artifacts") {
      const artifacts = realDirectory(current, "sandbox artifacts root");
      if (!inside(artifacts, runRoot)) return null;
      const sandbox = realDirectory(path.dirname(artifacts), "sandbox root");
      if (!samePath(path.join(sandbox, "artifacts"), artifacts)) return null;
      return sandbox;
    }
    current = path.dirname(current);
  }
  return null;
}
function findMarkedRunRoot(appRoot: string): { runRoot: string; sandboxRoot: string } | null {
  let current = appRoot;
  while (current !== path.dirname(current)) {
    const marker = path.join(current, RUN_MARKER);
    if (existsSync(marker)) {
      fileAt(marker, "evolution test run marker");
      if (readFileSync(marker, "utf8").replace(/^\uFEFF/, "").trim() !== RUN_MARKER_VALUE) fail("evolution test run marker is invalid");
      const sandboxRoot = sandboxForRunRoot(current);
      if (!sandboxRoot) return null;
      return { runRoot: current, sandboxRoot };
    }
    current = path.dirname(current);
  }
  return null;
}
function parseReleaseManifest(value: unknown): ReleaseManifest {
  const manifest = exactObject(value, ["format", "files", "runtimes"], "fixed control release manifest");
  if (manifest.format !== 1 || !Array.isArray(manifest.files)) fail("fixed control release manifest is invalid");
  const runtimes = exactObject(manifest.runtimes, ["node", "python"], "fixed control runtime map");
  const runtime = (value: unknown, label: string): ManifestRuntime => {
    const item = exactObject(value, ["path", "sha256"], `${label} runtime entry`);
    if (typeof item.path !== "string" || !path.isAbsolute(item.path) || typeof item.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(item.sha256)) fail(`${label} runtime entry is invalid`);
    return { path: item.path, sha256: item.sha256 };
  };
  return { format: 1, files: manifest.files, runtimes: { node: runtime(runtimes.node, "Node"), python: runtime(runtimes.python, "Python") } };
}

/** Resolve test-only Host configuration from the sealed H1 layout; ordinary app roots return null. */
export function loadEvolutionTestHostProfile(appRootValue: string): EvolutionTestHostContext | null {
  if (typeof appRootValue !== "string" || !path.isAbsolute(appRootValue)) fail("H1 app root must be absolute");
  const appRoot = realDirectory(path.resolve(appRootValue), "H1 app root");
  const marked = findMarkedRunRoot(appRoot);
  if (!marked) return null;
  const expectedAppRoot = path.join(marked.runRoot, "host", "app");
  if (!samePath(appRoot, expectedAppRoot)) fail("marked test runs may start only the sealed H1 host app root");
  const hostRoot = realDirectory(path.join(marked.runRoot, "host"), "H1 host fixture root");
  if (!samePath(hostRoot, path.join(marked.runRoot, "host"))) fail("H1 host fixture root resolved unexpectedly");
  const profileFile = fileAt(path.join(hostRoot, PROFILE_FILE), "H1 evolution test profile");
  const bytes = readFileSync(profileFile);
  const rawProfile = parseJsonBytes(bytes);
  const profile = validateEvolutionTestProfile(rawProfile, marked.sandboxRoot);
  const expectedProfileRoot = path.join(hostRoot, "profile");
  if (!samePath(profile.profile_root, expectedProfileRoot)) fail("H1 test profile root must use the isolated host/profile directory");
  if (!inside(marked.runRoot, profile.control_release_root)) fail("fixed control release escaped the marked run root");
  return { app_root: appRoot, sandbox_root: marked.sandboxRoot, run_root: marked.runRoot, profile_file: profileFile, profile };
}

export async function buildEvolutionTestSupervisorConfig(
  context: EvolutionTestHostContext,
  options: { verifyControlRelease?: (release: ControlRelease) => Promise<void> } = {},
): Promise<EvolutionSupervisorHostConfig> {
  const recoveryFixture = context.profile.candidate_fixture_profile === T4_RECOVERY_PROFILE; const ownedPaths = recoveryFixture ? [...EVOLUTION_TEST_OWNED_PATHS, ...T4_RECOVERY_OWNED_PATHS] : [...EVOLUTION_TEST_OWNED_PATHS];
  for (const ownedPath of ownedPaths) fileAt(path.join(context.app_root, ...ownedPath.split("/")), "fixed H1 owned source file");
  const releaseRoot = realDirectory(context.profile.control_release_root, "fixed control release root");
  const manifestPath = fileAt(path.join(releaseRoot, "manifest.json"), "fixed control release manifest");
  const bytes = readFileSync(manifestPath);
  const manifest = parseReleaseManifest(parseJsonBytes(bytes));
  if (Buffer.from(`${canonicalJson(manifest)}\n`, "utf8").compare(bytes) !== 0) fail("fixed control release manifest is not canonical");
  if (!samePath(manifest.runtimes.python.path, context.profile.trusted_python_executable)) fail("test profile Python differs from the fixed control release");
  const digest: Hash = `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
  const release: ControlRelease = {
    release_id: digest,
    root: releaseRoot,
    node_executable: manifest.runtimes.node.path,
    python_executable: manifest.runtimes.python.path,
    manifest_path: manifestPath,
    manifest_digest: digest,
  };
  await (options.verifyControlRelease ?? verifyControlRelease)(release);
  return {
    protocol: "my-agent-evolution-supervisor/1.0",
    sandbox_root: context.sandbox_root,
    sidecar_root: context.app_root,
    run_root: context.run_root,
    control_release: release,
    scopes: [{
      scope_id: context.profile.fixture_scope_id,
      source_root: context.app_root,
      owned_paths: ownedPaths,
      state_root: path.join(context.run_root, "workspace-state"),
      read_only_roots: [],
    }],
    runtime_paths: recoveryFixture ? { [T4_RECOVERY_RUNTIME_ID]: fileAt(manifest.runtimes.node.path, "fixed T4 Node runtime") } : {},
    descriptors: [],
    ...(recoveryFixture ? { descriptor_factories: [{ artifact_id: T4_RECOVERY_DESCRIPTOR_ARTIFACT_ID, kind: T4_RECOVERY_PROFILE, runtime_id: T4_RECOVERY_RUNTIME_ID, profile_fixture_id: T4_RECOVERY_PROFILE_FIXTURE_ID }] } : {}),
    profile_fixtures: recoveryFixture ? [{ id: T4_RECOVERY_PROFILE_FIXTURE_ID, root: path.join(context.run_root, "candidate-profile") }] : [],
  };
}

