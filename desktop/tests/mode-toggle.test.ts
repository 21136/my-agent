import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const shell = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/shells/unified/unified.css", import.meta.url), "utf8");

test("composer mode control is a two-option segmented toggle", () => {
  const footer = shell.match(/<footer class="unified-composer"[\s\S]*?<\/footer>/)?.[0];
  assert.ok(footer);
  assert.match(footer, /id="unified-mode-toggle"[^>]*role="group"/);
  assert.match(footer, /data-mode-value="ask"/);
  assert.match(footer, /data-mode-value="agent"/);
  assert.match(footer, /只聊/);
  assert.match(footer, /动手/);
  assert.match(shell, /modeSegButtons/);
  assert.match(shell, /aria-pressed/);
  assert.doesNotMatch(footer, /unified-mode-pill/);
});

test("mode toggle uses Neon Twin accent tokens and active contrast", () => {
  assert.match(css, /\.unified-mode-toggle\s*\{/);
  assert.match(css, /\.unified-mode-seg\.is-active\s*\{/);
  assert.match(css, /var\(--ma-accent\)/);
  assert.match(css, /var\(--ma-accent-muted\)/);
  assert.doesNotMatch(css, /\.unified-mode-pill\s*\{/);
});
