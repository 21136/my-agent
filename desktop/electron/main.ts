import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import os from "node:os";
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  app,
  BrowserWindow,
  WebContentsView,
  clipboard,
  dialog,
  globalShortcut,
  ipcMain,
  Menu,
  nativeImage,
  shell,
  Tray,
  type IpcMainInvokeEvent,
} from "electron";
import { promoteCandidateWithRestart } from "./candidate-promotion";
import { SidecarSupervisor } from "./sidecar-supervisor";
import { buildBilibiliSearchUrl, isOfficialBilibiliUrl } from "./bilibili-search";
import { readAgentImageDataUrl } from "./agent-file-preview";
import { clampMusicViewBounds, type MusicViewBounds } from "./music-view-bounds";
import {
  configuredShellDevPreviewBase,
  defaultShellDevPreviewBase,
  defaultShellMainDevBase,
  isAllowedShellDevPreviewNavigation,
  isAllowedShellRendererPromotion,
  loopbackUrlVariants,
  normalizeShellDevPreviewBase,
} from "./preview-url-policy";
import { shellDevEmbedZoom } from "./shell-dev-embed-zoom";
import {
  getSandboxAdoptStatus,
  prepareAndMergeSandboxIntoStable,
  syncSandboxWorktreeWithStable,
  type SandboxAdoptPrepareOptions,
} from "./sandbox-adopt";
import { loadAppIcon } from "./app-icon";

type ShellDevOpenResult = {
  ok: boolean;
  url?: string;
  configuredUrl?: string;
  fallbackToMain?: boolean;
  message?: string;
};

async function isDevServerReachable(url: string): Promise<boolean> {
  const normalized = normalizeShellDevPreviewBase(url);
  if (!normalized) return false;
  const lib = normalized.startsWith("https:") ? https : http;
  return new Promise((resolve) => {
    const req = lib.get(
      normalized,
      { timeout: 2500, headers: { Accept: "text/html" } },
      (res) => {
        res.resume();
        resolve((res.statusCode ?? 0) < 500);
      },
    );
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.on("error", () => resolve(false));
  });
}

async function resolveReachableDevServerUrl(preferred: string): Promise<string | null> {
  for (const candidate of loopbackUrlVariants(preferred)) {
    if (await isDevServerReachable(candidate)) return candidate;
  }
  return null;
}

async function pickShellPreviewLoadUrl(): Promise<{ url: string; fallback: boolean; configured: string }> {
  const configured = configuredShellDevPreviewBase();
  const main = defaultShellMainDevBase();
  const previewUrl = await resolveReachableDevServerUrl(configured);
  if (previewUrl) {
    return { url: previewUrl, fallback: false, configured };
  }
  if (configured !== main) {
    const mainUrl = await resolveReachableDevServerUrl(main);
    if (mainUrl) {
      return { url: mainUrl, fallback: true, configured };
    }
  }
  return { url: configured, fallback: false, configured };
}

async function loadShellPreviewUrl(view: WebContentsView, url: string): Promise<boolean> {
  try {
    await view.webContents.loadURL(url);
    return !view.webContents.isDestroyed();
  } catch {
    return false;
  }
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const AGENT_ROOT = path.resolve(__dirname, "../..");

/** Dev: keep Chromium/session cache on local disk (USB/removable roots cause lock storms). */
const LOCAL_DEV_ROOT = path.join(
  process.env.LOCALAPPDATA || process.env.TEMP || os.tmpdir(),
  "my-agent-desktop",
);
if (!app.isPackaged) {
  const userData = path.join(LOCAL_DEV_ROOT, "userData");
  mkdirSync(userData, { recursive: true });
  app.setPath("userData", userData);
  app.setPath("sessionData", path.join(LOCAL_DEV_ROOT, "session-data"));
  app.commandLine.appendSwitch(
    "disk-cache-dir",
    path.join(LOCAL_DEV_ROOT, "disk-cache"),
  );
}

const STATE_PATH = path.join(AGENT_ROOT, "data", "state.json");
const CONSTELLATION_PATH = path.join(AGENT_ROOT, "data", "constellation.json");
const SHOW_SHORTCUT = "CommandOrControl+Shift+M";
const DEFAULT_WS_PORT = Number(process.env.MY_AGENT_WS_PORT ?? "8765") || 8765;
const SANDBOX_WS_PORT = Number(process.env.MY_AGENT_SANDBOX_WS_PORT ?? "8766") || 8766;
/** Cold Python import on first launch can exceed 30s on Windows. */
const SIDECAR_READY_TIMEOUT_MS =
  Number(process.env.MY_AGENT_SIDECAR_READY_MS ?? "90000") || 90000;
const PORT_PROBE_TIMEOUT_MS = Number(process.env.MY_AGENT_PORT_PROBE_MS ?? "2500") || 2500;
const PORT_WAIT_MS = Number(process.env.MY_AGENT_PORT_WAIT_MS ?? "20000") || 20000;
/** Dev only: vite.config.ts treats this as user quit and shuts down `npm run dev`. */
const DEV_USER_QUIT_CODE = 100;

type LockHolder = { ui: string; pid: number; since: string };

type SidecarStartResult =
  | { ok: true; host: string; port: number }
  | { ok: false; lockConflict?: LockHolder; message?: string };

let workbenchWindow: BrowserWindow | null = null;
let bilibiliSearchView: WebContentsView | null = null;
let shellDevPreviewView: WebContentsView | null = null;
let shellDevPopoutWindow: BrowserWindow | null = null;
let shellDevPreviewBaseUrl = defaultShellDevPreviewBase();
let sidecar: ChildProcess | null = null;
let sidecarInfo: { host: string; port: number } | null = null;
let tray: Tray | null = null;
let quitting = false;
let quitInProgress = false;

let sidecarRestarting = false;
let sidecarOwned = false;
let activeSidecarRoot = AGENT_ROOT;
let sandboxSidecar: ChildProcess | null = null;
let sandboxSidecarInfo: { host: string; port: number } | null = null;
let sandboxSidecarOwned = false;
const sidecarSupervisor = new SidecarSupervisor();

function resolveSandboxRootPath(): string | null {
  const raw = process.env.MY_AGENT_SANDBOX_ROOT?.trim();
  const sandboxRoot = raw ? path.resolve(raw) : path.join(AGENT_ROOT, ".worktrees", "sandbox");
  const markers = [
    path.join(sandboxRoot, "evolve", "_index.core.toml"),
    path.join(sandboxRoot, "evolve", "_index.toml"),
  ];
  if (!existsSync(sandboxRoot) || !markers.some((marker) => existsSync(marker))) {
    return null;
  }
  return sandboxRoot;
}

function sandboxSidecarSpawnEnv(sandboxRoot: string): NodeJS.ProcessEnv {
  return {
    ...sidecarSpawnEnv(),
    MY_AGENT_SANDBOX_ROOT: sandboxRoot,
  };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isPortOpen(host: string, port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect({ host, port });
    const finish = (open: boolean) => {
      socket.destroy();
      resolve(open);
    };
    socket.setTimeout(PORT_PROBE_TIMEOUT_MS);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
  });
}

