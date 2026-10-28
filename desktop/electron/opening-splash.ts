import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { BrowserWindow } from "electron";
import { workbenchDefaultWindowSize } from "./workbench-window-defaults";

/** Approved cold-start splash clip (Neon Twin · 小魔王). Served from Vite `public/`. */
export const OPENING_SPLASH_VIDEO_BASENAME = "splash-xiaomowang.mp4";

const SPLASH_MAX_WAIT_MS = 12_000;

const electronDir = path.dirname(fileURLToPath(import.meta.url));
const distRoot = path.join(electronDir, "../dist");
const publicRoot = path.join(electronDir, "../public");

let splashWindow: BrowserWindow | null = null;
let activeSplashFinish: (() => void) | null = null;

export function openingSplashVideoPath(): string {
  const distVideo = path.join(distRoot, OPENING_SPLASH_VIDEO_BASENAME);
  if (existsSync(distVideo)) return distVideo;
  const publicVideo = path.join(publicRoot, OPENING_SPLASH_VIDEO_BASENAME);
  if (existsSync(publicVideo)) return publicVideo;
  return distVideo;
}

export function openingSplashPageHref(): string {
  const devBase = process.env.VITE_DEV_SERVER_URL?.replace(/\/$/, "");
  if (devBase) return `${devBase}/splash.html`;
  return pathToFileURL(path.join(distRoot, "splash.html")).href;
}

export function openingSplashEnabled(evolutionTestHost: boolean): boolean {
  if (evolutionTestHost) return false;
  if (process.env.MY_AGENT_SKIP_OPENING_SPLASH === "1") return false;
  return existsSync(openingSplashVideoPath());
}

export async function dismissOpeningSplash(): Promise<void> {
  activeSplashFinish?.();
}

async function waitForSplashPlayback(win: BrowserWindow): Promise<void> {
  if (win.webContents.isDestroyed()) return;
  await win.webContents.executeJavaScript(
    `new Promise((resolve) => {
      const video = document.getElementById("splash-video");
      if (!(video instanceof HTMLVideoElement)) {
        resolve();
        return;
      }
      const finish = () => resolve();
      video.addEventListener("error", finish, { once: true });
      video.addEventListener(
        "ended",
        () => {
          try {
            video.pause();
          } catch {
            /* hold last frame until main dismisses splash */
          }
        },
        { once: true },
      );
      void video.play().catch(finish);
    })`,
    true,
  );
}

export function playOpeningSplash(): Promise<void> {
  return new Promise((resolve) => {
    let settled = false;
    let maxWait: ReturnType<typeof setTimeout> | undefined;

    const finish = () => {
      if (settled) return;
      settled = true;
      activeSplashFinish = null;
      if (maxWait !== undefined) clearTimeout(maxWait);
      const win = splashWindow;
      splashWindow = null;
      if (win && !win.isDestroyed()) win.close();
      resolve();
    };

    activeSplashFinish = finish;
    maxWait = setTimeout(finish, SPLASH_MAX_WAIT_MS);

    const { width, height } = workbenchDefaultWindowSize();
    const win = new BrowserWindow({
      width,
      height,
      frame: false,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      center: true,
      show: false,
      alwaysOnTop: true,
      backgroundColor: "#05060a",
      autoHideMenuBar: true,
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });

    splashWindow = win;

    win.once("ready-to-show", () => {
      if (!win.isDestroyed()) win.show();
    });

    void win
      .loadURL(openingSplashPageHref())
      .then(() => waitForSplashPlayback(win))
      .catch(() => undefined);
  });
}
