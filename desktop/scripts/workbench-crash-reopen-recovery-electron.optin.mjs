import { existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WORKBENCH_RECOVERY_ALLOWED_DATA_ROOT_ENTRIES } from "../electron/evolution/workbench-recovery-h1-data-allowlist.mjs";

const optinDir = path.dirname(fileURLToPath(import.meta.url));

export const EVOLUTION_CONTROL_RELEASE_REL = path.join("artifacts", "evolution-control-build", "control-release.js");
export const EVOLUTION_CONTROL_PREP_RUN_REL = path.join("artifacts", "evolution-control-prep");

export const WORKBENCH_RECOVERY_ELECTRON_PROFILE = "workbench-crash-reopen-recovery-v1";
export const WORKBENCH_RECOVERY_SESSION_ID = "workbench-recovery-electron";
/** Post-seed `data/` entries permitted before real Electron launch. */
/** Companion user profile materialized on first real sidecar open (not in freeze manifest). */
export const WORKBENCH_RECOVERY_RUNTIME_APP_REL_PATHS = ["evolve/user_profile.md"];

export { WORKBENCH_RECOVERY_ALLOWED_DATA_ROOT_ENTRIES } from "../electron/evolution/workbench-recovery-h1-data-allowlist.mjs";

export function deny(message) {
  throw Object.assign(new Error(message), { code: "permission_denied" });
}

export function reconcileWorkbenchRecoveryRuntimeAppWrites(appRoot) {
  if (typeof appRoot !== "string" || !path.isAbsolute(appRoot)) deny("recovery runtime reconcile requires an absolute app root");
  for (const rel of WORKBENCH_RECOVERY_RUNTIME_APP_REL_PATHS) {
    const target = path.join(appRoot, ...rel.split("/"));
    if (!existsSync(target)) continue;
    const info = lstatSync(target);
    if (info.isSymbolicLink()) deny(`recovery runtime reconcile refused a link: ${rel}`);
    rmSync(target, { force: true });
  }
}

export function requireWorkbenchRecoveryElectronOptIn(env = process.env) {
  if (env.REAL_ELECTRON_WORKBENCH_RECOVERY_OPT_IN !== "1") {
    deny("set REAL_ELECTRON_WORKBENCH_RECOVERY_OPT_IN=1 to launch the real Electron workbench crash/reopen harness");
  }
}

export function requireWorkbenchRecoveryElectronGuiOptIn(env = process.env) {
  requireWorkbenchRecoveryElectronOptIn(env);
  if (env.REAL_ELECTRON_WORKBENCH_RECOVERY_GUI_OPT_IN !== "1") {
    deny("set REAL_ELECTRON_WORKBENCH_RECOVERY_GUI_OPT_IN=1 only after local Win32 GUI review");
  }
}

export function assertWorkbenchRecoveryWin32GuiTarget(env = process.env) {
  if (env.REAL_ELECTRON_WORKBENCH_RECOVERY_GUI_OPT_IN !== "1") return;
  if (process.platform !== "win32") deny("real Electron workbench recovery GUI is Win32-only in this harness");
}

export function captureGuiWebSocketFrames(events, pythonPort) {
  const wsUrls = new Map(), sentFrames = [], receivedFrames = [];
  for (let sequence = 0; sequence < events.length; sequence++) {
    const event = events[sequence];
    const params = event.params ?? {};
    if (event.method === "Network.webSocketCreated") wsUrls.set(params.requestId, params.url);
    if (event.method === "Network.webSocketFrameSent" || event.method === "Network.webSocketFrameReceived") {
      const url = wsUrls.get(params.requestId);
      if (!url) continue;
      const target = event.method.endsWith("Sent") ? sentFrames : receivedFrames;
      target.push({ url, payload: params.response?.payloadData ?? "", websocketRequestId: params.requestId, sequence });
    }
  }
  for (const frame of [...sentFrames, ...receivedFrames]) {
    let ws;
    try { ws = new URL(frame.url); } catch { deny("renderer WebSocket URL is invalid"); }
    if (ws.hostname !== "127.0.0.1" || Number(ws.port) !== pythonPort) deny("renderer WebSocket did not use this run Python loopback port");
  }
  return { sentFrames, receivedFrames };
}