async function waitForPortOpen(
  host: string,
  port: number,
  deadlineMs = PORT_WAIT_MS,
): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < deadlineMs) {
    if (await isPortOpen(host, port)) {
      return true;
    }
    await sleep(500);
  }
  return false;
}

async function waitForPortClosed(
  host: string,
  port: number,
  deadlineMs = PORT_WAIT_MS,
): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < deadlineMs) {
    if (!(await isPortOpen(host, port))) {
      return true;
    }
    await sleep(100);
  }
  return !(await isPortOpen(host, port));
}

// Windows dev: reduce native GPU crashes (exit code 0xC0000005) when tray + Chromium init.
if (process.platform === "win32" && process.env.VITE_DEV_SERVER_URL) {
  app.disableHardwareAcceleration();
}

function pythonCommand(): string {
  if (process.platform === "win32") {
    const localPython = path.join(AGENT_ROOT, ".venv", "Scripts", "python.exe");
    return existsSync(localPython) ? localPython : "python";
  }
  const localPython = path.join(AGENT_ROOT, ".venv", "bin", "python");
  return existsSync(localPython) ? localPython : "python3";
}

/** DOC-08 / T-1824-03: force UTF-8 on sidecar pipes (Electron always decodes stdout as utf-8). */
function sidecarSpawnEnv(): NodeJS.ProcessEnv {
  return {
    ...process.env,
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };
}

function finalizeAppExit(): void {
  if (process.env.VITE_DEV_SERVER_URL) {
    app.exit(DEV_USER_QUIT_CODE);
    return;
  }
  app.quit();
}

function atomicWriteJson(filePath: string, payload: unknown): void {
  mkdirSync(path.dirname(filePath), { recursive: true });
  const tmpPath = `${filePath}.tmp`;
  writeFileSync(tmpPath, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
  renameSync(tmpPath, filePath);
}

function readConstellationFile(): { version: 1; stars: unknown[]; links: unknown[] } {
  const empty = { version: 1 as const, stars: [], links: [] };
  if (!existsSync(CONSTELLATION_PATH)) {
    return empty;
  }
  try {
    const loaded = JSON.parse(readFileSync(CONSTELLATION_PATH, "utf-8")) as {
      version?: number;
      stars?: unknown[];
      links?: unknown[];
    };
    if (!loaded || typeof loaded !== "object") {
      return empty;
    }
    return {
      version: 1,
      stars: Array.isArray(loaded.stars) ? loaded.stars : [],
      links: Array.isArray(loaded.links) ? loaded.links : [],
    };
  } catch {
    return empty;
  }
}

function setPreferredUi(ui: "electron" | "cli"): void {
  let payload: Record<string, unknown> = {};
  if (existsSync(STATE_PATH)) {
    try {
      const loaded = JSON.parse(readFileSync(STATE_PATH, "utf-8")) as Record<string, unknown>;
      if (loaded && typeof loaded === "object") {
        payload = loaded;
      }
    } catch {
      payload = {};
    }
  }
  payload.preferred_ui = ui;
  writeFileSync(STATE_PATH, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
}

function trayIcon() {
  return loadAppIcon(32);
}

function buildTray(): void {
  if (tray) return;
  tray = new Tray(trayIcon());
  tray.setToolTip("my-agent");
  const menu = Menu.buildFromTemplate([
    { label: "打开工作台", click: () => void openWorkbenchWindow() },
    { type: "separator" },
    { label: "改用终端 (CLI)", click: () => void switchToCli() },
    { type: "separator" },
    {
      label: "退出",
      click: () => {
        void requestAppQuit();
      },
    },
  ]);
  tray.setContextMenu(menu);
  tray.on("double-click", () => void openWorkbenchWindow());
}

function lockUiLabel(ui: string): string {
  if (ui === "cli") return "CLI REPL";
  if (ui === "terminal") return "Terminal 狂野模式";
  return "Electron 桌面";
}

function startSidecar(root: string = activeSidecarRoot): Promise<SidecarStartResult> {
  return new Promise((resolve, reject) => {
    const script = path.join(root, "agent-core", "server.py");
    const args = [script, "--port", String(DEFAULT_WS_PORT)];
    sidecar = spawn(pythonCommand(), args, {
      cwd: root,
      stdio: ["ignore", "pipe", "pipe"],
      env: sidecarSpawnEnv(),
    });
    const proc = sidecar;
    const settledRef = { value: false };

    let settled = false;
    const timeout = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(new Error("sidecar ready timeout"));
      }
    }, SIDECAR_READY_TIMEOUT_MS);

    const handlePayload = (payload: Record<string, unknown>) => {
      if (payload.error === "lock_conflict") {
        if (!settled) {
          settled = true;
          settledRef.value = true;
          clearTimeout(timeout);
          const lock = payload.lock;
          if (lock && typeof lock === "object") {
            resolve({ ok: false, lockConflict: lock as LockHolder });
          } else {
            resolve({
              ok: false,
              message: typeof payload.message === "string" ? payload.message : "lock_conflict",
            });
          }
        }
        return;
      }
      if (payload.ready === true && typeof payload.port === "number") {
        if (!settled) {
          settled = true;
          settledRef.value = true;
          clearTimeout(timeout);
          sidecarInfo = {
            host: typeof payload.host === "string" ? payload.host : "127.0.0.1",
            port: payload.port,
          };
          sidecarOwned = true;
          resolve({ ok: true, ...sidecarInfo });
        }
      }
    };

    proc.stdout?.on("data", (chunk: Buffer) => {
      const line = chunk.toString("utf-8").trim();
      for (const part of line.split("\n")) {
        if (!part.trim()) continue;
        try {
          handlePayload(JSON.parse(part) as Record<string, unknown>);
        } catch {
          // ignore non-JSON stdout
        }
      }
    });

    proc.stderr?.on("data", (chunk: Buffer) => {
      console.error("[sidecar]", chunk.toString("utf-8"));
    });

    proc.on("error", (err) => {
      if (!settled) {
        settled = true;
        settledRef.value = true;
        clearTimeout(timeout);
        reject(err);
      }
    });

    proc.on("exit", (code) => {
      const wasReady = sidecarInfo !== null;
      sidecar = null;
      sidecarInfo = null;

      if (!settled) {
        settled = true;
        settledRef.value = true;
        clearTimeout(timeout);
        if (code === 2) {
          resolve({ ok: false, message: "interface lock held by another UI" });
        } else {
          reject(new Error(`sidecar exited before ready (code ${code})`));
        }
        return;
      }

      if (!quitting && wasReady) {
        void restartSidecarAfterCrash();
      }
    });
  });
}

