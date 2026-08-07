declare global {
  namespace NodeJS {
    interface Process {
      electronApp?: import("node:child_process").ChildProcess;
    }
  }
}

import { execSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { defineConfig } from "vite";
import electron from "vite-plugin-electron/simple";

const repoRoot = path.resolve(__dirname, "..");
const localCacheRoot = path.join(
  process.env.LOCALAPPDATA || process.env.TEMP || os.tmpdir(),
  "my-agent-desktop",
);

/** Paths that must not wake the renderer dev server (sidecar / agent / electron artifacts). */
const devWatchIgnored = [
  "**/node_modules/**",
  "**/dist-electron/**",
  path.join(__dirname, "dist-electron/**"),
  path.join(__dirname, "mockups/**"),
  path.join(repoRoot, "agent-core/**"),
  path.join(repoRoot, "data/**"),
  path.join(repoRoot, "evolve/**"),
  path.join(repoRoot, "workspace/**"),
  path.join(repoRoot, "_trash/**"),
  path.join(repoRoot, "docs/**"),
];

/** Electron sub-builds only watch main/preload — not renderer `src/`. */
const electronBuildWatch = {
  emptyOutDir: false as const,
  watch: {
    exclude: [
      "**/node_modules/**",
      path.join(__dirname, "src/**"),
      path.join(__dirname, "index.html"),
      path.join(__dirname, "pet.html"),
      path.join(__dirname, "mockups/**"),
      path.join(__dirname, "dist-electron/**"),
      path.join(__dirname, "vite.config.ts"),
      ...devWatchIgnored,
    ],
  },
};
/** Must match `DEV_USER_QUIT_CODE` in electron/main.ts — user closed the window. */
const DEV_USER_QUIT_CODE = 100;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Kill the prior Electron tree (Windows needs /T or cache locks linger on USB roots). */
function killElectronApp(): void {
  const proc = process.electronApp;
  if (!proc || proc.killed) {
    process.electronApp = undefined;
    return;
  }
  const pid = proc.pid;
  process.electronApp = undefined;
  proc.removeAllListeners();
  if (pid == null) return;
  try {
    if (process.platform === "win32") {
      execSync(`taskkill /PID ${pid} /T /F`, { stdio: "ignore" });
    } else {
      process.kill(pid, "SIGTERM");
    }
  } catch {
    /* already exited */
  }
}

let electronLaunchTimer: ReturnType<typeof setTimeout> | null = null;
let electronLaunching = false;

function launchElectron(startup: (argv?: string[]) => Promise<void>): void {
  if (electronLaunchTimer) clearTimeout(electronLaunchTimer);
  electronLaunchTimer = setTimeout(() => void runElectron(startup), 900);
}

async function runElectron(startup: (argv?: string[]) => Promise<void>): Promise<void> {
  if (electronLaunching) return;
  electronLaunching = true;
  let restartTimer: ReturnType<typeof setTimeout> | null = null;

  const scheduleRestart = (delayMs: number): void => {
    if (restartTimer) clearTimeout(restartTimer);
    restartTimer = setTimeout(() => void runElectron(startup), delayMs);
  };

  try {
    killElectronApp();
    await sleep(400);
    await startup([".", "--no-sandbox", "--disable-gpu"]);
    const appProc = process.electronApp;
    if (!appProc) {
      scheduleRestart(2000);
      return;
    }
    appProc.removeAllListeners("exit");
    appProc.once("exit", (code, signal) => {
      if (code === DEV_USER_QUIT_CODE) {
        console.log("[electron] user quit; shutting down dev server.");
        process.exit(0);
        return;
      }
      if (code === 0 && signal === null) {
        console.log("[electron] exited cleanly; dev server keeps running.");
        return;
      }
      console.warn(
        `[electron] exited (code=${code ?? "?"}, signal=${signal ?? "none"}), restarting in 2s…`,
      );
      scheduleRestart(2000);
    });
  } catch (err) {
    console.error("[electron] failed to start:", err);
    scheduleRestart(2000);
  } finally {
    electronLaunching = false;
  }
}

export default defineConfig({
  cacheDir: path.join(localCacheRoot, "vite-cache"),
  plugins: [
    electron({
      main: {
        entry: "electron/main.ts",
        vite: {
          build: electronBuildWatch,
        },
        onstart({ startup }) {
          launchElectron(startup);
        },
      },
      preload: {
        input: "electron/preload.ts",
        vite: {
          build: electronBuildWatch,
        },
      },
      renderer: {},
    }),
  ],
  build: {
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, "index.html"),
        pet: path.resolve(__dirname, "pet.html"),
      },
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    watch: {
      ignored: devWatchIgnored,
    },
  },
});
