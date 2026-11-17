import assert from "node:assert/strict";
import { existsSync, mkdirSync, statSync, utimesSync, writeFileSync, mkdtempSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  buildAppIconIco,
  resolveAppIconPngSources,
  sha256File,
} from "../scripts/build-app-icon-ico.mjs";
import {
  APP_ICON_CANONICAL_PNG,
  APP_ICON_RUNTIME_CANDIDATE_FILES,
  resolveFirstExistingIconFile,
} from "../electron/app-icon-paths.ts";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("buildAppIconIco canonical source is icon-256 when present", () => {
  const { primary } = resolveAppIconPngSources();
  assert.ok(primary);
  assert.equal(path.basename(primary), "icon-256.png");
});

test("newer mtime on icon-32 does not change canonical PNG source", () => {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "icon-mtime-"));
  const build = path.join(tmp, "build");
  const pub = path.join(tmp, "public");
  mkdirSync(build, { recursive: true });
  mkdirSync(pub, { recursive: true });
  const p256 = path.join(build, "icon-256.png");
  const p32 = path.join(build, "icon-32.png");
  writeFileSync(p256, Buffer.from("png256"));
  writeFileSync(p32, Buffer.from("png32-old"));
  const now = Date.now();
  utimesSync(p256, now / 1000, (now - 60_000) / 1000);
  utimesSync(p32, now / 1000, (now + 60_000) / 1000);

  const order = [
    path.join(build, "icon-256.png"),
    path.join(build, "icon-32.png"),
    path.join(pub, "icon-32.png"),
  ].filter((f) => existsSync(f));
  assert.equal(path.basename(order[0]), "icon-256.png");
});

test("runtime resolve prefers icon-256 over committed app-icon.ico", () => {
  assert.ok(existsSync(APP_ICON_CANONICAL_PNG));
  assert.ok(existsSync(path.join(desktopDir, "build", "app-icon.ico")));
  const chosen = resolveFirstExistingIconFile(APP_ICON_RUNTIME_CANDIDATE_FILES);
  assert.ok(chosen);
  assert.equal(path.basename(chosen), "icon-256.png");
  assert.equal(sha256File(chosen), sha256File(APP_ICON_CANONICAL_PNG));
});

test("buildAppIconIco records primary sha256 matching icon-256", async () => {
  const before = sha256File(APP_ICON_CANONICAL_PNG);
  const result = await buildAppIconIco({ force: true });
  assert.equal(result.primarySha256, before);
  assert.ok(result.inputs.some((f) => path.basename(f) === "icon-256.png"));
});

test("predev would not rebuild ico from icon-32 only when 256 exists", async () => {
  const result = await buildAppIconIco({ force: false });
  assert.equal(path.basename(result.source), "icon-256.png");
  assert.ok(statSync(result.outIco).mtimeMs >= statSync(APP_ICON_CANONICAL_PNG).mtimeMs);
});