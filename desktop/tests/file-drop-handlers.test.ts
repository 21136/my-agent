import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(new URL("../src/file-drop.ts", import.meta.url), "utf8");

test("file drop listens on composer only so nested textarea drops are not staged twice", () => {
  assert.match(source, /const dropTargets = \[composer\]/);
  assert.doesNotMatch(source, /dropTargets = Array\.from\(new Set\(\[composer, \.\.\.pasteTargets\]\)\)/);
});

test("image attachments render a thumbnail-only chip with zoom affordance", () => {
  assert.match(source, /file-drop-chip-image/);
  assert.match(source, /file-drop-chip-zoom/);
  assert.match(source, /file-drop-chip-thumb/);
  assert.doesNotMatch(source, /file-drop-chip-body/);
  assert.match(source, /hydrateAgentImageElements\(chips/);
  assert.match(source, /bindImageLightboxClick\(chips/);
});

test("paste and drop handlers stop propagation after accepting files", () => {
  const dropBlock = source.match(/async function onDrop[\s\S]*?^  \}/m);
  const pasteBlock = source.match(/async function onPaste[\s\S]*?^  \}/m);
  assert.ok(dropBlock, "onDrop must exist");
  assert.ok(pasteBlock, "onPaste must exist");
  assert.match(dropBlock[0], /ev\.stopPropagation\(\)/);
  assert.match(pasteBlock[0], /ev\.stopPropagation\(\)/);
});
