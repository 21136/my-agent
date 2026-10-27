import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import os from "node:os";
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
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
import { createLegacySandboxLane, createTrustedLegacyLaneHandlers } from "./legacy-sandbox-lane";
import { SidecarBootstrapRegistry, sidecarBootstrapLaunch, type EvolutionSidecarBootstrap } from "./evolution/sidecar-bootstrap";
import { createEvolutionSupervisorHost, type EvolutionSupervisorHost } from "./evolution/supervisor-host";
import { buildEvolutionTestSupervisorConfig, loadEvolutionTestHostProfile, type EvolutionTestHostContext } from "./evolution/test-host-config";
import { bindEvolutionIpc, type EvolutionIpcAdapter, type EvolutionIpcEvent } from "./evolution/main-bridge";
import { startEvolutionTestRendererServer, type EvolutionTestRendererServer } from "./evolution/sealed-renderer-server";
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
  createSandboxAdoptRequestHandler,
  createSandboxAdopter,
  prepareAndMergeSandboxIntoStable,
  isTrustedSandboxAdoptSender,
  clearSandboxPreviewPreference,
  sandboxAdoptionHostBlocker,
  type SandboxAdoptStatus,
} from "./sandbox-adopt";
import { loadAppIcon } from "./app-icon";
import {loadFixedHostContext} from "./fixed-host-context";
import { createManagedReviewHandler, resolveManagedReviewRoots } from "./managed-review-bridge";
import { createManagedSelectionHandler } from "./managed-selection-bridge";
import { createManagedCheckpointHandler } from "./managed-checkpoint-bridge";
import { createManagedSelectedMergeHandler } from "./managed-selected-merge-bridge";
import { validateManagedSelectionRequest, createManagedSourceSelectionProposal } from "../launcher/managed-source-selection.mjs";
import { validateManagedCheckpointRequest } from "../launcher/managed-source-checkpoint.mjs";
import { validateManagedSelectedMergeRequest } from "../launcher/managed-selected-merge.mjs";
import {createAdoptionJournal,cleanHostEnvironment} from "../launcher/index.mjs";

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
  if (!fixedHostContext && configured !== main) {
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
// Only a private Launcher bootstrap plus full manifest/target verification establishes this identity.
const fixedHostContext = loadFixedHostContext(AGENT_ROOT, process.argv);
const DATA_ROOT = fixedHostContext?.dataRoot ?? path.join(AGENT_ROOT, "data");
const fixedAdoptionJournal = fixedHostContext ? createAdoptionJournal(fixedHostContext.binding.state_root, {
  installation_id:fixedHostContext.binding.installation_id, release_id:fixedHostContext.binding.release.release_id, binding_digest:fixedHostContext.bindingDigest,
}) : null;
const sandboxAdoptionService = fixedHostContext ? createSandboxAdopter({
  stableRoot:fixedHostContext.binding.stable_source_root, sandboxRoot:fixedHostContext.binding.sandbox_source_root,
}, {observer:fixedAdoptionJournal!,revalidate:fixedHostContext.revalidate}) : {getStatus:getSandboxAdoptStatus,prepareAndMerge:prepareAndMergeSandboxIntoStable};
let adoptionInProgress = false;
let evolutionTestHostContext: EvolutionTestHostContext | null = null;
let evolutionTestHostContextError: Error | null = null;
let t9ShutdownToken: string | null = null;
try {
  evolutionTestHostContext = loadEvolutionTestHostProfile(AGENT_ROOT);
} catch (error) {
  evolutionTestHostContextError = error instanceof Error ? error : new Error(String(error));
}
if (fixedHostContext && evolutionTestHostContext) throw Error("fixed and rehearsal Host identities cannot be combined");
if (evolutionTestHostContext?.profile.candidate_fixture_profile === "t4-recovery-node-v1") {
  let controlBuffer = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk: string) => {
    controlBuffer += chunk;
    if (Buffer.byteLength(controlBuffer, "utf8") > 8192) { controlBuffer = ""; return; }
    for (;;) {
      const newline = controlBuffer.indexOf("\n"); if (newline < 0) break;
      const line = controlBuffer.slice(0, newline).replace(/\r$/u, ""); controlBuffer = controlBuffer.slice(newline + 1);
      let frame: unknown; try { frame = JSON.parse(line); } catch { continue; }
      if (!frame || typeof frame !== "object" || Array.isArray(frame)) continue;
      const item = frame as Record<string, unknown>;
      if (item.type === "t9.control.bootstrap" && Object.keys(item).length === 2 && typeof item.token === "string" && /^[0-9a-f]{64}$/.test(item.token) && t9ShutdownToken === null) { t9ShutdownToken = item.token; continue; }
      if (item.type === "t9.control.shutdown" && Object.keys(item).length === 2 && t9ShutdownToken !== null && item.token === t9ShutdownToken) { t9ShutdownToken = null; app.quit(); }
    }
  });
}

/** Dev: keep Chromium/session cache on local disk (USB/removable roots cause lock storms). */
const LOCAL_DEV_ROOT = path.join(
  process.env.LOCALAPPDATA || process.env.TEMP || os.tmpdir(),
  "my-agent-desktop",
);
if (fixedHostContext) {
  const userData=path.join(fixedHostContext.profileRoot,"electron-userData");
  const sessionData=path.join(fixedHostContext.profileRoot,"electron-sessionData");
  const tempRoot=path.join(fixedHostContext.profileRoot,"tmp");
  mkdirSync(userData,{recursive:true});mkdirSync(sessionData,{recursive:true});mkdirSync(tempRoot,{recursive:true});
  app.setPath("temp",tempRoot);
  app.setPath("userData",userData);app.setPath("sessionData",sessionData);
} else if (evolutionTestHostContext) {
  try {
    const userData = path.join(evolutionTestHostContext.profile.profile_root, "electron-userData");
    const sessionData = path.join(evolutionTestHostContext.profile.profile_root, "electron-sessionData");
    mkdirSync(userData, { recursive: false });
    mkdirSync(sessionData, { recursive: false });
    app.setPath("userData", userData);
    app.setPath("sessionData", sessionData);
  } catch (error) {
    evolutionTestHostContextError = error instanceof Error ? error : new Error(String(error));
  }
} else if (!app.isPackaged) {
  const userData = path.join(LOCAL_DEV_ROOT, "userData");
  mkdirSync(userData, { recursive: true });
  app.setPath("userData", userData);
  app.setPath("sessionData", path.join(LOCAL_DEV_ROOT, "session-data"));
  app.commandLine.appendSwitch(
    "disk-cache-dir",
    path.join(LOCAL_DEV_ROOT, "disk-cache"),
  );
}

