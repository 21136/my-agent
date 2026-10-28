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

test("electron main plays opening splash in parallel with sidecar startup", () => {
  assert.match(mainSource, /playOpeningSplash\(\)/);
  assert.match(mainSource, /await splashWork;/);
  assert.match(mainSource, /await dismissOpeningSplash\(\)/);
});
