import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

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

export function resolveStockElectronExe(desktopDir, requireFn) {
  const electronPkg = path.join(desktopDir, "node_modules", "electron", "index.js");
  if (!existsSync(electronPkg) || typeof requireFn !== "function") return null;
  const exePath = requireFn(electronPkg);
  if (typeof exePath !== "string" || !existsSync(exePath)) return null;
  return { exePath, distDir: path.dirname(exePath) };
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