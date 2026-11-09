import path from "node:path";

export const WORKBENCH_RECOVERY_ELECTRON_PROFILE = "workbench-crash-reopen-recovery-v1";
export const WORKBENCH_RECOVERY_SESSION_ID = "workbench-recovery-electron";

export function deny(message) {
  throw Object.assign(new Error(message), { code: "permission_denied" });
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

export async function forceKillProcessTree(child) {
  if (!child?.pid) return;
  if (process.platform === "win32") {
    const { execFileSync } = await import("node:child_process");
    try { execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); } catch { /* process may already be gone */ }
    return;
  }
  try { child.kill("SIGKILL"); } catch { /* ignore */ }
}

export function resolveDefaultElectronExecutable(repoRoot) {
  const win = path.join(repoRoot, "desktop", "node_modules", "electron", "dist", "electron.exe");
  const unix = path.join(repoRoot, "desktop", "node_modules", "electron", "dist", "electron");
  if (process.platform === "win32") return win;
  return unix;
}
