#!/usr/bin/env node
/**
 * Windows stable dev: branded copy of Electron dist (project-local, not node_modules).
 * Sets ELECTRON_OVERRIDE_DIST_PATH so taskbar uses my-agent icon, not stock Electron.
 */
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import * as PELibrary from "pe-library";
import * as ResEdit from "resedit";
import { buildAppIconIco } from "./build-app-icon-ico.mjs";
import {
  APP_USER_MODEL_ID,
  VERSION_STRINGS,
  brandingIsFresh,
  brandingPaths,
  envOverridePath,
  readManifest,
  assertDistinctDistCopy,
  resolveStockElectronDist,
  sha256File,
} from "./windows-branded-electron-lib.mjs";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function patchExecutableIcon(exePath, icoPath) {
  const data = readFileSync(exePath);
  const exe = PELibrary.NtExecutable.from(data, { ignoreCert: true });
  const res = PELibrary.NtExecutableResource.from(exe);
  const iconFile = ResEdit.Data.IconFile.from(readFileSync(icoPath));
  const groups = ResEdit.Resource.IconGroupEntry.fromEntries(res.entries);
  if (!groups.length) {
    throw new Error(`no icon resource groups in ${exePath}`);
  }
  for (const group of groups) {
    ResEdit.Resource.IconGroupEntry.replaceIconsForResource(
      res.entries,
      group.id,
      group.lang ?? 1033,
      iconFile.icons.map((item) => item.data),
    );
  }
  const viList = ResEdit.Resource.VersionInfo.fromEntries(res.entries);
  const vi = viList[0];
  if (vi) {
    vi.setFileVersion(0, 1, 0, 0, 1033);
    vi.setStringValues(
      { lang: 1033, codepage: 1200 },
      VERSION_STRINGS,
    );
    vi.outputToResourceEntries(res.entries);
  }
  res.outputResource(exe);
  writeFileSync(exePath, Buffer.from(exe.generate()));
}

export async function ensureWindowsBrandedElectron(desktopRoot = desktopDir) {
  if (process.platform !== "win32") {
    return { ok: true, skipped: "non-win32", overridePath: null };
  }
  const stock = resolveStockElectronDist(desktopRoot);
  if (!stock) {
    return { ok: false, skipped: "electron-missing", overridePath: null };
  }
  const { outIco } = await buildAppIconIco();
  const icoSha256 = sha256File(outIco);
  const sourceExeSha256 = sha256File(stock.exePath);
  const { brandingRoot, brandedDistDir, manifestPath } = brandingPaths(desktopRoot);
  const manifest = readManifest(manifestPath);
  const brandedExe = path.join(brandedDistDir, path.basename(stock.exePath));

  if (brandingIsFresh({ manifest, sourceExeSha256, icoSha256 })) {
    return { ok: true, skipped: "fresh", overridePath: envOverridePath(brandedDistDir), brandedExe };
  }

  assertDistinctDistCopy({ stockDistDir: stock.distDir, brandedDistDir });
  mkdirSync(brandingRoot, { recursive: true });
  if (existsSync(brandedDistDir)) {
    rmSync(brandedDistDir, { recursive: true, force: true });
  }
  cpSync(stock.distDir, brandedDistDir, { recursive: true });
  patchExecutableIcon(brandedExe, outIco);

  const canonicalPng = path.join(desktopRoot, "build", "icon-256.png");
  const nextManifest = {
    appUserModelId: APP_USER_MODEL_ID,
    sourceExeSha256,
    icoSha256,
    canonicalPngSha256: existsSync(canonicalPng) ? sha256File(canonicalPng) : null,
    runtimeIconPolicy: "icon-256-first",
    sourceDistDir: stock.distDir,
    brandedDistDir,
    brandedExePath: brandedExe,
    brandedAt: new Date().toISOString(),
  };
  writeFileSync(manifestPath, `${JSON.stringify(nextManifest, null, 2)}\n`, "utf8");
  return { ok: true, skipped: false, overridePath: envOverridePath(brandedDistDir), brandedExe };
}

export function applyElectronOverrideEnv(overridePath) {
  if (!overridePath) return;
  process.env.ELECTRON_OVERRIDE_DIST_PATH = overridePath;
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

if (isMain) {
  ensureWindowsBrandedElectron()
    .then((result) => {
      if (result.overridePath) {
        applyElectronOverrideEnv(result.overridePath);
        console.log(`[branded-electron] ELECTRON_OVERRIDE_DIST_PATH=${result.overridePath}`);
      } else if (result.skipped === "non-win32") {
        console.log("[branded-electron] skipped (not Windows)");
      } else if (result.skipped === "electron-missing") {
        console.warn("[branded-electron] node_modules/electron not ready — run npm install in desktop/");
        process.exitCode = 1;
      } else {
        console.log(`[branded-electron] ready (${result.skipped ?? "rebuilt"})`);
      }
    })
    .catch((err) => {
      console.error("[branded-electron]", err instanceof Error ? err.stack ?? err.message : err);
      process.exitCode = 1;
    });
}