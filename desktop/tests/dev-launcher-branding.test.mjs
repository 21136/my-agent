import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test, afterEach } from "node:test";
import {
  assertDistinctDistCopy,
  brandingPaths,
  resolveStockElectronDist,
} from "../scripts/windows-branded-electron-lib.mjs";

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