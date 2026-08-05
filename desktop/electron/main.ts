import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import net from "node:net";
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  app,
  BrowserWindow,
  dialog,
  globalShortcut,
  ipcMain,
  Menu,
  nativeImage,
  screen,
  shell,
  Tray,
} from "electron";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const AGENT_ROOT = path.resolve(__dirname, "../..");
const APP_ICON = path.join(AGENT_ROOT, "desktop", "build", "icon.png");
const STATE_PATH = path.join(AGENT_ROOT, "data", "state.json");
const CONSTELLATION_PATH = path.join(AGENT_ROOT, "data", "constellation.json");
const PET_COLLAPSED = { width: 128, height: 128 };
const PET_EXPANDED = { width: 340, height: 480 };
const PET_MARGIN = 16;
const SHOW_SHORTCUT = "CommandOrControl+Shift+M";
const DEFAULT_WS_PORT = Number(process.env.MY_AGENT_WS_PORT ?? "8765") || 8765;
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

let petWindow: BrowserWindow | null = null;
let workbenchWindow: BrowserWindow | null = null;
let sidecar: ChildProcess | null = null;
let sidecarInfo: { host: string; port: number } | null = null;
let tray: Tray | null = null;
let quitting = false;
let quitInProgress = false;

let sidecarRestarting = false;
let sidecarOwned = false;

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

// Windows dev: reduce native GPU crashes (exit code 0xC0000005) when tray + Chromium init.
if (process.platform === "win32" && process.env.VITE_DEV_SERVER_URL) {
  app.disableHardwareAcceleration();
}

function pythonCommand(): string {
  return process.platform === "win32" ? "python" : "python3";
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
  if (!existsSync(APP_ICON)) {
    return nativeImage.createEmpty();
  }
  return nativeImage.createFromPath(APP_ICON);
}

