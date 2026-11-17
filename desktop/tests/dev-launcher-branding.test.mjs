import assert from "node:assert/strict";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { test, afterEach } from "node:test";
import { fileURLToPath } from "node:url";

const desktopPackageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
import {
  APP_USER_MODEL_ID,
  assertDistinctDistCopy,
  brandingIsFresh,
  brandingPaths,
  resolveStockElectronDist,
  sha256File,
} from "../scripts/windows-branded-electron-lib.mjs";
import { applyElectronOverrideEnv } from "../scripts/ensure-windows-branded-electron.mjs";

const prevOverride = process.env.ELECTRON_OVERRIDE_DIST_PATH;

afterEach(() => {
  if (prevOverride === undefined) {
    delete process.env.ELECTRON_OVERRIDE_DIST_PATH;
  } else {
    process.env.ELECTRON_OVERRIDE_DIST_PATH = prevOverride;
  }
});

function seedStockElectronDist(desktopDir) {
  const distDir = path.join(desktopDir, "node_modules", "electron", "dist");
  mkdirSync(distDir, { recursive: true });
  const exeBase = process.platform === "win32" ? "electron.exe" : "electron";
  const exePath = path.join(distDir, exeBase);
  writeFileSync(exePath, "fake-electron-binary");
  writeFileSync(path.join(desktopDir, "node_modules", "electron", "path.txt"), exeBase.replace(/\.exe$/i, ""));
  return { distDir, exePath };
}

test("resolveStockElectronDist uses node_modules even when override points at branded dist", () => {
  const desktopDir = mkdtempSync(path.join(os.tmpdir(), "my-agent-launcher-"));
  const { distDir } = seedStockElectronDist(desktopDir);
  const { brandedDistDir } = brandingPaths(desktopDir);
  process.env.ELECTRON_OVERRIDE_DIST_PATH = brandedDistDir;

  const stock = resolveStockElectronDist(desktopDir);
  assert.ok(stock);
  assert.equal(path.resolve(stock.distDir), path.resolve(distDir));
  assert.notEqual(path.resolve(stock.distDir), path.resolve(brandedDistDir));
});

test("assertDistinctDistCopy blocks self-copy (bat override + mistaken stock resolution)", () => {
  const desktopDir = mkdtempSync(path.join(os.tmpdir(), "my-agent-selfcopy-"));
  const { brandedDistDir } = brandingPaths(desktopDir);
  assert.throws(
    () => assertDistinctDistCopy({ stockDistDir: brandedDistDir, brandedDistDir }),
    /refusing self-copy/,
  );
});

test("launcher chain: double resolve with override between calls stays on stock dist", () => {
  const desktopDir = mkdtempSync(path.join(os.tmpdir(), "my-agent-double-"));
  seedStockElectronDist(desktopDir);
  const { brandedDistDir } = brandingPaths(desktopDir);

  const first = resolveStockElectronDist(desktopDir);
  assert.ok(first);
  process.env.ELECTRON_OVERRIDE_DIST_PATH = brandedDistDir;
  const second = resolveStockElectronDist(desktopDir);
  assert.ok(second);
  assert.equal(path.resolve(first.distDir), path.resolve(second.distDir));
});

function simulateEnsureCopyFromStock(desktopDir) {
  const stock = resolveStockElectronDist(desktopDir);
  assert.ok(stock);
  const { brandingRoot, brandedDistDir } = brandingPaths(desktopDir);
  assertDistinctDistCopy({ stockDistDir: stock.distDir, brandedDistDir });
  mkdirSync(brandingRoot, { recursive: true });
  if (existsSync(brandedDistDir)) {
    rmSync(brandedDistDir, { recursive: true, force: true });
  }
  cpSync(stock.distDir, brandedDistDir, { recursive: true });
  return { stock, brandedDistDir };
}

test("launcher: first ensure copy then override then second ensure copy (npm dev inherit)", () => {
  const desktopDir = mkdtempSync(path.join(os.tmpdir(), "my-agent-launcher-chain-"));
  seedStockElectronDist(desktopDir);

  const first = simulateEnsureCopyFromStock(desktopDir);
  applyElectronOverrideEnv(first.brandedDistDir);
  assert.equal(process.env.ELECTRON_OVERRIDE_DIST_PATH, first.brandedDistDir);

  const second = simulateEnsureCopyFromStock(desktopDir);
  assert.ok(existsSync(path.join(second.brandedDistDir, path.basename(second.stock.exePath))));
});

test("launcher: manifest left after branded dist deleted rebuilds from stock with override preset", () => {
  const desktopDir = mkdtempSync(path.join(os.tmpdir(), "my-agent-manifest-only-"));
  const { distDir, exePath } = seedStockElectronDist(desktopDir);
  const icoPath = path.join(desktopDir, "build", "app-icon.ico");
  mkdirSync(path.dirname(icoPath), { recursive: true });
  const repoIco = path.join(desktopPackageRoot, "build", "app-icon.ico");
  writeFileSync(icoPath, readFileSync(repoIco));

  const { brandingRoot, brandedDistDir, manifestPath } = brandingPaths(desktopDir);
  const sourceExeSha256 = sha256File(exePath);
  const icoSha256 = sha256File(icoPath);
  const brandedExe = path.join(brandedDistDir, path.basename(exePath));

  mkdirSync(brandingRoot, { recursive: true });
  cpSync(distDir, brandedDistDir, { recursive: true });
  writeFileSync(
    manifestPath,
    `${JSON.stringify({
      appUserModelId: APP_USER_MODEL_ID,
      sourceExeSha256,
      icoSha256,
      brandedExePath: brandedExe,
    }, null, 2)}\n`,
  );

  rmSync(brandedDistDir, { recursive: true, force: true });
  assert.equal(brandingIsFresh({ manifest: JSON.parse(readFileSync(manifestPath, "utf8")), sourceExeSha256, icoSha256 }), false);

  applyElectronOverrideEnv(brandedDistDir);
  const rebuilt = simulateEnsureCopyFromStock(desktopDir);
  assert.ok(existsSync(path.join(rebuilt.brandedDistDir, path.basename(rebuilt.stock.exePath))));
});

test("launcher: child process inherits override and second resolve still uses node_modules", () => {
  const desktopDir = mkdtempSync(path.join(os.tmpdir(), "my-agent-spawn-"));
  seedStockElectronDist(desktopDir);
  const { brandedDistDir } = brandingPaths(desktopDir);
  simulateEnsureCopyFromStock(desktopDir);

  const runner = path.join(desktopPackageRoot, "tests", "fixtures", "launcher-child-resolve.mjs");
  const child = spawnSync(process.execPath, [runner, desktopDir], {
    env: { ...process.env, ELECTRON_OVERRIDE_DIST_PATH: brandedDistDir },
    encoding: "utf8",
  });
  assert.equal(child.status, 0, child.stderr || child.stdout);
  assert.equal(child.stdout.trim(), path.join(desktopDir, "node_modules", "electron", "dist"));
});