import assert from "node:assert/strict";
import { test } from "node:test";
import { shellDevPreviewBounds } from "../src/shells/unified/shell-dev-view-bounds.ts";

const rect = (left: number, top: number, right: number, bottom: number) => ({ left, top, right, bottom });

test("native preview bounds stop at preview panel instead of covering controls below", () => {
  const bounds = shellDevPreviewBounds({
    slot: rect(120, 220, 900, 760),
    preview: rect(100, 180, 920, 520),
    viewport: rect(60, 80, 1000, 700),
  });

  assert.deepEqual(bounds, { x: 120, y: 220, width: 780, height: 300 });
});

test("native preview is hidden when its slot is outside the visible preview panel", () => {
  assert.equal(shellDevPreviewBounds({
    slot: rect(120, 540, 900, 760),
    preview: rect(100, 180, 920, 520),
    viewport: rect(60, 80, 1000, 700),
  }), null);
});
