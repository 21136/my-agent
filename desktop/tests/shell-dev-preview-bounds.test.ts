import assert from "node:assert/strict";
import { test } from "node:test";
import { letterboxAspectBounds, SHELL_DEV_PREVIEW_ASPECT } from "../src/shell-dev-preview-bounds.ts";

test("shell preview letterbox fits wide slots with workbench aspect", () => {
  const bounds = letterboxAspectBounds({ left: 0, top: 0, right: 900, bottom: 300 });
  assert.ok(bounds);
  assert.ok(Math.abs(bounds.width / bounds.height - SHELL_DEV_PREVIEW_ASPECT) < 0.01);
  assert.equal(bounds.height, 300);
  assert.ok(bounds.width < 900);
});

test("shell preview letterbox fits tall slots with workbench aspect", () => {
  const bounds = letterboxAspectBounds({ left: 10, top: 20, right: 410, bottom: 820 });
  assert.ok(bounds);
  assert.ok(Math.abs(bounds.width / bounds.height - SHELL_DEV_PREVIEW_ASPECT) < 0.01);
  assert.equal(bounds.width, 400);
});
