declare global {
  namespace NodeJS {
    interface Process {
      electronApp?: import("node:child_process").ChildProcess;
    }
  }
}

import path from "node:path";
import { defineConfig } from "vite";
import electron from "vite-plugin-electron/simple";

const repoRoot = path.resolve(__dirname, "..");
/** Must match `DEV_USER_QUIT_CODE` in electron/main.ts — user closed the window. */
const DEV_USER_QUIT_CODE = 100;

/** Drop crashed ChildProcess before plugin startup() — otherwise treeKillSync taskkills a gone PID on Windows. */
function clearStaleElectronApp(): void {
  const proc = process.electronApp;
  if (!proc) return;
  process.electronApp = undefined;
  proc.removeAllListeners();
}

function launchElectron(startup: (argv?: string[]) => Promise<void>): void {
  let restartTimer: ReturnType<typeof setTimeout> | null = null;

  const scheduleRestart = (delayMs: number): void => {
    if (restartTimer) clearTimeout(restartTimer);
    restartTimer = setTimeout(() => void run(), delayMs);
  };

  const run = async () => {
    clearStaleElectronApp();
    try {
      await startup([".", "--no-sandbox", "--disable-gpu"]);
    } catch (err) {
      console.error("[electron] failed to start:", err);
      scheduleRestart(2000);
      return;
    }
    const appProc = process.electronApp;
    if (!appProc) {
      scheduleRestart(2000);
      return;
    }
    // vite-plugin-electron attaches process.exit — keep dev server alive on crash.
    appProc.removeAllListeners("exit");
    appProc.once("exit", (code, signal) => {
      if (code === DEV_USER_QUIT_CODE) {
        console.log("[electron] user quit; shutting down dev server.");
        process.exit(0);
        return;
      }
      // Other clean exits (e.g. main-process reload) — keep dev server, do not respawn.
      if (code === 0 && signal === null) {
        console.log("[electron] exited cleanly; dev server keeps running.");
        return;
      }
      console.warn(
        `[electron] exited (code=${code ?? "?"}, signal=${signal ?? "none"}), restarting in 1.5s…`,
      );
      scheduleRestart(1500);
    });
  };
  void run();
}

export default defineConfig({
  plugins: [
    electron({
      main: {
        entry: "electron/main.ts",
        onstart({ startup }) {
          launchElectron(startup);
        },
      },
      preload: {
        input: "electron/preload.ts",
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
    // 5173 被其它 Vite 项目占用时自动尝试 5174、5175…
    strictPort: false,
    watch: {
      ignored: [
        "**/node_modules/**",
        path.join(repoRoot, "agent-core/**"),
        path.join(repoRoot, "data/**"),
        path.join(repoRoot, "evolve/**"),
      ],
    },
  },
});
