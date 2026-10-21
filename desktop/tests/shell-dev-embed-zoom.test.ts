import assert from "node:assert/strict";
import { test } from "node:test";
import { shellDevEmbedZoom } from "../electron/shell-dev-embed-zoom.ts";

test("shell dev embed zoom scales down uniformly for small slots", () => {
  assert.equal(shellDevEmbedZoom(960, 720), 1);
  assert.ok(shellDevEmbedZoom(480, 360) < 1);
  assert.ok(shellDevEmbedZoom(480, 360) > 0.49);
});