type CandidateSidecar = {
  proc: ChildProcess;
  info: { host: string; port: number };
};

function stopCandidateSidecar(candidate: CandidateSidecar, root: string): void {
  if (!candidate.proc.killed) {
    try {
      candidate.proc.kill();
    } catch {
      // best effort
    }
  }
  const lockPath = path.join(root, "data", "sessions", ".interface.lock");
  if (existsSync(lockPath)) {
    try {
      unlinkSync(lockPath);
    } catch {
      // best effort
    }
  }
}

function startCandidateSidecar(root: string, port: number): Promise<CandidateSidecar> {
  return new Promise((resolve, reject) => {
    const script = path.join(root, "agent-core", "server.py");
    const proc = spawn(pythonCommand(), [script, "--port", String(port)], {
      cwd: root,
      stdio: ["ignore", "pipe", "pipe"],
      env: sidecarSpawnEnv(),
    });
    let settled = false;
    const timeout = setTimeout(() => {
      if (settled) return;
      settled = true;
      try { proc.kill(); } catch { /* best effort */ }
      reject(new Error("candidate sidecar ready timeout"));
    }, SIDECAR_READY_TIMEOUT_MS);
    const onPayload = (payload: Record<string, unknown>) => {
      if (settled) return;
      if (payload.error === "lock_conflict") {
        settled = true;
        clearTimeout(timeout);
        try { proc.kill(); } catch { /* best effort */ }
        reject(new Error("candidate sidecar interface lock conflict"));
        return;
      }
      if (payload.ready === true && typeof payload.port === "number") {
        settled = true;
        clearTimeout(timeout);
        resolve({
          proc,
          info: {
            host: typeof payload.host === "string" ? payload.host : "127.0.0.1",
            port: payload.port,
          },
        });
      }
    };
    proc.stdout?.on("data", (chunk: Buffer) => {
      for (const line of chunk.toString("utf-8").split("\n")) {
        if (!line.trim()) continue;
        try { onPayload(JSON.parse(line) as Record<string, unknown>); } catch { /* ignore non-json */ }
      }
    });
    proc.stderr?.on("data", (chunk: Buffer) => console.error("[candidate-sidecar]", chunk.toString("utf-8")));
    proc.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(error);
    });
    proc.on("exit", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(new Error(`candidate sidecar exited before ready (code ${code})`));
    });
  });
}

async function switchSidecarCandidate(rawRoot: unknown): Promise<Record<string, unknown>> {
  if (typeof rawRoot !== "string" || !rawRoot.trim()) {
    return { ok: false, state: "blocked", recovered: false, error: "candidate root is required" };
  }
  const candidateRoot = path.resolve(rawRoot);
  const allowedRoot = path.resolve(AGENT_ROOT);
  const relative = path.relative(allowedRoot, candidateRoot);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    return { ok: false, state: "blocked", recovered: false, error: "candidate root must be under the Agent root" };
  }
  if (!sidecarOwned || !sidecarInfo) {
    return { ok: false, state: "blocked", recovered: false, error: "current Electron sidecar is not owned" };
  }
  let candidate: CandidateSidecar | null = null;
  const result = await sidecarSupervisor.switchCandidate({
    startCandidate: async () => {
      candidate = await startCandidateSidecar(candidateRoot, 0);
      return { ok: true, ...candidate.info };
    },
    healthCandidate: async (info) => {
      return await waitForPortOpen(info.host, info.port, PORT_WAIT_MS);
    },
    stopCandidate: async () => {
      if (candidate) stopCandidateSidecar(candidate, candidateRoot);
    },
    stopCurrent: async () => {
      const currentPort = sidecarInfo?.port;
      stopSidecar();
      if (currentPort) {
        await waitForPortClosed("127.0.0.1", currentPort, PORT_WAIT_MS);
      }
    },
    promoteCandidate: async () => {
      if (!candidate) {
        return { ok: false, message: "candidate process is missing" };
      }
      return promoteCandidateWithRestart({
        candidateRoot,
        candidate,
        stopCandidate: async (readyCandidate, root) => {
          stopCandidateSidecar(readyCandidate, root);
        },
        startPromoted: async (root) => {
          activeSidecarRoot = root;
          return startSidecar(root);
        },
      });
    },
    recoverCurrent: async () => {
      activeSidecarRoot = AGENT_ROOT;
      return await startSidecar(AGENT_ROOT);
    },
  });
  return { ...result, candidateRoot };
}

async function ensureSidecar(): Promise<void> {
  if (!sidecarOwned && (await waitForPortOpen("127.0.0.1", DEFAULT_WS_PORT))) {
    console.log(`[sidecar] reusing listener on 127.0.0.1:${DEFAULT_WS_PORT}`);
    sidecarInfo = { host: "127.0.0.1", port: DEFAULT_WS_PORT };
    setPreferredUi("electron");
    return;
  }

  let result = await startSidecar();
  if (!result.ok && result.lockConflict) {
    const uiLabel = lockUiLabel(result.lockConflict.ui);
    await dialog.showMessageBox({
      type: "warning",
      title: "会话被占用",
      message: `${uiLabel} 正在占用会话 (pid ${result.lockConflict.pid})`,
      detail: "请先关闭占用中的界面后再启动桌面（不支持接管会话 · TM-9）。",
      buttons: ["退出"],
      defaultId: 0,
      cancelId: 0,
    });
    quitting = true;
    finalizeAppExit();
    return;
  }
  if (!result.ok) {
    throw new Error(result.message ?? "Failed to start Python sidecar");
  }
  setPreferredUi("electron");
}

function stopSidecar(): void {
  if (sidecar && !sidecar.killed) {
    try {
      sidecar.kill();
    } catch {
      // process may already be gone
    }
  }
  // On Windows, ChildProcess.kill() uses TerminateProcess which skips Python's
  // atexit handlers, so the .interface.lock file is never cleaned up.  Remove it
  // here so the next launch doesn't see a stale "session occupied" lock.
  const lockPath = path.join(AGENT_ROOT, "data", "sessions", ".interface.lock");
  if (existsSync(lockPath)) {
    try {
      unlinkSync(lockPath);
    } catch {
      // best-effort
    }
  }
  sidecar = null;
  sidecarInfo = null;
  sidecarOwned = false;
}

function stopSandboxSidecar(): void {
  const root = resolveSandboxRootPath();
  if (sandboxSidecar && !sandboxSidecar.killed) {
    try {
      sandboxSidecar.kill();
    } catch {
      // process may already be gone
    }
  }
  if (root) {
    const lockPath = path.join(root, "data", "sessions", ".interface.lock");
    if (existsSync(lockPath)) {
      try {
        unlinkSync(lockPath);
      } catch {
        // best-effort
      }
    }
  }
  sandboxSidecar = null;
  sandboxSidecarInfo = null;
  sandboxSidecarOwned = false;
}

