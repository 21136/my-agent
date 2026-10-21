import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const lightbox = readFileSync(new URL("../src/image-lightbox.ts", import.meta.url), "utf8");
const unified = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
const stylesheet = readFileSync(new URL("../src/shells/unified/unified.css", import.meta.url), "utf8");

test("lightbox opens from data URLs and agent paths", () => {
  assert.match(lightbox, /openImageLightboxFromSrc/);
  assert.match(lightbox, /openImageLightboxFromAgentPath/);
  assert.match(lightbox, /loadAgentImageDataUrl/);
});

test("lightbox supports wheel and toolbar zoom", () => {
  assert.match(lightbox, /changeScale/);
  assert.match(lightbox, /data-ma-zoom-in/);
  assert.match(lightbox, /"wheel"/);
});

test("chat inline images use click-to-zoom", () => {
  assert.match(unified, /bindImageLightboxClick\(chatEl/);
  assert.match(unified, /img\.unified-generated-image/);
  assert.doesNotMatch(unified, /image-zoom/);
  assert.match(stylesheet, /\.unified-generated-image\s*\{[^}]*cursor:\s*zoom-in/s);
});
