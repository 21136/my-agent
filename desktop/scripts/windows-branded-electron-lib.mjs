import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";

export const BRANDING_DIR_NAME = ".branding";
export const BRANDED_DIST_DIRNAME = "electron-dist";
export const MANIFEST_FILENAME = "branded-electron.manifest.json";
export const APP_USER_MODEL_ID = "my-agent.desktop";
export const VERSION_STRINGS = {
  FileDescription: "my-agent",
  ProductName: "my-agent",
  OriginalFilename: "my-agent.exe",
};

export function sha256File(file) {
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

export function brandingPaths(desktopDir) {
  const brandingRoot = path.join(desktopDir, BRANDING_DIR_NAME);
  const brandedDistDir = path.join(brandingRoot, BRANDED_DIST_DIRNAME);
  const manifestPath = path.join(brandingRoot, MANIFEST_FILENAME);
  return { brandingRoot, brandedDistDir, manifestPath };
}

/** Official npm electron dist only — never follows ELECTRON_OVERRIDE_DIST_PATH. */
export function resolveStockElectronDist(desktopDir) {
  const electronRoot = path.join(desktopDir, "node_modules", "electron");
  const distDir = path.join(electronRoot, "dist");
  if (!existsSync(distDir)) return null;
  let baseName = "electron";
  const pathFile = path.join(electronRoot, "path.txt");
  if (existsSync(pathFile)) {
    try {
      const raw = readFileSync(pathFile, "utf8").trim();
      if (raw) baseName = raw;
    } catch {
      /* keep default */
    }
  }
  const exeName =
    process.platform === "win32" && !baseName.toLowerCase().endsWith(".exe")
      ? `${baseName}.exe`
      : baseName;
  const exePath = path.join(distDir, exeName);
  if (!existsSync(exePath)) return null;
  return { exePath, distDir };
}

export function resolveStockElectronExe(desktopDir, requireFn) {
  void requireFn;
  return resolveStockElectronDist(desktopDir);
}

export function assertDistinctDistCopy({ stockDistDir, brandedDistDir }) {
  if (path.resolve(stockDistDir) === path.resolve(brandedDistDir)) {
    throw new Error(
      "branded electron dist equals stock dist; refusing self-copy "
      + "(likely ELECTRON_OVERRIDE_DIST_PATH leaked into stock resolution)",
    );
  }
}

export function readManifest(manifestPath) {
  if (!existsSync(manifestPath)) return null;
  try {
    return JSON.parse(readFileSync(manifestPath, "utf8"));
  } catch {
    return null;
  }
}

export function brandingIsFresh({ manifest, sourceExeSha256, icoSha256 }) {
  if (!manifest || manifest.sourceExeSha256 !== sourceExeSha256) return false;
  if (manifest.icoSha256 !== icoSha256) return false;
  if (manifest.appUserModelId !== APP_USER_MODEL_ID) return false;
  const brandedExe = manifest.brandedExePath;
  return typeof brandedExe === "string" && existsSync(brandedExe);
}

export function envOverridePath(brandedDistDir) {
  return brandedDistDir;
}