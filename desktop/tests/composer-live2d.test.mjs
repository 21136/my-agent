import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const shell = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/shells/unified/composer-companion.css", import.meta.url), "utf8");
const rendererUrl = new URL("../src/shells/unified/composer-live2d.ts", import.meta.url);
const renderer = existsSync(rendererUrl) ? readFileSync(rendererUrl, "utf8") : "";
const assets = new URL("../public/live2d/chibi-front/", import.meta.url);

test("front Live2D mounts only inside the existing decorative companion and keeps the image fallback", () => {
  const footer = shell.match(/<footer class="unified-composer"[\s\S]*?<\/footer>/)?.[0];
  assert.ok(footer);
  assert.match(footer, /class="unified-composer-companion"[^>]*aria-hidden="true"/);
  assert.match(footer, /<canvas[^>]*class="unified-composer-live2d"/);
  assert.match(footer, /composer-companion\.png/);
  assert.match(footer, /draggable="false"/);
  assert.match(shell, /mountComposerLive2D\(/);
  assert.match(renderer, /prefers-reduced-motion/);
  assert.match(renderer, /catch[\s\S]*fallback|fallback[\s\S]*catch/);
});

test("Live2D canvas shares the existing footprint and never captures input", () => {
  assert.match(css, /\.unified-composer-live2d[\s\S]*pointer-events:\s*none/);
  assert.match(css, /\.unified-composer-live2d[\s\S]*position:\s*absolute/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
});

test("bundled front model and Cubism Core are local and carry the upstream license notice", () => {
  for (const name of ["model3.json", "model.moc3", "texture.png", "LICENSE.txt", "live2dcubismcore.min.js"]) {
    assert.ok(existsSync(new URL(name, assets)), `missing local asset ${name}`);
  }
  const license = readFileSync(new URL("LICENSE.txt", assets), "utf8");
  assert.match(license, /Live2D Inc\./);
  assert.match(license, /Redistributable Code/);
});