function startSandboxSidecar(root: string): Promise<SidecarStartResult> {
  return new Promise((resolve, reject) => {
    const script = path.join(root, "agent-core", "server.py");
    const args = [script, "--port", String(SANDBOX_WS_PORT)];
    sandboxSidecar = spawn(pythonCommand(), args, {
      cwd: root,
      stdio: ["ignore", "pipe", "pipe"],
      env: sandboxSidecarSpawnEnv(root),
    });
    const proc = sandboxSidecar;
    let settled = false;
    const timeout = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(new Error("sandbox sidecar ready timeout"));
      }
    }, SIDECAR_READY_TIMEOUT_MS);

    const handlePayload = (payload: Record<string, unknown>) => {
      if (payload.error === "lock_conflict") {
        if (!settled) {
          settled = true;
          clearTimeout(timeout);
          const lock = payload.lock;
          if (lock && typeof lock === "object") {
            resolve({ ok: false, lockConflict: lock as LockHolder });
          } else {
            resolve({
              ok: false,
              message: typeof payload.message === "string" ? payload.message : "lock_conflict",
            });
          }
        }
        return;
      }
      if (payload.ready === true && typeof payload.port === "number") {
        if (!settled) {
          settled = true;
          clearTimeout(timeout);
          sandboxSidecarInfo = {
            host: typeof payload.host === "string" ? payload.host : "127.0.0.1",
            port: payload.port,
          };
          sandboxSidecarOwned = true;
          resolve({ ok: true, ...sandboxSidecarInfo });
        }
      }
    };

    proc.stdout?.on("data", (chunk: Buffer) => {
      const line = chunk.toString("utf-8").trim();
      for (const part of line.split("\n")) {
        if (!part.trim()) continue;
        try {
          handlePayload(JSON.parse(part) as Record<string, unknown>);
        } catch {
          // ignore non-JSON stdout
        }
      }
    });

    proc.stderr?.on("data", (chunk: Buffer) => {
      console.error("[sandbox-sidecar]", chunk.toString("utf-8"));
    });

    proc.on("error", (err) => {
      if (!settled) {
        settled = true;
        clearTimeout(timeout);
        reject(err);
      }
    });

    proc.on("exit", (code) => {
      sandboxSidecar = null;
      sandboxSidecarInfo = null;
      sandboxSidecarOwned = false;
      if (!settled) {
        settled = true;
        clearTimeout(timeout);
        if (code === 2) {
          resolve({ ok: false, message: "sandbox interface lock held by another UI" });
        } else {
          reject(new Error(`sandbox sidecar exited before ready (code ${code})`));
        }
      }
    });
  });
}

async function ensureSandboxSidecar(): Promise<SidecarStartResult> {
  const root = resolveSandboxRootPath();
  if (!root) {
    sandboxSidecarInfo = null;
    return { ok: false, message: "sandbox worktree not ready" };
  }
  if (
    !sandboxSidecarOwned
    && sandboxSidecarInfo === null
    && (await waitForPortOpen("127.0.0.1", SANDBOX_WS_PORT))
  ) {
    console.log(`[sandbox-sidecar] reusing listener on 127.0.0.1:${SANDBOX_WS_PORT}`);
    sandboxSidecarInfo = { host: "127.0.0.1", port: SANDBOX_WS_PORT };
    return { ok: true, ...sandboxSidecarInfo };
  }
  if (sandboxSidecarInfo && sandboxSidecar && !sandboxSidecar.killed) {
    return { ok: true, ...sandboxSidecarInfo };
  }
  return startSandboxSidecar(root);
}

async function isAgentBusy(): Promise<boolean> {
  if (!workbenchWindow || workbenchWindow.isDestroyed()) {
    return false;
  }
  return workbenchWindow.webContents
    .executeJavaScript("Boolean(window.__myAgentIsBusy?.())", true)
    .catch(() => false);
}

async function requestAppQuit(): Promise<void> {
  if (quitting || quitInProgress) {
    return;
  }
  quitInProgress = true;
  try {
    const busy = await isAgentBusy();
    if (busy) {
      const parent =
        workbenchWindow && !workbenchWindow.isDestroyed() ? workbenchWindow : undefined;
      const { response } = await dialog.showMessageBox({
        type: "warning",
        title: "my-agent",
        message: "助手仍在执行任务",
        detail: "现在退出将中断当前轮次，未完成的工具调用和回复可能丢失。",
        buttons: ["仍要退出", "继续等待"],
        defaultId: 1,
        cancelId: 1,
        ...(parent ? { parent } : {}),
      });
      if (response !== 0) {
        return;
      }
    }
    await performAppQuit();
  } finally {
    quitInProgress = false;
  }
}

async function performAppQuit(): Promise<void> {
  if (quitting) {
    return;
  }
  quitting = true;
  globalShortcut.unregisterAll();
  if (tray) {
    tray.destroy();
    tray = null;
  }
  stopSidecar();
  if (workbenchWindow && !workbenchWindow.isDestroyed()) {
    workbenchWindow.removeAllListeners("close");
    workbenchWindow.close();
  }
  workbenchWindow = null;
  finalizeAppExit();
}

async function restartSidecarAfterCrash(): Promise<void> {
  if (quitting || sidecarRestarting) {
    return;
  }
  sidecarRestarting = true;
  try {
    console.error("[sidecar] session backend stopped unexpectedly; restarting…");
    const result = await sidecarSupervisor.restart({
      stop: stopSidecar,
      start: async () => {
        await ensureSidecar();
        return sidecarInfo
          ? { ok: true, ...sidecarInfo }
          : { ok: false, message: "sidecar did not become ready" };
      },
    });
    if (!result.ok) {
      throw new Error(result.error ?? "sidecar restart failed");
    }
  } catch (err) {
    console.error("[sidecar] restart failed:", err);
    await dialog.showMessageBox({
      type: "error",
      title: "my-agent",
      message: "Python 会话后端已退出",
      detail: err instanceof Error ? err.message : String(err),
    });
  } finally {
    sidecarRestarting = false;
  }
}

function preloadPath(): string {
  const js = path.join(__dirname, "preload.js");
  const mjs = path.join(__dirname, "preload.mjs");
  if (existsSync(js)) return js;
  if (existsSync(mjs)) return mjs;
  return js;
}

function windowBackground(): string {
  return "#F6F3ED";
}

function applyApplicationMenu(): void {
  if (process.platform === "darwin") {
    Menu.setApplicationMenu(
      Menu.buildFromTemplate([
        {
          label: app.name,
          submenu: [{ role: "quit", label: `退出 ${app.name}` }],
        },
      ]),
    );
    return;
  }
  Menu.setApplicationMenu(null);
}