export function decodeWsPayload(frame) {
  if (typeof frame?.payload !== "string") return null;
  try { return JSON.parse(frame.payload); } catch { return null; }
}

export function countRecoveryWarns(receivedFrames) {
  return receivedFrames.filter(frame => {
    const payload = decodeWsPayload(frame);
    return payload?.type === "notice" && payload.level === "warn"
      && typeof payload.text === "string" && /未自动续跑|异常退出|无法核对/.test(payload.text);
  }).length;
}

export function sampleRecoveryNoticeFrames(receivedFrames, limit = 24) {
  return (Array.isArray(receivedFrames) ? receivedFrames : []).map((frame) => {
    const payload = decodeWsPayload(frame);
    if (payload?.type !== "notice") return null;
    return {
      level: payload.level ?? null,
      text: typeof payload.text === "string" ? payload.text.slice(0, 500) : null,
      sequence: frame.sequence ?? null,
    };
  }).filter(Boolean).slice(-limit);
}

export async function forceKillProcessTree(child) {
  if (!child?.pid) return;
  if (process.platform === "win32") {
    const { execFileSync } = await import("node:child_process");
    try { execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); } catch { /* process may already be gone */ }
    return;
  }
  try { child.kill("SIGKILL"); } catch { /* ignore */ }
}

const H1_CONVERSATION_ID_RE = /^[A-Za-z0-9._-]{1,128}$/;

export function listWorkbenchRecoveryConversationDirectories(sessionsRoot) {
  return readdirSync(sessionsRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && !entry.isSymbolicLink())
    .map((entry) => entry.name)
    .filter((name) => H1_CONVERSATION_ID_RE.test(name))
    .sort();
}

export function validateWorkbenchRecoveryDataRootEntries(dataEntries) {
  const unexpected = dataEntries.filter((name) => !WORKBENCH_RECOVERY_ALLOWED_DATA_ROOT_ENTRIES.has(name));
  const missing = ["sessions", "state.json"].filter((name) => !dataEntries.includes(name));
  return { ok: unexpected.length === 0 && missing.length === 0, unexpected, missing };
}

export function assertWorkbenchRecoverySeedLayout(appRoot, sessionId = WORKBENCH_RECOVERY_SESSION_ID) {
  const root = path.resolve(appRoot);
  const dataRoot = path.join(root, "data");
  const sessionsRoot = path.join(dataRoot, "sessions");
  const dataEntries = existsSync(dataRoot) ? readdirSync(dataRoot).sort() : [];
  const dataLayout = validateWorkbenchRecoveryDataRootEntries(dataEntries);
  if (!dataLayout.ok) {
    throw Object.assign(
      new Error(
        `workbench recovery seed data root layout invalid; `
        + `allowed: ${[...WORKBENCH_RECOVERY_ALLOWED_DATA_ROOT_ENTRIES].sort().join(", ")}; `
        + `got: ${dataEntries.join(", ") || "(missing)"}`
        + (dataLayout.unexpected.length ? `; unexpected: ${dataLayout.unexpected.join(", ")}` : "")
        + (dataLayout.missing.length ? `; missing: ${dataLayout.missing.join(", ")}` : ""),
      ),
      {
        code: "permission_denied",
        diagnostics: {
          data_root_entries: dataEntries,
          unexpected: dataLayout.unexpected,
          missing: dataLayout.missing,
          allowed: [...WORKBENCH_RECOVERY_ALLOWED_DATA_ROOT_ENTRIES].sort(),
        },
      },
    );
  }
  const sessionDirs = listWorkbenchRecoveryConversationDirectories(sessionsRoot);
  if (sessionDirs.length !== 1 || sessionDirs[0] !== sessionId) {
    deny(`workbench recovery seed must leave exactly one session directory (${sessionId}); found: ${sessionDirs.join(", ") || "(none)"}`);
  }
  const metaPath = path.join(sessionsRoot, sessionId, "meta.json");
  if (!existsSync(metaPath)) deny(`workbench recovery seed missing ${metaPath}`);
}

