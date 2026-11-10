/**
 * CSS / markup regressions for companion UI batch 2 — runs without Playwright.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { isHarnessArtifactPath } from "../electron/sandbox-adopt.ts";

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function read(rel) {
  return fs.readFileSync(path.join(desktop, rel), "utf8");
}

test("music panel CSS defines bounded glass card layout", () => {
  const css = read("src/shells/unified/music-panel.css");
  assert.match(css, /\.music-page\s*\{/);
  assert.match(css, /border-radius/);
  assert.match(css, /max-height/);
  assert.match(css, /backdrop-filter|background.*rgba/i);
});

test("current-work requirements keep scroll margin above footer", () => {
  const css = read("src/shells/unified/current-work-overview.css");
  assert.match(css, /scroll-margin-bottom/);
  assert.match(css, /padding-bottom/);
});

test("shell dev panel keeps git/URL grids inside closed details", () => {
  const panel = read("src/shells/unified/shell-dev-panel.ts");
  assert.match(panel, /dual-lane-details/);
  assert.match(panel, /shell-dev-diagnostics/);
  assert.doesNotMatch(panel, /<details[^>]+open/);
});

test("companion tool hint markup lives above status, not in chat transcript", () => {
  const html = read("src/shells/unified/index.ts");
  assert.match(html, /unified-companion-tool-hint/);
  assert.match(html, /setCompanionToolHint/);
  assert.doesNotMatch(html, /setStatus\([^)]*文档需在已绑定/);
});

test("harness artifact paths are excluded from product dirty lists", () => {
  assert.equal(isHarnessArtifactPath("desktop/_artifacts/run-1/foo.png"), true);
  assert.equal(isHarnessArtifactPath("desktop/_artifacts-91-run82/trace.zip"), true);
  assert.equal(isHarnessArtifactPath("desktop/@AutomationLog.txt"), true);
  assert.equal(isHarnessArtifactPath("desktop/tests/shot.png"), true);
  assert.equal(isHarnessArtifactPath("desktop/src/main.ts"), false);
});
