import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  APP_USER_MODEL_ID,
  BRANDED_DIST_DIRNAME,
  BRANDING_DIR_NAME,
  brandingIsFresh,
  brandingPaths,
  envOverridePath,
} from "../scripts/windows-branded-electron-lib.mjs";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("branding paths stay under desktop/.branding", () => {
  const { brandingRoot, brandedDistDir } = brandingPaths(desktopDir);
  assert.ok(brandedDistDir.includes(BRANDING_DIR_NAME));
  assert.ok(brandedDistDir.endsWith(BRANDED_DIST_DIRNAME));
  assert.equal(path.basename(brandingRoot), BRANDING_DIR_NAME);
});

test("env override uses branded dist directory", () => {
  const { brandedDistDir } = brandingPaths(desktopDir);
  assert.equal(envOverridePath(brandedDistDir), brandedDistDir);
});

test("brandingIsFresh rejects stale source hash", () => {
  const manifest = {
    sourceExeSha256: "aaa",
    icoSha256: "bbb",
    appUserModelId: APP_USER_MODEL_ID,
    brandedExePath: path.join(desktopDir, "package.json"),
  };
  assert.equal(
    brandingIsFresh({ manifest, sourceExeSha256: "ccc", icoSha256: "bbb" }),
    false,
  );
  assert.equal(
    brandingIsFresh({ manifest, sourceExeSha256: "aaa", icoSha256: "bbb" }),
    existsSync(manifest.brandedExePath),
  );
});

test("app-icon.ico is present for Windows branding pipeline", () => {
  const ico = path.join(desktopDir, "build", "app-icon.ico");
  assert.ok(existsSync(ico), "run node scripts/build-app-icon-ico.mjs");
  assert.ok(readFileSync(ico).length > 100);
});

test("main.ts sets AppUserModelId before ready on win32", () => {
  const mainTs = readFileSync(path.join(desktopDir, "electron", "main.ts"), "utf8");
  const readyIdx = mainTs.indexOf("app.whenReady");
  const idIdx = mainTs.indexOf('app.setAppUserModelId("my-agent.desktop")');
  assert.ok(idIdx >= 0);
  assert.ok(readyIdx >= 0);
  assert.ok(idIdx < readyIdx, "AppUserModelId must be registered before app.whenReady");
});