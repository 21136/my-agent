import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { APP_ICON_CANDIDATE_FILES } from "../electron/app-icon-paths.ts";

test("app icon candidates list smaller assets before icon.png", () => {
  const names = APP_ICON_CANDIDATE_FILES.map((file) => path.basename(file));
  const pngIndex = names.indexOf("icon.png");
  const smallIndex = names.findIndex((name) => name === "icon-256.png" || name === "icon-32.png");
  assert.ok(smallIndex >= 0);
  assert.ok(pngIndex > smallIndex);
  const firstExisting = APP_ICON_CANDIDATE_FILES.find((file) => existsSync(file));
  assert.ok(firstExisting);
  assert.notEqual(path.basename(firstExisting!), "icon.png");
});
