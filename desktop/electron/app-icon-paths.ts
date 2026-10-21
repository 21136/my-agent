import path from "node:path";
import { fileURLToPath } from "node:url";

const electronDir = path.dirname(fileURLToPath(import.meta.url));
export const desktopRoot = path.resolve(electronDir, "..");
export const repoRoot = path.resolve(desktopRoot, "..");

/** Prefer smaller assets — multi‑MB PNG often fails on Windows taskbar/tray. */
export const APP_ICON_CANDIDATE_FILES = [
  path.join(desktopRoot, "build", "icon-256.png"),
  path.join(desktopRoot, "build", "icon-32.png"),
  path.join(desktopRoot, "public", "icon-32.png"),
  path.join(desktopRoot, "build", "icon.png"),
  path.join(repoRoot, "desktop", "build", "icon-256.png"),
  path.join(repoRoot, "desktop", "build", "icon-32.png"),
];
