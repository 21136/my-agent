import path from "node:path";
import { fileURLToPath } from "node:url";

export const MANAGED_ELECTRON_PROTOCOL = "my-agent-managed-electron-v1" as const;
export const MAX_MANAGED_ELECTRON_FRAME_BYTES = 16 * 1024;
export type ManagedHostStartupStage = "bootstrap" | "context" | "readonly" | "application" | "renderer";

export function managedHostStartupFailure(stage: ManagedHostStartupStage): {
  ready: false; error: "managed_electron_startup_failed"; stage: ManagedHostStartupStage;
} {
  return { ready: false, error: "managed_electron_startup_failed", stage };
}

export function withManagedPhysicalRuntimeReads<T>(read: () => T): T {
  const electronProcess = process as NodeJS.Process & { noAsar?: boolean };
  const previous = electronProcess.noAsar;
  electronProcess.noAsar = true;
  try { return read(); }
  finally { electronProcess.noAsar = previous; }
}

export interface ManagedElectronBootstrap {
  protocol: typeof MANAGED_ELECTRON_PROTOCOL;
  access: "read_only" | "fenced_application";
  namespace_root: string;
  stable_root: string;
  sandbox_root: string;
  operation_id: string;
  profile_policy: "managed_host_chromium_outputs_v1";
  release_id: string;
  binding_digest: string;
  runtime_digest: string;
  nonce: string;
  control_token: string;
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonical(record[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function parseManagedHostArguments(argv: string[], sealedDesktopRoot: string): { mode: "managed" } {
  if (argv.length !== 2 || argv[1] !== "--managed-host-v1" || argv[0] !== sealedDesktopRoot) {
    throw new TypeError("managed host arguments rejected");
  }
  return { mode: "managed" };
}

export function parseManagedHostBootstrap(raw: Buffer): ManagedElectronBootstrap {
  if (!Buffer.isBuffer(raw) || raw.length === 0 || raw.length > MAX_MANAGED_ELECTRON_FRAME_BYTES
      || raw[raw.length - 1] !== 0x0a || raw.subarray(0, -1).includes(0x0a) || raw.includes(0x0d)) {
    throw new TypeError("managed host bootstrap frame rejected");
  }
  let value: unknown;
  try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw.subarray(0, -1))); }
  catch { throw new TypeError("managed host bootstrap frame rejected"); }
  const keys = ["access", "binding_digest", "control_token", "namespace_root", "nonce", "operation_id", "profile_policy", "protocol", "release_id", "runtime_digest", "sandbox_root", "stable_root"];
  if (value === null || typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).sort().join("\0") !== keys.join("\0") || canonical(value) + "\n" !== raw.toString("utf8")) {
    throw new TypeError("managed host bootstrap fields rejected");
  }
  const frame = value as Record<string, unknown>;
  if (frame.protocol !== MANAGED_ELECTRON_PROTOCOL || !["read_only", "fenced_application"].includes(String(frame.access))
      || frame.profile_policy !== "managed_host_chromium_outputs_v1"
      || typeof frame.namespace_root !== "string" || !path.isAbsolute(frame.namespace_root) || path.normalize(frame.namespace_root) !== frame.namespace_root
      || typeof frame.stable_root !== "string" || !path.isAbsolute(frame.stable_root) || path.normalize(frame.stable_root) !== frame.stable_root
      || typeof frame.sandbox_root !== "string" || !path.isAbsolute(frame.sandbox_root) || path.normalize(frame.sandbox_root) !== frame.sandbox_root
      || typeof frame.operation_id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(frame.operation_id)
      || typeof frame.release_id !== "string" || !/^[a-f0-9]{64}$/.test(frame.release_id)
      || typeof frame.binding_digest !== "string" || !/^[a-f0-9]{64}$/.test(frame.binding_digest)
      || typeof frame.runtime_digest !== "string" || !/^[a-f0-9]{64}$/.test(frame.runtime_digest)
      || typeof frame.control_token !== "string" || !/^[a-f0-9]{64}$/.test(frame.control_token)
      || typeof frame.nonce !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(frame.nonce)) {
    throw new TypeError("managed host bootstrap identity rejected");
  }
  return frame as unknown as ManagedElectronBootstrap;
}

