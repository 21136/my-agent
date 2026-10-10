import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const cssPath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../src/shells/unified/unified.css",
);
const css = readFileSync(cssPath, "utf8");

test("success tool sublines stay muted and only failures use the error color", () => {
  const unscoped = css.match(/\.unified-tool-step-sub\s*\{[^}]*\}/)?.[0] ?? "";
  assert.match(unscoped, /color:\s*var\(--ma-text-muted\)/);
  assert.equal(unscoped.includes("#c44"), false);
  assert.match(
    css,
    /\.unified-tool-line\.is-fail \.unified-tool-step-sub\s*\{[^}]*#c44/,
  );
  assert.match(css, /\.unified-tool-step-sub\.is-fail\s*,/);
});
