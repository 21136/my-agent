import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";

const splashModule = readFileSync(new URL("../electron/opening-splash.ts", import.meta.url), "utf8");
const mainSource = readFileSync(new URL("../electron/main.ts", import.meta.url), "utf8");
const splashHtml = readFileSync(new URL("../public/splash.html", import.meta.url), "utf8");

test("opening splash asset and page reference splash-xiaomowang.mp4", () => {
  assert.ok(
    existsSync(new URL("../public/splash-xiaomowang.mp4", import.meta.url)),
    "splash video must live under desktop/public",
  );
  assert.match(splashHtml, /splash-xiaomowang\.mp4/);
  assert.match(splashModule, /OPENING_SPLASH_VIDEO_BASENAME = "splash-xiaomowang\.mp4"/);
});

test("electron main overlaps sidecar startup with workbench load and dismisses splash when workbench shows", () => {
  assert.match(mainSource, /playOpeningSplash\(\)/);
  assert.match(mainSource, /revealColdStartWorkbench/);
  assert.match(mainSource, /await dismissOpeningSplash\(\)/);
  assert.match(mainSource, /Promise\.all\(\[runSidecarStartup\(\), revealColdStartWorkbench\(\)\]\)/);
  assert.doesNotMatch(mainSource, /await runSidecarStartup\(\);\s*\n\s*await openWorkbenchWindow\(\)/);
  assert.doesNotMatch(mainSource, /await splashWork;\s*\n\s*await openWorkbenchWindow\(\)/);
});

test("opening splash holds last frame until main dismisses (video ended does not auto-close)", () => {
  assert.match(splashModule, /video\.pause\(\)/);
  assert.doesNotMatch(splashModule, /addEventListener\("ended", finish/);
});
