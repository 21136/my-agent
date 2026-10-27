import { existsSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import * as path from "node:path";
import { ContractError } from "./contracts";
import { T4_RECOVERY_PROFILE } from "./test-profile";
import { isEvolutionConversationId } from "./conversation-id";

export type EvolutionIpcEvent = { senderId: number; isMainFrame: boolean; origin: string };
export interface EvolutionIpcAdapter {
  handle(channel: string, fn: (event: EvolutionIpcEvent, payload: unknown) => Promise<unknown>): void;
  removeHandler(channel: string): void;
}
export type EvolutionHostRequest =
  | { type: "evolution.begin"; conversation_id: string }
  | { type: "evolution.inspect"; conversation_id: string }
  | { type: "evolution.stop"; conversation_id: string };
type ActionChannel = "evolution:getState" | "evolution:begin" | "evolution:stop";
type HostMessage = EvolutionHostRequest;
const ACTION_CHANNELS: ActionChannel[] = ["evolution:getState", "evolution:begin", "evolution:stop"];
const SESSION_CHANNEL = "evolution:session";
const CHANNELS = [...ACTION_CHANNELS, SESSION_CHANNEL];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
function deny(message: string): never { throw new ContractError("permission_denied", message); }
function trustedEvent(event: EvolutionIpcEvent, mainWindow: { id: number }, origin: string): void {
  if (event.senderId !== mainWindow.id || event.isMainFrame !== true || event.origin !== origin) deny("evolution IPC is restricted to the trusted main frame");
}
function noRendererOptions(payload: unknown): void {
  if (payload === undefined || payload === null) return;
  if (typeof payload === "object" && !Array.isArray(payload) && Object.keys(payload as object).length === 0) return;
  deny("renderer cannot select evolution ids, paths, grants, or host options");
}
export function bindEvolutionIpc(input: {
  mainWindow: { id: number };
  ipc: EvolutionIpcAdapter;
  trustedOrigin: string;
  getConversationId: () => string | null;
  setConversationId?: (conversationId: string | null) => void;
  callHost: (message: HostMessage) => Promise<unknown>;
}): () => void {
  const dispatch = async (channel: ActionChannel, event: EvolutionIpcEvent, payload: unknown): Promise<unknown> => {
    trustedEvent(event, input.mainWindow, input.trustedOrigin);
    noRendererOptions(payload);
    const conversationId = input.getConversationId();
    if (!isEvolutionConversationId(conversationId)) deny("there is no current authenticated conversation");
    const type = channel === "evolution:begin" ? "evolution.begin" : channel === "evolution:stop" ? "evolution.stop" : "evolution.inspect";
    return await input.callHost({ type, conversation_id: conversationId });
  };
  for (const channel of ACTION_CHANNELS) input.ipc.handle(channel, (event, payload) => dispatch(channel, event, payload));
  input.ipc.handle(SESSION_CHANNEL, async (event, payload) => {
    trustedEvent(event, input.mainWindow, input.trustedOrigin);
    if (!input.setConversationId) deny("active conversation tracking is unavailable");
    if (payload !== null && !isEvolutionConversationId(payload)) deny("active conversation id is invalid");
    input.setConversationId(payload as string | null);
    return true;
  });
  let removed = false;
  return () => {
    if (removed) return;
    removed = true;
    for (const channel of CHANNELS) input.ipc.removeHandler(channel);
  };
}

export type EvolutionTestProfile = { profile_root: string; control_release_root: string; trusted_python_executable: string; ws_port: 0; renderer_port: 0; fixture_scope_id: string; candidate_fixture_profile?: typeof T4_RECOVERY_PROFILE };
function fail(message: string): never { throw new ContractError("permission_denied", message); }
function realDirectory(value: string, label: string): string {
  const info = lstatSync(value);
  if (!info.isDirectory() || info.isSymbolicLink()) fail(`${label} must be a real directory`);
  return realpathSync.native(value);
}
function under(parent: string, child: string): boolean {
  const rel = path.relative(parent, child);
  return rel !== "" && rel !== ".." && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel);
}
function markedRunRoot(target: string, artifacts: string): string {
  let current = target;
  while (under(artifacts, current)) {
    const marker = path.join(current, ".evolution-test-run");
    try {
      if (readFileSync(marker, "utf8").replace(/^\uFEFF/, "").trim() === "evolution-a") return current;
    } catch { /* keep walking toward the artifact root */ }
    current = path.dirname(current);
  }
  fail("test profile must be inside a marked evolution run root");
}
export function validateEvolutionTestProfile(value: unknown, sandboxRoot: string): EvolutionTestProfile {
  if (value === null || typeof value !== "object" || Array.isArray(value)) fail("test profile must be an object");
  const profile = value as Record<string, unknown>;
  const keys = ["profile_root", "control_release_root", "trusted_python_executable", "ws_port", "renderer_port", "fixture_scope_id"];
  const recovery = Object.hasOwn(profile, "candidate_fixture_profile");
  const acceptedKeys = recovery ? [...keys, "candidate_fixture_profile"] : keys;
  if (Object.keys(profile).length !== acceptedKeys.length || acceptedKeys.some((key) => !Object.hasOwn(profile, key))) fail("test profile fields are invalid");
  if (recovery && profile.candidate_fixture_profile !== T4_RECOVERY_PROFILE) fail("candidate fixture profile is unsupported");
  const sandbox = realDirectory(path.resolve(sandboxRoot), "sandbox root");
  const artifacts = realDirectory(path.join(sandbox, "artifacts"), "sandbox artifacts root");
  for (const key of ["profile_root", "control_release_root", "trusted_python_executable"]) {
    if (typeof profile[key] !== "string" || !path.isAbsolute(profile[key] as string)) fail(`${key} must be an absolute path`);
  }
  const profileRoot = realDirectory(path.resolve(profile.profile_root as string), "test profile root");
  const releaseRoot = realDirectory(path.resolve(profile.control_release_root as string), "control release root");
  if (!under(artifacts, profileRoot) || !under(artifacts, releaseRoot)) fail("test profile and control release must remain under sandbox/artifacts");
  if (markedRunRoot(profileRoot, artifacts) !== markedRunRoot(releaseRoot, artifacts)) fail("test profile and release must share one marked run root");
  if (under(profileRoot, releaseRoot) || under(releaseRoot, profileRoot) || profileRoot === releaseRoot) fail("test profile and control release must be disjoint");
  if (profile.ws_port !== 0 || profile.renderer_port !== 0) fail("test host must use dynamic port zero");
  if (typeof profile.fixture_scope_id !== "string" || !UUID.test(profile.fixture_scope_id)) fail("fixture_scope_id must be a canonical UUID");
  const executable = path.resolve(profile.trusted_python_executable as string);
  const info = lstatSync(executable);
  if (!info.isFile() || info.isSymbolicLink()) fail("trusted Python executable must be a regular file");
  if (!existsSync(path.join(releaseRoot, "manifest.json"))) fail("control release manifest is missing");
  return { profile_root: profileRoot, control_release_root: releaseRoot, trusted_python_executable: realpathSync.native(executable), ws_port: 0, renderer_port: 0, fixture_scope_id: profile.fixture_scope_id as string, ...(recovery ? { candidate_fixture_profile: T4_RECOVERY_PROFILE } : {}) };
}

