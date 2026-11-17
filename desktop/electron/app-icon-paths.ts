import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const electronDir = path.dirname(fileURLToPath(import.meta.url));
export const desktopRoot = path.resolve(electronDir, "..");
export const repoRoot = path.resolve(desktopRoot, "..");

export const APP_ICON_BUILD_DIR = path.join(desktopRoot, "build");
export const APP_ICON_ICO_PATH = path.join(APP_ICON_BUILD_DIR, "app-icon.ico");
export const APP_ICON_CANONICAL_PNG = path.join(APP_ICON_BUILD_DIR, "icon-256.png");
export const APP_ICON_SMALL_PNG = path.join(APP_ICON_BUILD_DIR, "icon-32.png");
export const APP_ICON_PUBLIC_SMALL_PNG = path.join(desktopRoot, "public", "icon-32.png");

/**
 * Tray / BrowserWindow runtime: prefer full-resolution PNG (pre–PR#117 behavior).
 * `app-icon.ico` is for PE embedding in branded electron.exe, not first for NativeImage.
 */
export const APP_ICON_RUNTIME_CANDIDATE_FILES = [
  APP_ICON_CANONICAL_PNG,
  APP_ICON_SMALL_PNG,
  APP_ICON_PUBLIC_SMALL_PNG,
  path.join(APP_ICON_BUILD_DIR, "icon.png"),
  path.join(repoRoot, "desktop", "build", "icon-256.png"),
  path.join(repoRoot, "desktop", "build", "icon-32.png"),
  APP_ICON_ICO_PATH,
];

/** @deprecated use APP_ICON_RUNTIME_CANDIDATE_FILES */
export const APP_ICON_CANDIDATE_FILES = APP_ICON_RUNTIME_CANDIDATE_FILES;

export function resolveFirstExistingIconFile(
  candidates: readonly string[] = APP_ICON_RUNTIME_CANDIDATE_FILES,
): string | null {
  for (const file of candidates) {
    if (existsSync(file)) return file;
  }
  return null;
}