function sendSessionControl(win: BrowserWindow | null, action: "suspend" | "resume"): void {
  if (!win || win.isDestroyed()) return;
  win.webContents.send("session:control", action);
}

function readPersistedShellRendererUrl(): string | null {
  if (!existsSync(STATE_PATH)) return null;
  try {
    const loaded = JSON.parse(readFileSync(STATE_PATH, "utf-8")) as Record<string, unknown>;
    const raw = typeof loaded.desktop_shell_renderer_url === "string" ? loaded.desktop_shell_renderer_url : "";
    const normalized = normalizeShellDevPreviewBase(raw);
    if (!normalized || !isAllowedShellRendererPromotion(normalized)) return null;
    return normalized;
  } catch {
    return null;
  }
}

function readStateJson(): Record<string, unknown> {
  if (!existsSync(STATE_PATH)) return {};
  try {
    const loaded = JSON.parse(readFileSync(STATE_PATH, "utf-8")) as Record<string, unknown>;
    return loaded && typeof loaded === "object" ? loaded : {};
  } catch {
    return {};
  }
}

function writeStateJson(payload: Record<string, unknown>): void {
  writeFileSync(STATE_PATH, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
}

function persistShellRendererUrl(url: string): void {
  const normalized = normalizeShellDevPreviewBase(url);
  if (!normalized || !isAllowedShellRendererPromotion(normalized)) return;
  const payload = readStateJson();
  payload.desktop_shell_renderer_url = normalized;
  writeStateJson(payload);
}

/** 「采用预览为主壳」会写入 data/state.json；合入/同步后应恢复 5173 主轨。 */
function clearPersistedShellRendererUrl(): boolean {
  const payload = readStateJson();
  if (!("desktop_shell_renderer_url" in payload)) return false;
  delete payload.desktop_shell_renderer_url;
  writeStateJson(payload);
  return true;
}

async function reloadWorkbenchMainLaneStable(): Promise<void> {
  if (!workbenchWindow || workbenchWindow.isDestroyed()) return;
  const main = defaultShellMainDevBase();
  await workbenchWindow.webContents.loadURL(main).catch(() => {});
}

function resolveShellMainDevUrl(): string {
  if (process.env.VITE_DEV_SERVER_URL) {
    return readPersistedShellRendererUrl() || defaultShellMainDevBase();
  }
  return "";
}

async function loadRenderer(win: BrowserWindow): Promise<void> {
  if (process.env.VITE_DEV_SERVER_URL) {
    await win.loadURL(resolveShellMainDevUrl() || defaultShellMainDevBase());
    return;
  }
  await win.loadFile(path.join(__dirname, "../dist", "index.html"));
}

function isAppOwnedUrl(url: string): boolean {
  const text = url.trim();
  if (!text || text === "about:blank") return true;
  if (text.startsWith("file:")) return true;
  const dev = process.env.VITE_DEV_SERVER_URL;
  if (dev && text.startsWith(dev)) return true;
  return false;
}

/** Chat markdown links must not navigate the workbench (white-screen / ERR_CONNECTION_REFUSED). */
function wireExternalNavigation(win: BrowserWindow): void {
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (!isAppOwnedUrl(url)) {
      void shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (isAppOwnedUrl(url)) return;
    event.preventDefault();
    void shell.openExternal(url);
  });
}


function wireShellDevPreviewNavigation(view: WebContentsView, baseUrl: string): void {
  const base = normalizeShellDevPreviewBase(baseUrl) || defaultShellDevPreviewBase();
  shellDevPreviewBaseUrl = base;
  view.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedShellDevPreviewNavigation(url, base)) {
      void view.webContents.loadURL(url).catch((error) => console.warn("[shell-dev] navigation failed:", error));
    } else if (/^https?:\/\//i.test(url)) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });
  const guard = (event: Electron.Event, url: string): void => {
    if (isAllowedShellDevPreviewNavigation(url, base)) return;
    event.preventDefault();
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
  };
  view.webContents.on("will-navigate", guard);
  view.webContents.on("will-redirect", guard);
}