export function resolveDefaultElectronExecutable(repoRoot) {
  const win = path.join(repoRoot, "desktop", "node_modules", "electron", "dist", "electron.exe");
  const unix = path.join(repoRoot, "desktop", "node_modules", "electron", "dist", "electron");
  if (process.platform === "win32") return win;
  return unix;
}

function controlReleasePath(sandboxRoot) {
  return path.join(sandboxRoot, EVOLUTION_CONTROL_RELEASE_REL);
}

/** Canonical evolution sandbox for H1 freeze (prefer `.worktrees/sandbox` when invoked from stable). */
export function resolveWorkbenchRecoverySandboxRoot(invokedRoot = path.resolve(optinDir, "../..")) {
  const root = path.resolve(invokedRoot);
  const envRoot = process.env.MY_AGENT_EVOLUTION_SANDBOX_ROOT;
  if (typeof envRoot === "string" && envRoot.length > 0) return path.resolve(envRoot);

  if (existsSync(controlReleasePath(root))) return root;

  const sandboxWt = path.join(root, ".worktrees", "sandbox");
  if (existsSync(path.join(sandboxWt, "desktop", "package.json"))) {
    return path.resolve(sandboxWt);
  }

  return root;
}

export function isEvolutionControlBuildReady(sandboxRoot) {
  const modulePath = controlReleasePath(sandboxRoot);
  if (!existsSync(modulePath)) return false;
  const info = lstatSync(modulePath);
  return info.isFile() && !info.isSymbolicLink();
}

export function buildEvolutionControlPrepHint(sandboxRoot) {
  const prepRoot = path.join(sandboxRoot, EVOLUTION_CONTROL_PREP_RUN_REL);
  const desktop = path.join(sandboxRoot, "desktop");
  return [
    "H1 freeze requires artifacts/evolution-control-build/control-release.js under the evolution sandbox.",
    `Use the sandbox worktree (.worktrees/sandbox) after init-sandbox.bat, or set MY_AGENT_EVOLUTION_SANDBOX_ROOT.`,
    `Prebuild: cd ${desktop} && set EVOLUTION_TEST_ROOT=${prepRoot} && node scripts/build-evolution-control.mjs`,
    "The runner can auto-build when the prep run marker exists; tray reopen remains manual.",
  ].join(" ");
}

/** Ensure compiled control-release exists; runs build-evolution-control.mjs once when missing. */
export function ensureEvolutionControlBuild(sandboxRoot) {
  const modulePath = controlReleasePath(sandboxRoot);
  if (isEvolutionControlBuildReady(sandboxRoot)) {
    return { modulePath, built: false, sandboxRoot: path.resolve(sandboxRoot) };
  }

  const prepRoot = path.join(sandboxRoot, EVOLUTION_CONTROL_PREP_RUN_REL);
  const marker = path.join(prepRoot, ".evolution-test-run");
  if (!existsSync(prepRoot)) mkdirSync(prepRoot, { recursive: true });
  if (!existsSync(marker)) {
    writeFileSync(marker, "evolution-a\n", { flag: "wx" });
  }

  const buildScript = path.join(sandboxRoot, "desktop", "scripts", "build-evolution-control.mjs");
  if (!existsSync(buildScript)) {
    deny(buildEvolutionControlPrepHint(sandboxRoot));
  }

  const run = spawnSync(process.execPath, [buildScript], {
    cwd: path.join(sandboxRoot, "desktop"),
    env: { ...process.env, EVOLUTION_TEST_ROOT: prepRoot },
    encoding: "utf8",
    windowsHide: true,
    shell: false,
  });
  if (run.status !== 0 || !isEvolutionControlBuildReady(sandboxRoot)) {
    const detail = (run.stderr || run.stdout || "").trim().slice(0, 400);
    deny(`${buildEvolutionControlPrepHint(sandboxRoot)}${detail ? ` (build output: ${detail})` : ""}`);
  }

  return { modulePath, built: true, sandboxRoot: path.resolve(sandboxRoot) };
}
