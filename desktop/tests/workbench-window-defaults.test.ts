import assert from "node:assert/strict";
import { test } from "node:test";
import {
  WORKBENCH_DEFAULT_HEIGHT,
  WORKBENCH_DEFAULT_WIDTH,
  fitSizeWithinBox,
  workbenchDefaultWindowSize,
} from "../electron/workbench-window-defaults.ts";

test("fitSizeWithinBox keeps aspect ratio and letterboxes into a smaller box", () => {
  assert.deepEqual(fitSizeWithinBox(960, 720, 800, 600), { width: 800, height: 600 });
  // 16:9 design into a taller box -> pillarbox (width-limited)
  assert.deepEqual(fitSizeWithinBox(1280, 720, 960, 720), { width: 960, height: 540 });
});

test("fitSizeWithinBox never upscales past the design size", () => {
  assert.deepEqual(fitSizeWithinBox(960, 720, 1920, 1080), { width: 960, height: 720 });
});

test("workbenchDefaultWindowSize clamps to work area when provided", () => {
  assert.deepEqual(workbenchDefaultWindowSize(), {
    width: WORKBENCH_DEFAULT_WIDTH,
    height: WORKBENCH_DEFAULT_HEIGHT,
  });
  assert.deepEqual(workbenchDefaultWindowSize({ width: 800, height: 600 }), { width: 800, height: 600 });
  assert.deepEqual(workbenchDefaultWindowSize({ width: 500, height: 400 }), { width: 500, height: 375 });
});