import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const shell = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/shells/unified/composer-companion.css", import.meta.url), "utf8");

test("composer embeds one decorative static companion without replacing chat controls", () => {
  const footer = shell.match(/<footer class="unified-composer"[\s\S]*?<\/footer>/)?.[0];
  assert.ok(footer);
  assert.match(footer, /class="unified-composer-companion"[^>]*aria-hidden="true"/);
  assert.match(footer, /composer-companion\.png/);
  assert.match(footer, /draggable="false"/);
  for (const id of ["unified-input", "unified-mode-toggle", "unified-stop", "unified-send"]) {
    assert.equal((footer.match(new RegExp(`id="${id}"`, "g")) || []).length, 1);
  }
  assert.match(footer, /aria-label="输入消息"/);
  assert.match(footer, /aria-label="发送消息"/);
  assert.match(shell, /import "\.\/composer-companion\.css"/);
});

test("companion stays compact, reserves its own room and cannot intercept typing", () => {
  assert.match(css, /--composer-companion-width:\s*132px/);
  assert.match(css, /pointer-events:\s*none/);
  assert.match(css, /margin-top:\s*76px/);
  assert.match(css, /grid-template-areas:\s*"attachments attachments attachments"\s*"input input input"\s*"mode stop send"/);
  assert.match(css, /max-width:\s*920px/);
  assert.match(css, /@media\s*\(max-width:\s*680px\)/);
  assert.doesNotMatch(css, /animation:/);
});

test("companion image is a transparent cutout rather than a rectangular screenshot", () => {
  const png = readFileSync(new URL("../src/shells/unified/assets/composer-companion.png", import.meta.url));
  assert.equal(png.subarray(1, 4).toString(), "PNG");
  assert.equal(png[25], 6, "RGBA PNG required");
  assert.ok(png.readUInt32BE(16) <= 512);
});