function buildTray(): void {
  if (tray) return;
  tray = new Tray(trayIcon());
  tray.setToolTip("my-agent");
  const menu = Menu.buildFromTemplate([
    { label: "打开工作台", click: () => void openWorkbenchWindow() },
    { label: "显示伴侶", click: () => void showPetWindow() },
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

function startSidecar(takeover = false): Promise<SidecarStartResult> {
  return new Promise((resolve, reject) => {
    const script = path.join(AGENT_ROOT, "agent-core", "server.py");
    const args = [script, "--port", String(DEFAULT_WS_PORT)];
    if (takeover) {
      args.push("--takeover");
    }
    sidecar = spawn(pythonCommand(), args, {
      cwd: AGENT_ROOT,
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

async function ensureSidecar(): Promise<void> {
  if (!sidecarOwned && (await waitForPortOpen("127.0.0.1", DEFAULT_WS_PORT))) {
    console.log(`[sidecar] reusing listener on 127.0.0.1:${DEFAULT_WS_PORT}`);
    sidecarInfo = { host: "127.0.0.1", port: DEFAULT_WS_PORT };
    setPreferredUi("electron");
    return;
  }

  let result = await startSidecar(false);
  if (!result.ok && result.lockConflict) {
    const uiLabel = result.lockConflict.ui === "cli" ? "终端 REPL" : "Electron 桌面";
    const { response } = await dialog.showMessageBox({
      type: "warning",
      title: "会话被占用",
      message: `${uiLabel} 正在占用会话 (pid ${result.lockConflict.pid})`,
      detail: "是否让桌面壳接管会话？接管后终端将无法继续写入同一 session。",
      buttons: ["接管会话", "退出"],
      defaultId: 0,
      cancelId: 1,
    });
    if (response !== 0) {
      quitting = true;
      finalizeAppExit();
      return;
    }
    stopSidecar();
    result = await startSidecar(true);
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

async function isAgentBusy(): Promise<boolean> {
  const checks: Promise<boolean>[] = [];
  for (const win of [petWindow, workbenchWindow]) {
    if (!win || win.isDestroyed()) continue;
    checks.push(
      win.webContents.executeJavaScript("Boolean(window.__myAgentIsBusy?.())", true).catch(() => false),
    );
  }
  if (!checks.length) {
    return false;
  }
  const results = await Promise.all(checks);
  return results.some(Boolean);
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
        (workbenchWindow && !workbenchWindow.isDestroyed() ? workbenchWindow : null) ??
        (petWindow && !petWindow.isDestroyed() ? petWindow : null) ??
        undefined;
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
  for (const win of [petWindow, workbenchWindow]) {
    if (win && !win.isDestroyed()) {
      win.removeAllListeners("close");
      win.close();
    }
  }
  petWindow = null;
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
    stopSidecar();
    await ensureSidecar();
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

function petBounds(mode: "collapsed" | "expanded"): { x: number; y: number; width: number; height: number } {
  const { workArea } = screen.getPrimaryDisplay();
  const size = mode === "expanded" ? PET_EXPANDED : PET_COLLAPSED;
  return {
    x: workArea.x + workArea.width - size.width - PET_MARGIN,
    y: workArea.y + workArea.height - size.height - PET_MARGIN,
    width: size.width,
    height: size.height,
  };
}

function sendSessionControl(win: BrowserWindow | null, action: "suspend" | "resume"): void {
  if (!win || win.isDestroyed()) return;
  win.webContents.send("session:control", action);
}

async function loadRenderer(win: BrowserWindow, page: "pet" | "workbench"): Promise<void> {
  if (process.env.VITE_DEV_SERVER_URL) {
    const suffix = page === "pet" ? "/pet.html" : "/";
    await win.loadURL(`${process.env.VITE_DEV_SERVER_URL}${suffix}`);
    return;
  }
  const file = page === "pet" ? "pet.html" : "index.html";
  await win.loadFile(path.join(__dirname, "../dist", file));
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

async function createPetWindow(): Promise<void> {
  if (petWindow && !petWindow.isDestroyed()) {
    return;
  }

  const bounds = petBounds("collapsed");
  petWindow = new BrowserWindow({
    ...bounds,
    title: "my-agent · 伴侶",
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    hasShadow: false,
    focusable: true,
    show: false,
    backgroundColor: "#00000000",
    icon: existsSync(APP_ICON) ? APP_ICON : undefined,
    webPreferences: {
      preload: preloadPath(),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  petWindow.setIgnoreMouseEvents(true, { forward: true });
  petWindow.setAlwaysOnTop(true, "screen-saver");
  if (process.platform === "darwin") {
    petWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  } else if (process.platform === "linux") {
    petWindow.setVisibleOnAllWorkspaces(true);
  }

  petWindow.on("close", (event) => {
    if (quitting) return;
    event.preventDefault();
    petWindow?.hide();
  });

  await loadRenderer(petWindow, "pet");
  wireExternalNavigation(petWindow);

  petWindow.on("closed", () => {
    petWindow = null;
  });
}

async function createWorkbenchWindow(): Promise<void> {
  if (workbenchWindow && !workbenchWindow.isDestroyed()) {
    return;
  }

  workbenchWindow = new BrowserWindow({
    width: 960,
    height: 720,
    minWidth: 640,
    minHeight: 480,
    title: "my-agent",
    autoHideMenuBar: true,
    backgroundColor: windowBackground(),
    icon: existsSync(APP_ICON) ? APP_ICON : undefined,
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

  await loadRenderer(workbenchWindow, "workbench");
  wireExternalNavigation(workbenchWindow);

  workbenchWindow.on("closed", () => {
    workbenchWindow = null;
  });
}

async function showPetWindow(): Promise<void> {
  try {
    if (!sidecar || sidecar.killed || !sidecarInfo) {
      await ensureSidecar();
    }
    let petNeedsResume = false;
    if (workbenchWindow && !workbenchWindow.isDestroyed() && workbenchWindow.isVisible()) {
      sendSessionControl(workbenchWindow, "suspend");
      workbenchWindow.hide();
    }
    if (petWindow && !petWindow.isDestroyed() && !petWindow.isVisible()) {
      petNeedsResume = true;
    }
    await createPetWindow();
    if (petNeedsResume) {
      sendSessionControl(petWindow, "resume");
    }
    petWindow?.show();
    petWindow?.focus();
  } catch (err) {
    await dialog.showMessageBox({
      type: "error",
      title: "my-agent",
      message: "无法显示伴侶",
      detail: err instanceof Error ? err.message : String(err),
    });
  }
}

async function openWorkbenchWindow(): Promise<void> {
  try {
    if (!sidecar || sidecar.killed || !sidecarInfo) {
      await ensureSidecar();
    }
    let workbenchNeedsResume = false;
    if (petWindow && !petWindow.isDestroyed() && petWindow.isVisible()) {
      sendSessionControl(petWindow, "suspend");
      petWindow.hide();
    }
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
  petWindow?.hide();
}

function registerShortcuts(): void {
  if (!globalShortcut.register(SHOW_SHORTCUT, () => void openWorkbenchWindow())) {
    console.warn(`Failed to register shortcut ${SHOW_SHORTCUT}`);
  }
}

ipcMain.handle("sidecar:get", () => sidecarInfo);
ipcMain.handle("app:switch-to-cli", () => switchToCli());
ipcMain.handle("app:open-external", async (_event, raw: unknown) => {
  if (typeof raw !== "string" || !raw.trim()) return false;
  const url = raw.trim();
  if (!/^https?:\/\//i.test(url)) return false;
  await shell.openExternal(url);
  return true;
});

ipcMain.on("pet:set-ignore-mouse-events", (_event, ignore: unknown) => {
  if (!petWindow || petWindow.isDestroyed()) return;
  if (ignore) {
    petWindow.setIgnoreMouseEvents(true, { forward: true });
  } else {
    petWindow.setIgnoreMouseEvents(false);
  }
});

ipcMain.handle("pet:set-bounds", (_event, mode: unknown) => {
  if (!petWindow || petWindow.isDestroyed()) return;
  const next = mode === "expanded" ? "expanded" : "collapsed";
  petWindow.setBounds(petBounds(next));
});

ipcMain.handle("dialog:pick-directory", async () => {
  const parent =
    (workbenchWindow && !workbenchWindow.isDestroyed() ? workbenchWindow : null) ??
    (petWindow && !petWindow.isDestroyed() ? petWindow : null) ??
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
  applyApplicationMenu();
  buildTray();
  registerShortcuts();

  try {
    await ensureSidecar();
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
  stopSidecar();
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
});
