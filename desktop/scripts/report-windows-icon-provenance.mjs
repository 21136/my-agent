#!/usr/bin/env node
/**
 * Read-only: which PNG/ICO files drive tray, BrowserWindow, and PE-branded electron.exe.
 * For Windows manual acceptance — does not clear icon cache or touch running processes.
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildAppIconIco, resolveAppIconPngSources, sha256File } from "./build-app-icon-ico.mjs";
import { brandingPaths, readManifest } from "./windows-branded-electron-lib.mjs";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { primary, present, outIco } = resolveAppIconPngSources();
const { brandedDistDir, manifestPath } = brandingPaths(desktopDir);
const manifest = readManifest(manifestPath);

const runtimeOrder = [
  "build/icon-256.png",
  "build/icon-32.png",
  "public/icon-32.png",
  "build/icon.png",
  "build/app-icon.ico",
];

function rel(file) {
  return path.relative(desktopDir, file);
}

const report = {
  platform: process.platform,
  canonicalPng: primary ? { path: rel(primary), sha256: sha256File(primary) } : null,
  pngSourcesPresent: present.map((f) => ({ path: rel(f), sha256: sha256File(f) })),
  appIconIco: existsSync(outIco)
    ? { path: rel(outIco), sha256: sha256File(outIco), bytes: readFileSync(outIco).length }
    : null,
  runtimeCandidateOrder: runtimeOrder,
  runtimeExpectedFirst: "build/icon-256.png",
  brandedElectronManifest: manifest,
  brandedDistDir: existsSync(brandedDistDir) ? rel(brandedDistDir) : null,
  electronOverrideDistPath: process.env.ELECTRON_OVERRIDE_DIST_PATH ?? null,
  acceptance: {
    trayAndWindow: "NativeImage loads first existing file in runtime order (icon-256.png before app-icon.ico).",
    taskbarExe: "Shell uses branded electron.exe PE icons from app-icon.ico at ensure time.",
    visual: "Compare tray vs taskbar manually; this script only prints file paths and SHA256.",
    debugTray: "Set MY_AGENT_ICON_DEBUG=1 before dev to log runtime choice in Electron main log.",
  },
};

console.log(JSON.stringify(report, null, 2));