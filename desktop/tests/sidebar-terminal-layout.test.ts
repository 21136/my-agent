import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../src/shells/unified/sidebar-v2.css", import.meta.url), "utf8");

test("expanded terminal details stay in a bounded scroll area instead of displacing sidebar content", () => {
  const rule = css.match(/\.sidebar-runtime-badges\s*>\s*#sidebar-terminals:has\(\.sidebar-terminal-details\)\s*\{([^}]*)\}/s)?.[1] ?? "";
  assert.match(rule, /max-height\s*:/);
  assert.match(rule, /overflow-y\s*:\s*auto/);
  assert.match(rule, /min-height\s*:\s*0/);
});