export function ownedElectronPaths(profileRoot: string): { userData: string; sessionData: string; temp: string } {
  if (!path.isAbsolute(profileRoot) || path.normalize(profileRoot) !== profileRoot) throw new TypeError("managed profile root rejected");
  return {
    userData: path.join(profileRoot, "electron-userData"),
    sessionData: path.join(profileRoot, "electron-sessionData"),
    temp: path.join(profileRoot, "tmp"),
  };
}

export function isManagedRendererRequestAllowed(rawUrl: string, rendererRoot: string, loopbackPort: number): boolean {
  let parsed: URL;
  try { parsed = new URL(rawUrl); } catch { return false; }
  if (parsed.protocol === "file:") {
    try {
      const target = path.resolve(fileURLToPath(parsed));
      const root = path.resolve(rendererRoot);
      const relative = path.relative(root, target);
      return relative !== "" && !path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(".." + path.sep);
    } catch { return false; }
  }
  if (parsed.protocol !== "ws:" || !["127.0.0.1", "[::1]", "::1"].includes(parsed.hostname)
      || parsed.port !== String(loopbackPort) || parsed.username !== "" || parsed.password !== ""
      || parsed.pathname !== "/" || parsed.search !== "" || parsed.hash !== "") return false;
  return Number.isInteger(loopbackPort) && loopbackPort >= 1 && loopbackPort <= 65535;
}

export type ManagedReadonlyIdentity = {
  nonce: string;
  host_nonce: string;
  profile_intent_digest: string;
  installation_id: string;
  data_store_id: string;
  release_id: string;
  binding_digest: string;
  runtime_digest: string;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const sameCanonical = (left: unknown, right: unknown) => canonical(left) === canonical(right);

export function managedReadonlyReadyMatches(value: unknown, identity: ManagedReadonlyIdentity): value is Record<string, unknown> {
  if (!isRecord(value) || value.ready !== true || value.mode !== "managed_host_readonly" || value.access !== "read_only"
      || value.host !== "127.0.0.1" || !Number.isInteger(value.port) || Number(value.port) < 1 || Number(value.port) > 65535
      || !Number.isSafeInteger(value.generation) || Number(value.generation) < 0
      || !Number.isSafeInteger(value.revision) || Number(value.revision) < 0
      || typeof value.lifecycle !== "string" || !Object.hasOwn(value, "last_known_good")
      || !(value.last_known_good === null || isRecord(value.last_known_good))) return false;
  return Object.entries(identity).every(([key, expected]) => value[key] === expected);
}

export function managedReadonlyHealthMatches(value: unknown, ready: Record<string, unknown>): value is Record<string, unknown> {
  if (!isRecord(value) || value.type !== "managed.health" || value.ready !== true || value.access !== "read_only") return false;
  for (const key of ["installation_id", "data_store_id", "release_id", "binding_digest", "runtime_digest",
    "nonce", "host_nonce", "profile_intent_digest", "generation", "revision", "lifecycle", "last_known_good"]) {
    if (!Object.hasOwn(value, key) || !sameCanonical(value[key], ready[key])) return false;
  }
  return true;
}

export function managedReadonlyStopMatches(value: unknown, ready: Record<string, unknown>): value is Record<string, unknown> {
  const keys = ["binding_digest", "clean", "data_store_id", "generation", "host_nonce", "installation_id",
    "last_known_good", "lifecycle", "nonce", "profile_intent_digest", "release_id", "revision", "runtime_digest", "type"];
  if (!isRecord(value) || Object.keys(value).sort().join("\0") !== keys.join("\0")
      || value.type !== "managed.host-readonly.stopped" || value.clean !== true) return false;
  for (const key of keys) {
    if (key === "clean" || key === "type") continue;
    if (!sameCanonical(value[key], ready[key])) return false;
  }
  return true;
}