const STATE_PATH = path.join(DATA_ROOT, "state.json");
const CONSTELLATION_PATH = path.join(DATA_ROOT, "constellation.json");
const SHOW_SHORTCUT = "CommandOrControl+Shift+M";
const DEFAULT_WS_PORT = fixedHostContext || evolutionTestHostContext ? 0 : Number(process.env.MY_AGENT_WS_PORT ?? "8765") || 8765;
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
let legacySessionWorkbenchOrigin = "";
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
const sidecarSupervisor = new SidecarSupervisor();
const evolutionSidecarBootstraps = new SidecarBootstrapRegistry();
let evolutionSupervisorHost: EvolutionSupervisorHost | null = null;
let evolutionTestRendererServer: EvolutionTestRendererServer | null = null;
let activeEvolutionConversationId: string | null = null;
let removeEvolutionIpcHandlers: (() => void) | null = null;
let evolutionHostShutdownInProgress = false;

async function closeEvolutionSupervisorHost(): Promise<void> {
  const host = evolutionSupervisorHost;
  evolutionSupervisorHost = null;
  if (!host) {
    evolutionSidecarBootstraps.clearAll();
    return;
  }
  try {
    await host.close();
  } finally {
    evolutionSidecarBootstraps.clearAll();
  }
}
async function closeEvolutionTestRendererServer(): Promise<void> {
  const server = evolutionTestRendererServer;
  evolutionTestRendererServer = null;
  if (server) await server.close();
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

function pythonCommand(root: string = AGENT_ROOT, trustedHost = false): string {
  if(fixedHostContext){fixedHostContext.revalidate();if(path.resolve(root)!==AGENT_ROOT)throw Error("fixed Host cannot load another sidecar root");const runtime=fixedHostContext.manifest.runtimes.python;return path.join(runtime.root,runtime.entry);}
  if (trustedHost && evolutionTestHostContext && path.resolve(root) === AGENT_ROOT) {
    return evolutionTestHostContext.profile.trusted_python_executable;
  }
  if (process.platform === "win32") {
    const localPython = path.join(root, ".venv", "Scripts", "python.exe");
    return existsSync(localPython) ? localPython : "python";
  }
  const localPython = path.join(root, ".venv", "bin", "python");
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

function startSidecar(root: string = activeSidecarRoot, evolutionBootstrap?: EvolutionSidecarBootstrap): Promise<SidecarStartResult> {
  return new Promise((resolve, reject) => {
    const selectedBootstrap = evolutionBootstrap ?? evolutionSupervisorHost?.getSidecarBootstrap(root) ?? undefined;
    if (selectedBootstrap) evolutionSidecarBootstraps.register(root, selectedBootstrap);
    const privateFrame = selectedBootstrap ? evolutionSidecarBootstraps.forRoot(root) : null;
    const privateLaunch = sidecarBootstrapLaunch(privateFrame);
    const script = path.join(root, "agent-core", fixedHostContext ? "fixed_host_entry.py" : "server.py");
    if(fixedHostContext && selectedBootstrap) throw Error("fixed E0 layout cannot be mixed with evolution test bootstrap");
    const fixedFrame=fixedHostContext?.sidecarLayoutFrame();
    const args = fixedHostContext ? ["-I","-B","-S",script,"--port","0"] : [script, "--port", String(DEFAULT_WS_PORT), ...privateLaunch.args];
    sidecar = spawn(pythonCommand(root, privateLaunch.privateInput !== null), args, {
      cwd: fixedHostContext?.binding.instance_root ?? root,
      stdio: [fixedHostContext ? "pipe" : privateLaunch.stdin, "pipe", "pipe"],
      env: fixedHostContext ? {...cleanHostEnvironment(),TEMP:path.join(fixedHostContext.profileRoot,"tmp"),TMP:path.join(fixedHostContext.profileRoot,"tmp")} : sidecarSpawnEnv(),
      windowsHide:true, shell:false,
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

    const privateInput = fixedFrame ?? privateLaunch.privateInput;
    if (privateInput) {
      const input = proc.stdin;
      const failBootstrapDelivery = () => {
        if (settled) {
          try { proc.kill(); } catch { /* do not keep a sidecar with unknown bootstrap state */ }
          return;
        }
        settled = true;
        settledRef.value = true;
        clearTimeout(timeout);
        try { proc.kill(); } catch { /* startup failure remains explicit */ }
        reject(new Error("sidecar private bootstrap delivery failed"));
      };
      if (!input) {
        failBootstrapDelivery();
      } else {
        input.once("error", failBootstrapDelivery);
        try { input.end(privateInput); }
        catch { failBootstrapDelivery(); }
      }
    }
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
  if (!fixedHostContext && existsSync(lockPath)) {
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
    const proc = spawn(pythonCommand(root), [script, "--port", String(port)], {
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
  if(fixedHostContext || adoptionInProgress) return {ok:false,state:"blocked",error:"固定控制入口不支持旧候选直接替换 sidecar；采用中也不切换。"};
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
  if (DEFAULT_WS_PORT !== 0 && !sidecarOwned && (await waitForPortOpen("127.0.0.1", DEFAULT_WS_PORT))) {
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
  const lockPath = path.join(DATA_ROOT, "sessions", ".interface.lock");
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

function legacySandboxLaneAllowed(): boolean {
  return !fixedHostContext && !evolutionTestHostContext && !evolutionTestHostContextError
    && !app.isPackaged && Boolean(process.env.VITE_DEV_SERVER_URL);
}

const legacySandboxLane = createLegacySandboxLane({
  agentRoot: AGENT_ROOT,
  allowed: legacySandboxLaneAllowed,
  primary: () => ({ root: activeSidecarRoot, owned: sidecarOwned, running: Boolean(sidecar && !sidecar.killed), info: sidecarInfo }),
  spawn: (root) => spawn(pythonCommand(root), [path.join(root, "agent-core", "server.py"), "--port", "0"], {
    cwd: root, stdio: ["ignore", "pipe", "pipe"],
    env: { ...sidecarSpawnEnv(), MY_AGENT_SANDBOX_ROOT: root }, windowsHide: true, shell: false,
  }),
  readyTimeoutMs: SIDECAR_READY_TIMEOUT_MS,
  onStderr: (chunk) => console.error("[sandbox-sidecar]", chunk.toString("utf8")),
});
function stopSandboxSidecar(): void { legacySandboxLane.stop(); }
function ensureSandboxSidecar() { return legacySandboxLane.ensure(); }
async function isAgentBusy(): Promise<boolean> {
  if (!workbenchWindow || workbenchWindow.isDestroyed()) {
    return false;
  }
  return workbenchWindow.webContents
    .executeJavaScript("Boolean(window.__myAgentIsBusy?.())", true)
    .catch(() => false);
}

async function requestAppQuit(): Promise<void> {
  if(adoptionInProgress){await dialog.showMessageBox({type:"warning",message:"源码采用正在进行，请等待完成或查看恢复状态后退出。",buttons:["继续等待"]});return;}
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
  try {
    await closeEvolutionSupervisorHost();
  } catch (error) {
    console.error("[evolution] supervisor shutdown failed:", error);
  }
  try {
    await closeEvolutionTestRendererServer();
  } catch (error) {
    console.error("[evolution] renderer server shutdown failed:", error);
  }
  if (workbenchWindow && !workbenchWindow.isDestroyed()) {
    workbenchWindow.removeAllListeners("close");
    workbenchWindow.close();
  }
  workbenchWindow = null;
  finalizeAppExit();
}

async function restartSidecarAfterCrash(): Promise<void> {
  if(fixedHostContext){console.error("[fixed-host] owned sidecar exited; automatic restart is disabled");app.exit(2);return;}
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

function rendererOrigin(url: string): string {
  try {
    const parsed = new URL(url);
    if(parsed.protocol === "file:"){parsed.search="";parsed.hash="";return parsed.href;}
    return parsed.origin;
  } catch {
    return "";
  }
}

function expectedWorkbenchOrigin(): string {
  if(fixedHostContext) return pathToFileURL(path.join(__dirname,"../dist","index.html")).href;
  if (evolutionTestRendererServer) return rendererOrigin(evolutionTestRendererServer.origin);
  if (process.env.VITE_DEV_SERVER_URL) return rendererOrigin(resolveShellMainDevUrl() || defaultShellMainDevBase());
  return pathToFileURL(path.join(__dirname, "../dist", "index.html")).href;
}

function evolutionIpcAdapter(): EvolutionIpcAdapter {
  return {
    handle(channel, handler) {
      ipcMain.handle(channel, (event, payload) => {
        const frame = event.senderFrame;
        const mapped: EvolutionIpcEvent = {
          senderId: event.sender.id,
          isMainFrame: Boolean(frame && frame === event.sender.mainFrame),
          origin: frame ? rendererOrigin(frame.url) : "",
        };
        return handler(mapped, payload);
      });
    },
    removeHandler(channel) { ipcMain.removeHandler(channel); },
  };
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

function persistShellRendererUrl(url: string): void {
  const normalized = normalizeShellDevPreviewBase(url);
  if (!normalized || !isAllowedShellRendererPromotion(normalized)) return;
  let payload: Record<string, unknown> = {};
  if (existsSync(STATE_PATH)) {
    try {
      const loaded = JSON.parse(readFileSync(STATE_PATH, "utf-8")) as Record<string, unknown>;
      if (loaded && typeof loaded === "object") payload = loaded;
    } catch {
      payload = {};
    }
  }
  payload.desktop_shell_renderer_url = normalized;
  writeFileSync(STATE_PATH, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
}

function resolveShellMainDevUrl(): string {
  if(fixedHostContext) return "";
  if (process.env.VITE_DEV_SERVER_URL) {
    return readPersistedShellRendererUrl() || defaultShellMainDevBase();
  }
  return "";
}

async function loadRenderer(win: BrowserWindow): Promise<void> {
  if(fixedHostContext){fixedHostContext.revalidate();await win.loadFile(path.join(__dirname,"../dist","index.html"));return;}
  if (evolutionTestRendererServer) {
    await win.loadURL(evolutionTestRendererServer.indexUrl);
    return;
  }
  if (process.env.VITE_DEV_SERVER_URL) {
    await loadLegacyWorkbenchUrl(win, resolveShellMainDevUrl() || defaultShellMainDevBase());
    return;
  }
  await win.loadFile(path.join(__dirname, "../dist", "index.html"));
}

async function loadLegacyWorkbenchUrl(win: BrowserWindow, url: string): Promise<void> {
  const previous = legacySessionWorkbenchOrigin, origin = rendererOrigin(url);
  legacySessionWorkbenchOrigin = origin;
  try { await win.loadURL(url); }
  catch (error) { if (legacySessionWorkbenchOrigin === origin) legacySessionWorkbenchOrigin = previous; throw error; }
}

function isAppOwnedUrl(url: string): boolean {
  const text = url.trim();
  if (!text || text === "about:blank") return true;
  if (evolutionTestRendererServer) {
    try { return new URL(text).origin === evolutionTestRendererServer.origin; }
    catch { return false; }
  }
  if (text.startsWith("file:")) return fixedHostContext ? rendererOrigin(text)===expectedWorkbenchOrigin() : true;
  if(fixedHostContext) return false;
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

  removeEvolutionIpcHandlers?.();
  activeEvolutionConversationId = null;
  removeEvolutionIpcHandlers = bindEvolutionIpc({
    mainWindow: workbenchWindow.webContents,
    ipc: evolutionIpcAdapter(),
    trustedOrigin: expectedWorkbenchOrigin(),
    getConversationId: () => activeEvolutionConversationId,
    setConversationId: (conversationId) => { activeEvolutionConversationId = conversationId; },
    callHost: async (message) => {
      if (!evolutionSupervisorHost?.getSidecarBootstrap(activeSidecarRoot)) return { status: "disabled", message: "当前 sidecar 不是固定测试 Host" };
      if (!workbenchWindow || workbenchWindow.isDestroyed()) return { status: "disabled", message: "工作台窗口已关闭" };
      workbenchWindow.webContents.send("evolution:request", message);
      return { status: "queued" };
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
    removeEvolutionIpcHandlers?.();
    removeEvolutionIpcHandlers = null;
    activeEvolutionConversationId = null;
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
  if(fixedHostContext || adoptionInProgress){await dialog.showMessageBox({type:"info",message:"固定控制入口不从可变源码启动旧 CLI；采用期间也不切换。",buttons:["知道了"]});return;}
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
const legacySandboxLaneIpc = createTrustedLegacyLaneHandlers({
  isTrusted: (event: IpcMainInvokeEvent) => Boolean(legacySessionWorkbenchOrigin)
    && assertShellDevWorkbenchSender(event, legacySessionWorkbenchOrigin),
  allowed: legacySandboxLaneAllowed,
  get: () => legacySandboxLane.get(),
  ensure: ensureSandboxSidecar,
});
ipcMain.handle("sidecar:session-lane-capabilities", (event) => legacySandboxLaneIpc.capabilities(event));
ipcMain.handle("sidecar:get-sandbox", (event) => legacySandboxLaneIpc.get(event));
ipcMain.handle("sidecar:ensure-sandbox", (event) => legacySandboxLaneIpc.ensure(event));
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

function assertShellDevWorkbenchSender(event: IpcMainInvokeEvent, pinnedOrigin?: string): boolean {
  if (!workbenchWindow || workbenchWindow.isDestroyed()) return false;
  if (event.sender.id !== workbenchWindow.webContents.id || !event.senderFrame || event.senderFrame !== event.sender.mainFrame) return false;
  return isTrustedSandboxAdoptSender({
    senderId: event.sender.id, windowId: workbenchWindow.webContents.id,
    isMainFrame: Boolean(event.senderFrame && event.senderFrame === event.sender.mainFrame),
    origin: event.senderFrame ? rendererOrigin(event.senderFrame.url) : "", expectedOrigin: pinnedOrigin ?? expectedWorkbenchOrigin(),
  });
}

const managedReviewHandler = createManagedReviewHandler({
  isTrusted: () => true,
  hasArgs: (args) => args.length > 0,
  isFixture: Boolean(fixedHostContext || evolutionTestHostContext),
  resolveRoots: () => resolveManagedReviewRoots(AGENT_ROOT, async (roots) => {
    const sourcePlanUrl = pathToFileURL(path.join(AGENT_ROOT, "desktop", "launcher", "managed-source-plan.mjs")).href;
    const { inspectManagedSources } = await import(sourcePlanUrl);
    return inspectManagedSources({ ...roots, metadataOnlyHistory: true });
  }),
  inspect: async (roots) => {
    const reviewUrl = pathToFileURL(path.join(AGENT_ROOT, "desktop", "launcher", "managed-review.mjs")).href;
    const { inspectManagedReview } = await import(reviewUrl);
    return inspectManagedReview(roots);
  },
});

ipcMain.handle("shell-dev:managed-source-review", async (event, ...args: unknown[]) => {
  // Validate the trusted main frame before resolving any code root or reading source metadata.
  if (!assertShellDevWorkbenchSender(event)) return { ok: false, message: "仅允许可信工作台主页面查看源码审查报告。" };
  return managedReviewHandler(...args);
});
const managedSelectionHandler = createManagedSelectionHandler({
  isTrusted: (event: IpcMainInvokeEvent) => assertShellDevWorkbenchSender(event),
  isFixture: Boolean(fixedHostContext || evolutionTestHostContext),
  validateRequest: validateManagedSelectionRequest,
  resolveRoots: () => resolveManagedReviewRoots(AGENT_ROOT, async (roots) => {
    const sourcePlanUrl = pathToFileURL(path.join(AGENT_ROOT, "desktop", "launcher", "managed-source-plan.mjs")).href;
    const { inspectManagedSources } = await import(sourcePlanUrl);
    return inspectManagedSources({ ...roots, metadataOnlyHistory: true });
  }),
  inspect: async (roots) => {
    const reviewUrl = pathToFileURL(path.join(AGENT_ROOT, "desktop", "launcher", "managed-review.mjs")).href;
    const { inspectManagedReview } = await import(reviewUrl);
    return inspectManagedReview(roots);
  },
  propose: createManagedSourceSelectionProposal,
});

ipcMain.handle("shell-dev:managed-source-selection", (event, ...args: unknown[]) => {
  // The caller must still be trusted after every asynchronous source capture.
  return managedSelectionHandler(event, ...args);
});

let managedCheckpointBackend: typeof import("../launcher/managed-source-checkpoint.mjs") | null = null;
function nativeCheckpointDetails(pack: import("../launcher/managed-source-checkpoint.mjs").ManagedSourceCheckpoint): string {
  const selectionRequest = { format: 1 as const, mode: "managed_source_selection_request_v1" as const, reviewDigest: pack.request.reviewDigest, selectedPaths: [...pack.request.selectedPaths] };
  const proposal = createManagedSourceSelectionProposal({ review: pack.review, request: selectionRequest });
  const blockers = (label: string, values: unknown) => {
    const list = Array.isArray(values) ? values : [];
    const counts = new Map<string, number>();
    for (const item of list) {
      const value = item && typeof item === "object" && !Array.isArray(item) ? item as Record<string, unknown> : {};
      const code = typeof value.code === "string" && value.code ? value.code : "unknown_blocker";
      counts.set(code, (counts.get(code) ?? 0) + 1);
    }
    return [
      `${label}：${list.length} 条 / ${counts.size} 类`,
      ...[...counts].map(([code, count]) => `- ${code}：${count} 条`),
    ].join("\n");
  };
  const files = pack.files.map((file) => `- ${String(file.path)} · SHA-256=${String(file.workingSha256)} · mode=${String(file.mode)} · ${String(file.size)} 字节`).join("\n");
  return [
    `Review SHA-256：${pack.request.reviewDigest}`,
    `Proposal SHA-256：${pack.request.proposalDigest}`,
    `Checkpoint SHA-256：${pack.checkpointDigest}`,
    `原始字节总数：${pack.rawBytes}`,
    "所选文件（完整路径、内容哈希、模式、大小）：", files,
    blockers("审查阻塞项", pack.review.blockers),
    blockers("范围提案阻塞项", proposal.blockers),
    "仅创建所选源码检查点；不合入、不启动、不验证所有权。Canonical 哈希绑定内容，不认证来源或同意。",
  ].join("\n\n");
}
const managedCheckpointHandler = createManagedCheckpointHandler<IpcMainInvokeEvent>({
  isTrusted: (event) => assertShellDevWorkbenchSender(event),
  isFixture: Boolean(fixedHostContext || evolutionTestHostContext),
  validateRequest: validateManagedCheckpointRequest,
  resolveRoots: async (event) => resolveManagedReviewRoots(AGENT_ROOT, async (roots) => {
    const sourcePlanUrl = pathToFileURL(path.join(AGENT_ROOT, "desktop", "launcher", "managed-source-plan.mjs")).href;
    const { inspectManagedSources } = await import(sourcePlanUrl);
    if (!assertShellDevWorkbenchSender(event)) throw new Error("untrusted checkpoint caller");
    const identity = await inspectManagedSources({ ...roots, metadataOnlyHistory: true });
    if (!assertShellDevWorkbenchSender(event)) throw new Error("untrusted checkpoint caller");
    return identity;
  }),
  inspect: async (roots, event) => {
    const reviewUrl = pathToFileURL(path.join(AGENT_ROOT, "desktop", "launcher", "managed-review.mjs")).href;
    const { inspectManagedReview } = await import(reviewUrl);
    if (!assertShellDevWorkbenchSender(event)) throw new Error("untrusted checkpoint caller");
    const report = await inspectManagedReview(roots);
    if (!assertShellDevWorkbenchSender(event)) throw new Error("untrusted checkpoint caller");
    return report;
  },
  propose: createManagedSourceSelectionProposal,
  loadBackend: async (event) => {
    const checkpointUrl = pathToFileURL(path.join(AGENT_ROOT, "desktop", "launcher", "managed-source-checkpoint.mjs")).href;
    const backend = await import(checkpointUrl);
    if (!assertShellDevWorkbenchSender(event)) throw new Error("untrusted checkpoint caller");
    managedCheckpointBackend = backend;
  },
  capture: (input, event) => {
    const backend = managedCheckpointBackend;
    if (!backend || !assertShellDevWorkbenchSender(event)) throw new Error("checkpoint backend unavailable");
    return backend.captureManagedSourceCheckpoint(input);
  },
  confirm: async (pack, event) => {
    const parent = workbenchWindow;
    if (!parent || parent.isDestroyed() || !assertShellDevWorkbenchSender(event)) return false;
    const answer = await dialog.showMessageBox(parent, {
      type: "warning",
      title: "确认保存选定源码检查点",
      message: "确认仅保存当前已核验的源码字节快照？此操作不合入、不启动，也不证明来源所有权。",
      detail: nativeCheckpointDetails(pack),
      buttons: ["保存检查点", "取消"],
      defaultId: 1,
      cancelId: 1,
      noLink: true,
    });
    return answer.response === 0 && assertShellDevWorkbenchSender(event) && !parent.isDestroyed() && workbenchWindow === parent;
  },
  persist: ({ sandboxRoot, checkpoint }) => {
    const backend = managedCheckpointBackend;
    if (!backend) throw new Error("checkpoint backend unavailable");
    return backend.persistManagedSourceCheckpoint({ sandboxRoot, checkpoint });
  },
});
ipcMain.handle("shell-dev:managed-source-checkpoint", (event, ...args: unknown[]) => {
  return managedCheckpointHandler(event, ...args);
});

let managedSelectedMergeBackend: typeof import("../launcher/managed-selected-merge.mjs") | null = null;
let managedSelectedMergeCheckpointBackend: typeof import("../launcher/managed-source-checkpoint.mjs") | null = null;
let managedSelectedMergeCheckpoint: import("../launcher/managed-source-checkpoint.mjs").ManagedSourceCheckpoint | null = null;
function nativeSelectedMergeDetails(input: { plan: import("../launcher/managed-selected-merge.mjs").ManagedSelectedMergePlan; checkpoint: import("../launcher/managed-source-checkpoint.mjs").ManagedSourceCheckpoint }): string {
  const { plan } = input;
  const preservation = plan.stablePreservation;
  const preservationDetails = preservation ? [
    `稳定已选中原始工作改动保留（${preservation.files.length} 个文件，${preservation.rawBytes} 字节）`,
    "以下原始未提交工作改动将先成为保留父提交中的已提交历史，不会继续保留为未提交改动；随后所选检查点内容作为子提交合入。只提交这些所选原始内容，不广泛暂存其他文件。",
    ...preservation.files.map((file) => `- ${file.path} · preserved original SHA-256=${file.sha256} · preserved Git blob OID=${file.oid} · preserved mode=${file.mode}`),
    `Preservation tree=${preservation.expectedTree}`,
    `Preservation commit=${preservation.expectedCommit}`,
    `Preservation parent=${plan.stable.head}`,
  ].join("\n") : "本计划不包含稳定原始工作改动保留提交。";
  const files = plan.files.map((file) =>
    `- ${file.path} · stable before SHA-256=${String(file.beforeSha256 ?? "absent/new path")} · selected after SHA-256=${String(file.rawSha256)} · stable before mode=${String(file.expectedBaseMode ?? "absent/new path")} · selected after mode=${String(file.mode)} · before Git blob OID=${String(file.expectedBaseOid ?? "absent/new path")} · action=${file.action}`
  ).join("\n");
  const laneDetails = (label: string, lane: typeof plan.stable) => [
    `${label} worktreeRoot=${lane.worktreeRoot}`,
    `${label} gitDir=${lane.gitDir}`,
    `${label} commonDir=${lane.commonDir}`,
    `${label} branch=${lane.branch}`,
    `${label} HEAD=${lane.head}`,
    `${label} index SHA-256=${lane.rawIndexSha256 ?? "absent"}`,
  ].join("\n");
  const blockerGroups = new Map<string, { disposition: string; code: string; count: number; paths: number }>();
  for (const blocker of plan.blockers) {
    const disposition = String(blocker.disposition);
    const code = String(blocker.code);
    const key = `${disposition}:${code}`;
    const group = blockerGroups.get(key) ?? { disposition, code, count: 0, paths: 0 };
    group.count++;
    group.paths += Array.isArray(blocker.paths) ? blocker.paths.length : 0;
    blockerGroups.set(key, group);
  }
  const blockers = blockerGroups.size
    ? [...blockerGroups.values()].map((group) => {
        const label = group.disposition === "preserve" ? "保留" : group.disposition === "exclude" ? "排除" : group.disposition === "review_required" ? "需人工复核" : "其他";
        return `- ${label} (${group.disposition}) · ${group.code} · ${group.count} 条 · ${group.paths} 个路径关联`;
      }).join("\n")
    : "（无计划 blocker）";
  return [
    "将真正通过项目 Git 采用下列检查点内容；所选 stable 原始工作改动仅按下述保留计划先提交为父提交。不会合入 sandbox 整段历史。其他未选中 dirty 文件、stable 未选中 index 条目及完整 sandbox 原 index 与 stashes 保持原状态；stable 已选中 index 条目会随项目合入更新。所选 stable 暂存/index/新路径碰撞或计划基线漂移仍拒绝。不直接覆盖原始改动、不清理未知锁。不会启动或激活候选版本，也不调用模型；source-only readyForLaunch=false。",
    "原生确认后即使 renderer 选择变化，也不会撤销宿主已确认的项目 Git 操作。",
    `Selected paths (${plan.selectedPaths.length}):`, files,
    laneDetails("Stable", plan.stable), laneDetails("Sandbox", plan.sandbox),
    preservationDetails,
    `Plan blockers（${plan.blockers.length} 条；按 disposition/code 归组）\n${blockers}\n这些是本计划保留/排除/需人工处理的约束，不表示原始检查点 blockers 已清空。`,
    `Plan digest=${plan.planDigest}`,
    `Checkpoint digest=${plan.checkpointDigest}`,
    `Expected tree=${plan.expectedMergedTree}`,
    `Expected commit=${plan.expectedCommit}`,
    `Final expected commit parent=${preservation?.expectedCommit ?? plan.stable.head}`,
    `Preservation digest=${plan.preservationDigest}`,
  ].join("\n\n");
}
const managedSelectedMergeHandler = createManagedSelectedMergeHandler<IpcMainInvokeEvent>({
  isTrusted: (event) => assertShellDevWorkbenchSender(event),
  isFixture: Boolean(fixedHostContext || evolutionTestHostContext),
  validateRequest: validateManagedSelectedMergeRequest,
  loadBackend: async (event) => {
    if (process.platform !== "win32") throw new Error("native project Git merge is currently Windows-only");
    const backendUrl = pathToFileURL(path.join(AGENT_ROOT, "desktop", "launcher", "managed-selected-merge.mjs")).href;
    const checkpointUrl = pathToFileURL(path.join(AGENT_ROOT, "desktop", "launcher", "managed-source-checkpoint.mjs")).href;
    const [backend, checkpointBackend] = await Promise.all([import(backendUrl), import(checkpointUrl)]);
    if (!assertShellDevWorkbenchSender(event)) throw new Error("untrusted selected merge caller");
    managedSelectedMergeBackend = backend;
    managedSelectedMergeCheckpointBackend = checkpointBackend;
  },
  resolveRoots: async (event) => {
    const roots = await resolveManagedReviewRoots(AGENT_ROOT, async (candidate) => {
      const sourcePlanUrl = pathToFileURL(path.join(AGENT_ROOT, "desktop", "launcher", "managed-source-plan.mjs")).href;
      const { inspectManagedSources } = await import(sourcePlanUrl);
      if (!assertShellDevWorkbenchSender(event)) throw new Error("untrusted selected merge caller");
      const identity = await inspectManagedSources({ ...candidate, metadataOnlyHistory: true });
      if (!assertShellDevWorkbenchSender(event)) throw new Error("untrusted selected merge caller");
      return identity;
    });
    if (!assertShellDevWorkbenchSender(event)) throw new Error("untrusted selected merge caller");
    return roots;
  },
  readCheckpoint: (_event, request, roots) => {
    const backend = managedSelectedMergeCheckpointBackend;
    if (!backend) throw new Error("checkpoint reader unavailable");
    const checkpoint = backend.readManagedSourceCheckpoint({ sandboxRoot: roots.sandboxRoot, checkpointDigest: request.checkpointDigest });
    managedSelectedMergeCheckpoint = checkpoint;
    return checkpoint;
  },
  prepare: ({ roots, checkpoint }) => {
    const backend = managedSelectedMergeBackend;
    if (!backend) throw new Error("selected merge backend unavailable");
    // Trusted Host policy only; the renderer request never supplies this option.
    return backend.prepareManagedSelectedMerge({ stableRoot: roots.stableRoot, sandboxRoot: roots.sandboxRoot, checkpoint, gitExecutable: "C:\\Program Files\\Git\\cmd\\git.exe", preserveStableWorkingChanges: true });
  },
  confirm: async ({ caller, request, plan }) => {
    const parent = workbenchWindow;
    if (!parent || parent.isDestroyed() || !assertShellDevWorkbenchSender(caller)) return false;
    const checkpoint = managedSelectedMergeCheckpoint;
    if (!checkpoint || checkpoint.checkpointDigest !== request.checkpointDigest || plan.checkpointDigest !== request.checkpointDigest
        || JSON.stringify(plan.selectedPaths) !== JSON.stringify(checkpoint.request.selectedPaths)
        || JSON.stringify(plan.files.map((file) => file.path)) !== JSON.stringify(checkpoint.request.selectedPaths)) return false;
    const isRecord = (value: unknown): value is Record<string, unknown> =>
      value !== null && typeof value === "object" && !Array.isArray(value);
    const stableReview = checkpoint.review.stable;
    const sandboxReview = checkpoint.review.sandbox;
    if (!isRecord(stableReview) || !isRecord(sandboxReview)
        || !isRecord(stableReview.identity) || !isRecord(sandboxReview.identity)
        || plan.stable.worktreeRoot !== stableReview.identity.worktreeRoot
        || plan.sandbox.worktreeRoot !== sandboxReview.identity.worktreeRoot) return false;
    const answer = await dialog.showMessageBox(parent, {
      type: "warning", title: "确认通过项目 Git 合入所选源码",
      message: plan.stablePreservation
        ? "这将真实修改 stable 项目 Git：先将所选原始未提交工作改动提交为保留父提交，再以子提交采用已选检查点内容。"
        : "这将真实修改 stable 项目 Git：仅采用已选内容，不合入 sandbox 整段历史。",
      detail: nativeSelectedMergeDetails({ plan, checkpoint }),
      buttons: ["通过项目 Git 合入所选内容", "取消"], defaultId: 1, cancelId: 1, noLink: true,
    });
    return answer.response === 0 && assertShellDevWorkbenchSender(caller) && !parent.isDestroyed() && workbenchWindow === parent;
  },
  execute: ({ caller, request, plan, roots, finalCheckpoint }) => {
    const backend = managedSelectedMergeBackend;
    if (!backend || !assertShellDevWorkbenchSender(caller) || finalCheckpoint.checkpointDigest !== request.checkpointDigest || plan.checkpointDigest !== request.checkpointDigest) throw new Error("selected merge backend unavailable");
    if (process.platform !== "win32") throw new Error("native project Git merge is currently Windows-only");
    const directory = path.join(roots.sandboxRoot, "artifacts", "managed-selected-merges", plan.planDigest);
    return backend.executeManagedSelectedMerge({ plan, checkpoint: finalCheckpoint, stableRoot: roots.stableRoot, sandboxRoot: roots.sandboxRoot, gitExecutable: "C:\\Program Files\\Git\\cmd\\git.exe", directory, assertTrusted: () => assertShellDevWorkbenchSender(caller) });
  },
});
ipcMain.handle("shell-dev:managed-selected-merge", (event, ...args: unknown[]) => managedSelectedMergeHandler(event, ...args));

ipcMain.handle("shell-dev:sandbox-adopt-status", async (event) => {
  if (!assertShellDevWorkbenchSender(event)) {
    return { ok: false, message: "workbench unavailable" };
  }
  const status = await sandboxAdoptionService.getStatus();
  try {fixedAdoptionJournal?.assertIdle();} catch(error){status.fatalBlockers.unshift(error instanceof Error?error.message:String(error));status.readyToMerge=false;}
  const blocker = sandboxAdoptionHostBlocker({ mutableDevelopmentHost: Boolean(process.env.VITE_DEV_SERVER_URL), testHost: Boolean(evolutionTestHostContext), fixedHostVerified:Boolean(fixedHostContext) });
  if (blocker) { status.fatalBlockers.unshift(blocker); status.readyToMerge = false; }
  return status;
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

const requestSandboxAdoption = createSandboxAdoptRequestHandler(sandboxAdoptionService, async (status: SandboxAdoptStatus) => {
  const parent = workbenchWindow;
  if (!parent || parent.isDestroyed()) return false;
  const changed = [...new Set([...status.mergeFiles, ...status.sandboxDirtyProduct.map((line) => line.slice(3))])];
  const answer = await dialog.showMessageBox(parent, {
    type: "warning", title: "确认将实验版合入稳定版", message: "将保存实验改动并合入稳定分支，继续吗？",
    detail: [
      "目标：" + status.currentBranch + "；来源：" + status.sandboxBranch,
      "稳定目录：" + status.repoRoot, "实验目录：" + status.sandboxRoot,
      "已有实验提交：" + (status.commitsAhead ?? "未知") + " 个；待保存实验文件：" + status.sandboxDirtyProduct.length + " 个。",
      "本次会备份并精确恢复稳定区 " + status.stableDirtyProduct.length + " 项本地改动及其暂存状态。",
      "本次备份保留至你验收后处理；不自动删除任何 stash。其它稳定区本地改动不自动提交、不自动备份。",
      "出现冲突将停止，不自动选边；只有恢复和同步都成功才报告完成。",
      fixedHostContext ? "本入口仅操作已登记的隔离 Git 夹具；同步源码后仍运行当前固定发行，不会激活新程序。" : "成功后将同步实验分支，并将下次启动的主壳恢复到稳定地址；不会自动重启当前会话。",
      "不安装依赖、不启动候选、不调用模型；Git 合入不是 P1/H1/M1 或正式发行验收。",
      "待合入或保存的文件（整支实验提交）：", ...changed,
      "稳定区本次备份范围：", ...status.stableDirtyProduct,
    ].join("\n"),
    buttons: ["取消", "确认合入"], defaultId: 0, cancelId: 0, noLink: true,
  });
  return answer.response === 1 && !parent.isDestroyed() && workbenchWindow === parent;
});

ipcMain.handle("shell-dev:merge-sandbox", async (event) => {
  if (!assertShellDevWorkbenchSender(event)) return { ok: false, message: "仅允许稳定版工作台的可信主页面发起合入。" };
  const blocker = sandboxAdoptionHostBlocker({ mutableDevelopmentHost: Boolean(process.env.VITE_DEV_SERVER_URL), testHost: Boolean(evolutionTestHostContext), fixedHostVerified:Boolean(fixedHostContext) });
  if (blocker) return { ok: false, message: blocker };
  if(adoptionInProgress) return {ok:false,message:"合入确认或执行正在进行中。"};
  adoptionInProgress=true;
  try {
  const result = await requestSandboxAdoption();
  if (result.ok) {
    try {
      restoreStableShellPreference();
      if(fixedHostContext){result.message="源码已合入并同步；当前仍运行固定发行 "+fixedHostContext.binding.release.release_id+"，未激活新程序。";result.steps?.push("当前固定发行及数据未切换");}
      else result.steps?.push("下次启动已恢复稳定主壳；当前会话未自动重启");
    } catch (error) {
      result.ok = false;
      result.message = "代码合入及同步已完成，但稳定主壳设置未恢复；未报告完整成功。";
      result.detail = error instanceof Error ? error.message : String(error);
    }
  }
  showSandboxAdoptResultDialog(result);
  return result;
  } finally {adoptionInProgress=false;}
});

function restoreStableShellPreference(): void {
  clearSandboxPreviewPreference(STATE_PATH);
}

ipcMain.handle("shell-dev:reset-main", async (event) => {
  if (!assertShellDevWorkbenchSender(event) || evolutionTestHostContext) return { ok: false, message: "仅允许普通工作台操作。" };
  try {
    restoreStableShellPreference();
    if(fixedHostContext){fixedHostContext.revalidate();await loadRenderer(workbenchWindow!);return {ok:true,message:"已返回当前固定稳定工作台；未切换发行。"};}
    const mainUrl = defaultShellMainDevBase();
    if (process.env.VITE_DEV_SERVER_URL) await loadLegacyWorkbenchUrl(workbenchWindow!, mainUrl);
    return { ok: true, mainUrl, message: "主壳已恢复稳定版。" };
  } catch (error) { return { ok: false, message: error instanceof Error ? error.message : String(error) }; }
});

ipcMain.handle("shell-dev:reset-main-lane", async (event) => {
  if (!legacySandboxLaneAllowed() || !assertShellDevWorkbenchSender(event)) return { ok: false, message: "仅普通可变开发 Host 可重置旧主壳通道。" };
  try {
    restoreStableShellPreference();
    const mainUrl = defaultShellMainDevBase();
    if (workbenchWindow && !workbenchWindow.isDestroyed()) await loadLegacyWorkbenchUrl(workbenchWindow, mainUrl);
    return { ok: true, mainUrl, message: "主窗口已恢复稳定开发通道。" };
  } catch (error) { return { ok: false, message: error instanceof Error ? error.message : String(error) }; }
});

ipcMain.handle("shell-dev:sync-sandbox", async (event) => {
  if (!legacySandboxLaneAllowed() || !assertShellDevWorkbenchSender(event)) return { ok: false, message: "旧同步通道仅限普通可变开发 Host；固定/测试 Host 已禁用。" };
  return { ok: false, message: "旧 sandbox 同步实现未在选定源码合入流程中开放；请先进行受审查的源码审查、选择、检查点与单独合入。" };
});
ipcMain.handle("shell-dev:promote-preview", async (event) => {
  if(fixedHostContext) return {ok:false,message:"固定控制工作台不能采用可变预览 URL 作为可信主页面。"};
  const mainWindow = workbenchWindow;
  if (!mainWindow || mainWindow.isDestroyed() || !assertShellDevWorkbenchSender(event)) {
    return { ok: false, message: "workbench unavailable" };
  }
  const previewUrl = normalizeShellDevPreviewBase(shellDevPreviewBaseUrl || defaultShellDevPreviewBase());
  if (!previewUrl || !isAllowedShellRendererPromotion(previewUrl)) {
    return { ok: false, message: "预览地址无效" };
  }
  const previousMain =
    normalizeShellDevPreviewBase(mainWindow.webContents.getURL()) || defaultShellMainDevBase();
  try {
    persistShellRendererUrl(previewUrl);
    await loadLegacyWorkbenchUrl(mainWindow, previewUrl);
    const view = shellDevPreviewView;
    if (view && !view.webContents.isDestroyed() && previousMain !== previewUrl) {
      shellDevPreviewBaseUrl = previousMain;
      wireShellDevPreviewNavigation(view, previousMain);
      if (view.getVisible()) {
        await view.webContents.loadURL(previousMain).catch(() => {});
      }
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
        ...(fixedHostContext ? {} : {preload:preloadPath()}),
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
        ...(fixedHostContext ? {} : {preload:preloadPath()}),
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
  const dir = fixedHostContext ? path.join(DATA_ROOT,"paste") : path.join(os.tmpdir(), "my-agent-paste");
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
  return readAgentImageDataUrl(fixedHostContext?.binding.instance_root ?? activeSidecarRoot, rawPath.trim());
});

ipcMain.handle("clipboard:read-image-temp", async () => {
  const image = await readClipboardNativeImage();
  if (!image) return null;
  const dir = fixedHostContext ? path.join(DATA_ROOT,"paste") : path.join(os.tmpdir(), "my-agent-paste");
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
    if (evolutionTestHostContextError) throw evolutionTestHostContextError;
    if (evolutionTestHostContext) {
      const hostConfig = await buildEvolutionTestSupervisorConfig(evolutionTestHostContext);
      evolutionSupervisorHost = await createEvolutionSupervisorHost(hostConfig, { bootstrapRegistry: evolutionSidecarBootstraps });
    }
    await ensureSidecar();
    if (legacySandboxLaneAllowed()) void ensureSandboxSidecar().catch((error) => console.warn("[sandbox-sidecar] optional start failed:", error));
    if (evolutionTestHostContext) {
      evolutionTestRendererServer = await startEvolutionTestRendererServer(path.join(__dirname, "../dist"));
      if (evolutionTestHostContext.profile.candidate_fixture_profile === "t4-recovery-node-v1") {
        process.stdout.write(`${JSON.stringify({ type: "evolution-test-host-ready", host: sidecarInfo?.host ?? "127.0.0.1", port: sidecarInfo?.port ?? 0 })}\n`);
      }
    }
  } catch (err) {
    try {
      await closeEvolutionSupervisorHost();
    } catch (closeError) {
      console.error("[evolution] supervisor cleanup failed:", closeError);
    }
    try {
      await closeEvolutionTestRendererServer();
    } catch (closeError) {
      console.error("[evolution] renderer cleanup failed:", closeError);
    }
    stopSidecar();
    console.error("Failed to start Python sidecar or fixed test Host:", err);
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
  if(fixedHostContext){fixedHostContext.revalidate();process.stdout.write(JSON.stringify(fixedHostContext.readyFrame())+"\n");}
});

app.on("window-all-closed", () => {
  if (quitting && !evolutionHostShutdownInProgress) {
    finalizeAppExit();
  }
});

app.on("before-quit", (event) => {
  if(adoptionInProgress){event.preventDefault();return;}
  quitting = true;
  globalShortcut.unregisterAll();
  stopSandboxSidecar();
  stopSidecar();
  if (evolutionHostShutdownInProgress) {
    event.preventDefault();
    return;
  }
  if (evolutionSupervisorHost || evolutionTestRendererServer) {
    event.preventDefault();
    evolutionHostShutdownInProgress = true;
    void (async () => {
      try { await closeEvolutionTestRendererServer(); }
      catch (error) { console.error("[evolution] renderer server shutdown failed:", error); }
      try { await closeEvolutionSupervisorHost(); }
      catch (error) { console.error("[evolution] supervisor shutdown failed:", error); }
    })().finally(() => {
      evolutionSidecarBootstraps.clearAll();
      evolutionHostShutdownInProgress = false;
      app.quit();
    });
  }
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
});
