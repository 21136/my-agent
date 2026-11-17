import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { APP_ICON_RUNTIME_CANDIDATE_FILES } from "../electron/app-icon-paths.ts";

test("app icon runtime candidates prefer icon-256 before app-icon.ico", () => {
  const names = APP_ICON_RUNTIME_CANDIDATE_FILES.map((file) => path.basename(file));
  assert.equal(names[0], "icon-256.png");
  const icoIndex = names.indexOf("app-icon.ico");
  const smallIndex = names.findIndex((name) => name === "icon-256.png" || name === "icon-32.png");
  assert.ok(smallIndex >= 0);
  assert.ok(icoIndex > smallIndex);
  const pngIndex = names.indexOf("icon.png");
  assert.ok(pngIndex > smallIndex);
  const firstExisting = APP_ICON_RUNTIME_CANDIDATE_FILES.find((file) => existsSync(file));
  assert.ok(firstExisting);
  assert.equal(path.basename(firstExisting!), "icon-256.png");
});
