import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const shell = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
const renderer = readFileSync(new URL("../src/shells/unified/composer-live2d.ts", import.meta.url), "utf8");
test("unified shell disposes its Live2D renderer when the shell unmounts", () => {
  assert.match(shell, /disposeComposerLive2D\?\.\(\)/);
  assert.match(renderer, /document\.hidden[\s\S]*stopFrame/);
  assert.match(renderer, /model\?\.release\?\.\(\)/);
  assert.match(renderer, /moc\?\._release\?\.\(\)/);
});
