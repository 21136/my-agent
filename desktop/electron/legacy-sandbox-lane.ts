import { lstatSync, realpathSync } from "node:fs";
import path from "node:path";
import type { ChildProcess } from "node:child_process";

export type LegacyLaneInfo = { host: string; port: number };
export type LegacyLaneResult = { ok: boolean; message?: string; host?: string; port?: number };
type LaneChild = Pick<ChildProcess, "stdout" | "stderr" | "on" | "kill">;
type OwnedPrimary = { root: string; owned: boolean; running: boolean; info: LegacyLaneInfo | null };

function samePath(left: string, right: string): boolean {
  const key = (value: string) => process.platform === "win32" ? path.resolve(value).toLowerCase() : path.resolve(value);
  return key(left) === key(right);
}

/** Same conventional layout as managed-review-bridge; ambient roots cannot select code. */
export function resolveRegisteredSandboxRoot(agentRoot: string): string | null {
  const root = path.resolve(agentRoot);
  const isSandbox = path.basename(root).toLowerCase() === "sandbox"
    && path.basename(path.dirname(root)).toLowerCase() === ".worktrees";
  const sandbox = isSandbox ? root : path.join(root, ".worktrees", "sandbox");
  try {
    const directory = lstatSync(sandbox);
    if (!directory.isDirectory() || directory.isSymbolicLink() || !samePath(realpathSync(sandbox), sandbox)) return null;
    const isSourceFile = (relative: string) => {
      const full = path.join(sandbox, relative), entry = lstatSync(full, { throwIfNoEntry: false });
      return entry?.isFile() && !entry.isSymbolicLink() && samePath(realpathSync(full), full);
    };
    if (!isSourceFile("agent-core/server.py") || !isSourceFile("desktop/package.json")
        || !(isSourceFile("evolve/_index.core.toml") || isSourceFile("evolve/_index.toml"))) return null;
    return sandbox;
  } catch { return null; }
}

function validInfo(value: unknown): value is LegacyLaneInfo {
  if (!value || typeof value !== "object") return false;
  const info = value as LegacyLaneInfo;
  return info.host === "127.0.0.1" && Number.isInteger(info.port) && info.port > 0 && info.port <= 65535;
}

