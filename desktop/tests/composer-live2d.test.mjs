import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const shell = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/shells/unified/composer-companion.css", import.meta.url), "utf8");
const rendererUrl = new URL("../src/shells/unified/composer-live2d.ts", import.meta.url);
const renderer = existsSync(rendererUrl) ? readFileSync(rendererUrl, "utf8") : "";
const assets = new URL("../public/live2d/chibi-front/", import.meta.url);

test("front Live2D implementation remains available but is not mounted by the unified shell", () => {
  const footer = shell.match(/<footer class="unified-composer"[\s\S]*?<\/footer>/)?.[0];
  assert.ok(footer);
  assert.doesNotMatch(footer, /unified-composer-companion|unified-composer-live2d|composer-companion\.png/);
  assert.doesNotMatch(shell, /mountComposerLive2D|disposeComposerLive2D/);
  assert.match(renderer, /export function mountComposerLive2D/);
  assert.match(renderer, /prefers-reduced-motion/);
  assert.match(renderer, /catch[\s\S]*fallback|fallback[\s\S]*catch/);
});

test("unified composer styles do not load or reserve space for Live2D", () => {
  assert.doesNotMatch(css, /unified-composer-live2d|unified-composer-companion/);
  assert.doesNotMatch(css, /64px|grid-area:\s*companion/);
});
test("bundled front model and Cubism Core are local and carry the upstream license notice", () => {
  for (const name of ["model3.json", "model.moc3", "texture.png", "LICENSE.txt", "live2dcubismcore.min.js"]) {
    assert.ok(existsSync(new URL(name, assets)), `missing local asset ${name}`);
  }
  const license = readFileSync(new URL("LICENSE.txt", assets), "utf8");
  assert.match(license, /Live2D Inc\./);
  assert.match(license, /Redistributable Code/);
});