function wireBilibiliViewNavigation(view: WebContentsView): void {
  view.webContents.setWindowOpenHandler(({ url }) => {
    if (isOfficialBilibiliUrl(url)) {
      void view.webContents.loadURL(url).catch((error) => console.warn("[music] Bilibili navigation failed:", error));
    } else if (/^https?:\/\//i.test(url)) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });
  const keepOfficialPage = (event: Electron.Event, url: string): void => {
    if (isOfficialBilibiliUrl(url)) return;
    event.preventDefault();
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
  };
  view.webContents.on("will-navigate", keepOfficialPage);
  view.webContents.on("will-redirect", keepOfficialPage);
}

function boundedMusicViewRect(raw: unknown): MusicViewBounds | null {
  if (!workbenchWindow || workbenchWindow.isDestroyed()) return null;
  const [width, height] = workbenchWindow.getContentSize();
  return clampMusicViewBounds(raw, { width, height });
}

async function createWorkbenchWindow(): Promise<void> {
  if (workbenchWindow && !workbenchWindow.isDestroyed()) {
    return;
  }

  const windowIcon = loadAppIcon(256);
  workbenchWindow = new BrowserWindow({
    width: 960,
    height: 720,
    minWidth: 640,
    minHeight: 480,
    title: "my-agent",
    autoHideMenuBar: true,
    backgroundColor: windowBackground(),
    icon: windowIcon.isEmpty() ? undefined : windowIcon,
    webPreferences: {
      preload: preloadPath(),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  workbenchWindow.on("close", (event) => {
    if (quitting) return;
    event.preventDefault();
    sendSessionControl(workbenchWindow, "suspend");
    workbenchWindow?.hide();
    // Phase 34: hide to tray; do not bounce back to pet as default entry
  });

  await loadRenderer(workbenchWindow);
  wireExternalNavigation(workbenchWindow);

  workbenchWindow.on("closed", () => {
    const bilibili = bilibiliSearchView;
    bilibiliSearchView = null;
    if (bilibili && !bilibili.webContents.isDestroyed()) bilibili.webContents.close();
    const shellDev = shellDevPreviewView;
    shellDevPreviewView = null;
    if (shellDev && !shellDev.webContents.isDestroyed()) shellDev.webContents.close();
    const popout = shellDevPopoutWindow;
    shellDevPopoutWindow = null;
    if (popout && !popout.isDestroyed()) popout.close();
    workbenchWindow = null;
  });
}

async function openWorkbenchWindow(): Promise<void> {
  try {
    if (!sidecar || sidecar.killed || !sidecarInfo) {
      await ensureSidecar();
    }
    let workbenchNeedsResume = false;
    if (workbenchWindow && !workbenchWindow.isDestroyed() && !workbenchWindow.isVisible()) {
      workbenchNeedsResume = true;
    }
    await createWorkbenchWindow();
    if (workbenchNeedsResume) {
      sendSessionControl(workbenchWindow, "resume");
    }
    workbenchWindow?.show();
    workbenchWindow?.focus();
  } catch (err) {
    await dialog.showMessageBox({
      type: "error",
      title: "my-agent",
      message: "无法打开工作台",
      detail: err instanceof Error ? err.message : String(err),
    });
  }
}

async function switchToCli(): Promise<void> {
  const { response } = await dialog.showMessageBox({
    type: "question",
    title: "改用终端",
    message: "切换到终端 REPL？",
    detail: "桌面将释放会话锁并缩到托盘；请在终端中继续对话。",
    buttons: ["切换", "取消"],
    defaultId: 0,
    cancelId: 1,
  });
  if (response !== 0) {
    return;
  }

  setPreferredUi("cli");
  stopSidecar();

  const startBat = path.join(AGENT_ROOT, "start.bat");
  if (process.platform === "win32") {
    spawn("cmd.exe", ["/c", "start", "cmd", "/k", startBat], {
      cwd: AGENT_ROOT,
      detached: true,
      stdio: "ignore",
    }).unref();
  } else {
    const term = process.env.SHELL || "/bin/bash";
    spawn(term, ["-lc", `cd "${AGENT_ROOT}" && python3 agent-core/main.py`], {
      detached: true,
      stdio: "ignore",
    }).unref();
  }

  workbenchWindow?.hide();
}

function registerShortcuts(): void {
  if (!globalShortcut.register(SHOW_SHORTCUT, () => void openWorkbenchWindow())) {
    console.warn(`Failed to register shortcut ${SHOW_SHORTCUT}`);
  }
}

ipcMain.handle("sidecar:get", () => sidecarInfo);
ipcMain.handle("sidecar:get-sandbox", () => sandboxSidecarInfo);
ipcMain.handle("sidecar:ensure-sandbox", () => ensureSandboxSidecar());
ipcMain.handle("sidecar:switch-candidate", (_event, rawRoot: unknown) => switchSidecarCandidate(rawRoot));
ipcMain.handle("app:switch-to-cli", () => switchToCli());

ipcMain.handle("music:open-bilibili-search", async (event, rawQuery: unknown, rawBounds: unknown) => {
  if (!workbenchWindow || workbenchWindow.isDestroyed() || event.sender !== workbenchWindow.webContents) return false;
  const url = buildBilibiliSearchUrl(rawQuery);
  const bounds = boundedMusicViewRect(rawBounds);
  if (!url || !bounds) return false;

  let view = bilibiliSearchView;
  if (!view || view.webContents.isDestroyed()) {
    view = new WebContentsView({
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        partition: "persist:bilibili-search",
      },
    });
    bilibiliSearchView = view;
    workbenchWindow.contentView.addChildView(view);
    wireBilibiliViewNavigation(view);
  }
  view.setBounds(bounds);
  view.setVisible(true);
  try {
    await view.webContents.loadURL(url);
    return !view.webContents.isDestroyed();
  } catch (error) {
    console.warn("[music] Bilibili search page failed to load:", error);
    view.setVisible(false);
    return false;
  }
});

ipcMain.handle("shell-dev:get-preview-url", (event) => {
  if (!workbenchWindow || workbenchWindow.isDestroyed() || event.sender !== workbenchWindow.webContents) {
    return configuredShellDevPreviewBase();
  }
  return configuredShellDevPreviewBase();
});

ipcMain.handle("shell-dev:get-main-url", (event) => {
  if (!workbenchWindow || workbenchWindow.isDestroyed() || event.sender !== workbenchWindow.webContents) {
    return defaultShellMainDevBase();
  }
  const live = normalizeShellDevPreviewBase(workbenchWindow.webContents.getURL());
  return live || resolveShellMainDevUrl() || defaultShellMainDevBase();
});

function assertShellDevWorkbenchSender(event: IpcMainInvokeEvent): boolean {
  return Boolean(workbenchWindow && !workbenchWindow.isDestroyed() && event.sender === workbenchWindow.webContents);
}

ipcMain.handle("shell-dev:sandbox-adopt-status", async (event) => {
  if (!assertShellDevWorkbenchSender(event)) {
    return { ok: false, message: "workbench unavailable" };
  }
  return getSandboxAdoptStatus();
});

function showSandboxAdoptResultDialog(result: {
  ok: boolean;
  message: string;
  detail?: string;
  steps?: string[];
}): void {
  const parent = workbenchWindow && !workbenchWindow.isDestroyed() ? workbenchWindow : null;
  const detail = [result.steps?.join(" → "), result.detail].filter(Boolean).join("\n\n");
  const box = {
    type: result.ok ? "info" : "error",
    title: result.ok ? "实验分支已合入" : "合入未完成",
    message: result.message,
    detail: detail || undefined,
    buttons: ["知道了"],
    defaultId: 0,
  } as Electron.MessageBoxOptions;
  if (parent) void dialog.showMessageBox(parent, box);
  else void dialog.showMessageBox(box);
}

ipcMain.handle("shell-dev:reset-main-lane", async (event) => {
  if (!assertShellDevWorkbenchSender(event)) {
    return { ok: false, message: "workbench unavailable" };
  }
  const cleared = clearPersistedShellRendererUrl();
  await reloadWorkbenchMainLaneStable();
  return {
    ok: true,
    message: cleared
      ? "已清除记住的预览主壳地址，主窗口已指向稳定 5173。"
      : "主窗口已指向稳定 5173（无需清除 state）。",
    mainUrl: defaultShellMainDevBase(),
  };
});

ipcMain.handle("shell-dev:sync-sandbox", async (event) => {
  if (!assertShellDevWorkbenchSender(event)) {
    return { ok: false, message: "workbench unavailable" };
  }
  const result = await syncSandboxWorktreeWithStable();
  if (result.ok) {
    clearPersistedShellRendererUrl();
    await reloadWorkbenchMainLaneStable();
  }
  showSandboxAdoptResultDialog(result);
  return result;
});

ipcMain.handle("shell-dev:merge-sandbox", async (event, rawOptions: unknown) => {
  if (!assertShellDevWorkbenchSender(event)) {
    const blocked = { ok: false, message: "workbench unavailable" };
    showSandboxAdoptResultDialog(blocked);
    return blocked;
  }
  const options = (rawOptions && typeof rawOptions === "object" ? rawOptions : {}) as SandboxAdoptPrepareOptions;
  const result = await prepareAndMergeSandboxIntoStable(options);
  if (result.ok) {
    clearPersistedShellRendererUrl();
    await reloadWorkbenchMainLaneStable();
  }
  showSandboxAdoptResultDialog(result);
  return result;
});

ipcMain.handle("shell-dev:promote-preview", async (event) => {
  if (!assertShellDevWorkbenchSender(event)) {
    return { ok: false, message: "workbench unavailable" };
  }
  const win = workbenchWindow;
  if (!win || win.isDestroyed()) {
    return { ok: false, message: "workbench unavailable" };
  }
  const previewUrl = normalizeShellDevPreviewBase(shellDevPreviewBaseUrl || defaultShellDevPreviewBase());
  if (!previewUrl || !isAllowedShellRendererPromotion(previewUrl)) {
    return { ok: false, message: "预览地址无效" };
  }
  const previousMain =
    normalizeShellDevPreviewBase(win.webContents.getURL()) || defaultShellMainDevBase();
  try {
    persistShellRendererUrl(previewUrl);
    await win.webContents.loadURL(previewUrl);
    const view = shellDevPreviewView;
    if (view && !view.webContents.isDestroyed() && previousMain !== previewUrl) {
      shellDevPreviewBaseUrl = previousMain;
      wireShellDevPreviewNavigation(view, previousMain);
      await view.webContents.loadURL(previousMain).catch(() => {});
    }
    return { ok: true, mainUrl: previewUrl, previewUrl: previousMain };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { ok: false, message };
  }
});

function applyShellDevPreviewViewBounds(view: WebContentsView, bounds: MusicViewBounds): void {
  view.setBounds(bounds);
  if (!workbenchWindow || workbenchWindow.isDestroyed() || view.webContents.isDestroyed()) return;
  const hostZoom = workbenchWindow.webContents.getZoomFactor() || 1;
  const fit = shellDevEmbedZoom(bounds.width, bounds.height);
  view.webContents.setZoomFactor(hostZoom * fit);
}

async function ensureShellDevPreviewView(bounds: MusicViewBounds): Promise<WebContentsView | null> {
  if (!workbenchWindow || workbenchWindow.isDestroyed()) return null;
  let view = shellDevPreviewView;
  if (!view || view.webContents.isDestroyed()) {
    view = new WebContentsView({
      webPreferences: {
        preload: preloadPath(),
        contextIsolation: true,
        nodeIntegration: false,
        partition: "persist:shell-dev-preview",
      },
    });
    shellDevPreviewView = view;
    wireShellDevPreviewNavigation(view, defaultShellDevPreviewBase());
  }
  const bilibili = bilibiliSearchView;
  if (bilibili && !bilibili.webContents.isDestroyed()) bilibili.setVisible(false);
  workbenchWindow.contentView.addChildView(view);
  applyShellDevPreviewViewBounds(view, bounds);
  view.setVisible(true);
  return view;
}

ipcMain.handle("shell-dev:open-preview", async (event, rawBounds: unknown): Promise<ShellDevOpenResult> => {
  if (!workbenchWindow || workbenchWindow.isDestroyed() || event.sender !== workbenchWindow.webContents) {
    return { ok: false, message: "工作台不可用" };
  }
  const bounds = boundedMusicViewRect(rawBounds);
  if (!bounds) {
    return { ok: false, message: "预览区域尺寸无效" };
  }
  let pick = await pickShellPreviewLoadUrl();
  shellDevPreviewBaseUrl = pick.url;
  const view = await ensureShellDevPreviewView(bounds);
  if (!view) return { ok: false, message: "无法创建预览视图" };
  wireShellDevPreviewNavigation(view, pick.url);
  let loaded = await loadShellPreviewUrl(view, pick.url);
  const mainDev = defaultShellMainDevBase();
  if (!loaded && pick.url !== mainDev && await isDevServerReachable(mainDev)) {
    loaded = await loadShellPreviewUrl(view, mainDev);
    if (loaded) {
      pick = { url: mainDev, fallback: true, configured: pick.configured };
      shellDevPreviewBaseUrl = mainDev;
      wireShellDevPreviewNavigation(view, mainDev);
    }
  }
  if (!loaded) {
    view.setVisible(false);
    const hint = await isDevServerReachable(mainDev)
      ? `开发服在 ${mainDev}，但嵌入预览失败。下方将尝试页内预览；或点「重新加载预览」。`
      : "无法连接开发服。请用 start-desktop.bat 启动（默认含 5173+5174）；或单独 npm run dev:preview。";
    return {
      ok: false,
      url: pick.url,
      configuredUrl: pick.configured,
      message: hint,
    };
  }
  return {
    ok: true,
    url: pick.url,
    configuredUrl: pick.configured,
    fallbackToMain: pick.fallback,
    message: pick.fallback
      ? `未检测到 ${pick.configured}，已临时嵌入主壳 ${pick.url}。请重启 start-desktop.bat（默认会起 5174）。`
      : undefined,
  };
});

ipcMain.handle("shell-dev:open-popout", async (event): Promise<ShellDevOpenResult> => {
  if (!workbenchWindow || workbenchWindow.isDestroyed() || event.sender !== workbenchWindow.webContents) {
    return { ok: false, message: "工作台不可用" };
  }
  const pick = await pickShellPreviewLoadUrl();
  const reachable = await resolveReachableDevServerUrl(pick.url);
  if (!reachable) {
    return {
      ok: false,
      configuredUrl: pick.configured,
      message: "预览开发服未就绪。请用 start-desktop.bat 启动（默认含 5174）。",
    };
  }
  let win = shellDevPopoutWindow;
  if (!win || win.isDestroyed()) {
    win = new BrowserWindow({
      width: 1000,
      height: 760,
      minWidth: 720,
      minHeight: 540,
      title: "壳预览 · my-agent",
      autoHideMenuBar: true,
      backgroundColor: windowBackground(),
      icon: (() => {
        const popoutIcon = loadAppIcon(256);
        return popoutIcon.isEmpty() ? undefined : popoutIcon;
      })(),
      webPreferences: {
        preload: preloadPath(),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    shellDevPopoutWindow = win;
    wireExternalNavigation(win);
    win.on("closed", () => {
      shellDevPopoutWindow = null;
    });
  }
  try {
    await win.loadURL(reachable);
    win.show();
    win.focus();
  } catch {
    return { ok: false, message: "预览窗口暂时无法打开。" };
  }
  return {
    ok: true,
    url: reachable,
    configuredUrl: pick.configured,
    fallbackToMain: pick.fallback,
    message: pick.fallback
      ? `未检测到 ${pick.configured}，独立窗口已打开主壳 ${reachable}。`
      : undefined,
  };
});

ipcMain.handle("shell-dev:reload-preview", async (event) => {
  if (!workbenchWindow || workbenchWindow.isDestroyed() || event.sender !== workbenchWindow.webContents) {
    return false;
  }
  const view = shellDevPreviewView;
  if (!view || view.webContents.isDestroyed()) return false;
  try {
    await view.webContents.loadURL(shellDevPreviewBaseUrl || defaultShellDevPreviewBase());
    return true;
  } catch {
    return false;
  }
});

ipcMain.handle("shell-dev:set-view-state", (event, rawState: unknown) => {
  if (!workbenchWindow || workbenchWindow.isDestroyed() || event.sender !== workbenchWindow.webContents) {
    return false;
  }
  const view = shellDevPreviewView;
  if (!view || view.webContents.isDestroyed()) return false;
  if (!rawState || typeof rawState !== "object" || typeof (rawState as { visible?: unknown }).visible !== "boolean") {
    return false;
  }
  const state = rawState as { visible: boolean; bounds?: unknown };
  if (!state.visible) {
    view.setVisible(false);
    return true;
  }
  const bounds = boundedMusicViewRect(state.bounds);
  if (!bounds) {
    view.setVisible(false);
    return false;
  }
  applyShellDevPreviewViewBounds(view, bounds);
  view.setVisible(true);
  return true;
});

ipcMain.handle("music:set-bilibili-view-state", (event, rawState: unknown) => {
  if (!workbenchWindow || workbenchWindow.isDestroyed() || event.sender !== workbenchWindow.webContents) return false;
  const view = bilibiliSearchView;
  if (!view || view.webContents.isDestroyed()) return false;
  if (!rawState || typeof rawState !== "object" || typeof (rawState as { visible?: unknown }).visible !== "boolean") return false;
  const state = rawState as { visible: boolean; bounds?: unknown };
  if (!state.visible) {
    view.setVisible(false);
    return true;
  }
  const bounds = boundedMusicViewRect(state.bounds);
  if (!bounds) {
    view.setVisible(false);
    return false;
  }
  view.setBounds(bounds);
  view.setVisible(true);
  return true;
});

ipcMain.handle("app:open-external", async (_event, raw: unknown) => {
  if (typeof raw !== "string" || !raw.trim()) return false;
  const url = raw.trim();
  if (!/^https?:\/\//i.test(url)) return false;
  await shell.openExternal(url);
  return true;
});

ipcMain.handle("dialog:pick-directory", async () => {
  const parent =
    (workbenchWindow && !workbenchWindow.isDestroyed() ? workbenchWindow : null) ??
    BrowserWindow.getFocusedWindow();
  const result = parent
    ? await dialog.showOpenDialog(parent, {
        title: "选择托管文件夹",
        properties: ["openDirectory", "createDirectory"],
      })
    : await dialog.showOpenDialog({
        title: "选择托管文件夹",
        properties: ["openDirectory", "createDirectory"],
      });
  if (result.canceled || !result.filePaths.length) {
    return null;
  }
  return result.filePaths[0];
});

ipcMain.handle("app:get-downloads-path", () => app.getPath("downloads"));
ipcMain.handle("app:get-desktop-path", () => app.getPath("desktop"));

ipcMain.handle("staging:write-temp", async (_event, name: unknown, data: unknown) => {
  if (typeof name !== "string" || !name.trim()) {
    throw new Error("staging name required");
  }
  const bytes =
    data instanceof Uint8Array
      ? Buffer.from(data)
      : Buffer.isBuffer(data)
        ? data
        : null;
  if (!bytes?.length) {
    throw new Error("staging payload empty");
  }
  const safeName = path.basename(name).replace(/[^\w.\-()+ ]+/g, "_") || "paste.png";
  const dir = path.join(os.tmpdir(), "my-agent-paste");
  mkdirSync(dir, { recursive: true });
  const filePath = path.join(dir, `${Date.now()}-${safeName}`);
  writeFileSync(filePath, bytes);
  return filePath;
});

async function readClipboardNativeImage(): Promise<Electron.NativeImage | null> {
  const imageMimes = ["image/png", "image/jpeg", "image/bmp", "image/webp"];
  for (const mime of imageMimes) {
    if (!(await clipboard.has(mime))) continue;
    const items = await clipboard.read();
    for (const item of items) {
      if (!item.types.includes(mime)) continue;
      const payload = await item.getType(mime);
      if (!(payload instanceof Blob)) continue;
      const image = nativeImage.createFromBuffer(Buffer.from(await payload.arrayBuffer()));
      if (!image.isEmpty()) return image;
    }
  }
  const items = await clipboard.read();
  for (const item of items) {
    const mime = item.types.find((t) => t.startsWith("image/"));
    if (!mime) continue;
    const payload = await item.getType(mime);
    if (!(payload instanceof Blob)) continue;
    const image = nativeImage.createFromBuffer(Buffer.from(await payload.arrayBuffer()));
    if (!image.isEmpty()) return image;
  }
  return null;
}

ipcMain.handle("agent:read-image-data-url", (_event, rawPath: unknown) => {
  if (typeof rawPath !== "string" || !rawPath.trim()) return null;
  return readAgentImageDataUrl(activeSidecarRoot, rawPath.trim());
});

ipcMain.handle("clipboard:read-image-temp", async () => {
  const image = await readClipboardNativeImage();
  if (!image) return null;
  const dir = path.join(os.tmpdir(), "my-agent-paste");
  mkdirSync(dir, { recursive: true });
  const filePath = path.join(dir, `${Date.now()}-clipboard.png`);
  writeFileSync(filePath, image.toPNG());
  return filePath;
});

ipcMain.handle("constellation:read", () => readConstellationFile());

ipcMain.handle(
  "constellation:write",
  (_event, payload: { version: 1; stars: unknown[]; links: unknown[] }) => {
    if (!payload || payload.version !== 1 || !Array.isArray(payload.stars) || !Array.isArray(payload.links)) {
      throw new Error("invalid constellation payload");
    }
    atomicWriteJson(CONSTELLATION_PATH, payload);
    return true;
  },
);

ipcMain.handle("constellation:clear", () => {
  atomicWriteJson(CONSTELLATION_PATH, { version: 1, stars: [], links: [] });
  return true;
});

app.whenReady().then(async () => {
  if (process.platform === "win32") {
    app.setAppUserModelId("my-agent.desktop");
  }
  const dockIcon = loadAppIcon(256);
  if (!dockIcon.isEmpty() && process.platform === "darwin") {
    app.dock?.setIcon(dockIcon);
  }
  applyApplicationMenu();
  buildTray();
  registerShortcuts();

  try {
    await ensureSidecar();
    void ensureSandboxSidecar().catch((err) => {
      console.warn("[sandbox-sidecar] optional start failed:", err);
    });
  } catch (err) {
    console.error("Failed to start Python sidecar:", err);
    await dialog.showMessageBox({
      type: "error",
      title: "my-agent",
      message: "无法启动会话 sidecar",
      detail: err instanceof Error ? err.message : String(err),
    });
    quitting = true;
    finalizeAppExit();
    return;
  }

  await openWorkbenchWindow();
});

app.on("window-all-closed", () => {
  if (quitting) {
    finalizeAppExit();
  }
});

app.on("before-quit", () => {
  quitting = true;
  globalShortcut.unregisterAll();
  stopSandboxSidecar();
  stopSidecar();
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
});