/** Only the child returned by spawn belongs to this controller. It never edits a lock. */
export function createLegacySandboxLane(options: {
  agentRoot: string;
  allowed: () => boolean;
  primary?: () => OwnedPrimary | null;
  spawn: (root: string) => LaneChild;
  readyTimeoutMs: number;
  onStderr?: (chunk: Buffer) => void;
}) {
  type ChildState = { child: LaneChild; root: string; info: LegacyLaneInfo | null; closing: boolean; cancel: () => void };
  let active: ChildState | null = null;
  let pending: Promise<LegacyLaneResult> | null = null;
  const disabled = (): LegacyLaneResult => ({ ok: false, message: "legacy sandbox lane is disabled for this Host" });
  function primary(root: string): LegacyLaneInfo | null {
    const current = options.primary?.();
    return current?.owned && current.running && samePath(current.root, root) && validInfo(current.info)
      ? { ...current.info } : null;
  }
  function get(): LegacyLaneInfo | null {
    if (!options.allowed()) return null;
    const root = resolveRegisteredSandboxRoot(options.agentRoot);
    if (!root) return null;
    if (active) return !active.closing && samePath(active.root, root) && active.info ? { ...active.info } : null;
    return primary(root);
  }
  function ensure(): Promise<LegacyLaneResult> {
    if (!options.allowed()) return Promise.resolve(disabled());
    const root = resolveRegisteredSandboxRoot(options.agentRoot);
    if (!root) return Promise.resolve({ ok: false, message: "sandbox worktree not ready in registered layout" });
    if (active) {
      if (active.closing) return Promise.resolve({ ok: false, message: "sandbox child termination is not confirmed" });
      if (active.info) return Promise.resolve({ ok: true, ...active.info });
      if (pending) return pending;
    }
    const ownedPrimary = primary(root);
    if (ownedPrimary) return Promise.resolve({ ok: true, ...ownedPrimary });

    let child: LaneChild;
    try { child = options.spawn(root); }
    catch (error) { return Promise.reject(error); }
    const state: ChildState = { child, root, info: null, closing: false, cancel: () => {} };
    active = state;
    const launch = new Promise<LegacyLaneResult>((resolve, reject) => {
      let settled = false, output = "";
      const timer = setTimeout(() => fail(new Error("sandbox sidecar ready timeout")), options.readyTimeoutMs);
      function terminate(): void {
        state.info = null;
        if (state.closing) return;
        state.closing = true;
        // Keep the record fenced until exit/close. A successful kill request is not exit proof.
        try { child.kill(); } catch { /* uncertain termination stays fenced */ }
      }
      function fail(error: Error): void {
        if (!settled) { settled = true; clearTimeout(timer); reject(error); }
        terminate();
      }
      function blocked(message: string): void {
        if (!settled) { settled = true; clearTimeout(timer); resolve({ ok: false, message }); }
        terminate();
      }
      state.cancel = () => blocked("sandbox sidecar stopped");
      child.on("error", fail);
      const exited = (code: number | null) => {
        state.info = null;
        if (active === state) active = null;
        if (!settled) {
          settled = true; clearTimeout(timer);
          if (code === 2) resolve({ ok: false, message: "sandbox interface lock held by another UI" });
          else reject(new Error(`sandbox sidecar exited before ready (code ${code})`));
        }
      };
      child.on("exit", exited);
      child.on("close", exited);
      child.stderr?.on("data", (chunk: Buffer) => options.onStderr?.(chunk));
      child.stdout?.on("data", (chunk: Buffer) => {
        if (settled || active !== state) return;
        output += chunk.toString("utf8");
        if (Buffer.byteLength(output, "utf8") > 65536) { fail(new Error("sandbox sidecar ready output limit")); return; }
        for (;;) {
          const newline = output.indexOf("\n");
          if (newline < 0) return;
          const line = output.slice(0, newline).trim(); output = output.slice(newline + 1);
          let frame: Record<string, unknown>;
          try { frame = JSON.parse(line); } catch { continue; }
          if (!frame || typeof frame !== "object" || Array.isArray(frame)) continue;
          if (frame.error === "lock_conflict") { blocked("sandbox interface lock held by another UI"); return; }
          if (frame.ready !== true) continue;
          if (!validInfo(frame)) { fail(new Error("invalid sandbox sidecar ready endpoint")); return; }
          state.info = { host: frame.host, port: frame.port };
          settled = true; clearTimeout(timer); resolve({ ok: true, ...state.info }); return;
        }
      });
    });
    const published = launch.finally(() => { if (pending === published) pending = null; });
    pending = published;
    return published;
  }
  return { get, ensure, stop: () => active?.cancel() };
}

/** Caller validation always precedes endpoint/root access and is repeated after startup. */
export function createTrustedLegacyLaneHandlers<Event>(options: {
  isTrusted: (event: Event) => boolean;
  allowed: () => boolean;
  get: () => LegacyLaneInfo | null;
  ensure: () => Promise<LegacyLaneResult>;
}) {
  return {
    capabilities: (event: Event): { legacySessionLanes: boolean } => ({
      legacySessionLanes: options.allowed() && options.isTrusted(event),
    }),
    get: (event: Event): LegacyLaneInfo | null => options.allowed() && options.isTrusted(event) ? options.get() : null,
    ensure: async (event: Event): Promise<LegacyLaneResult> => {
      if (!options.allowed() || !options.isTrusted(event)) return { ok: false, message: "workbench unavailable" };
      const result = await options.ensure();
      if (!options.allowed() || !options.isTrusted(event)) return { ok: false, message: "workbench unavailable" };
      return result;
    },
  };
}
