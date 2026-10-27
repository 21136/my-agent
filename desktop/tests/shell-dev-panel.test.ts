import assert from "node:assert/strict";
import { test } from "node:test";
import { mainFocusForRail, isRailTab } from "../src/shells/unified/rail.ts";
import { renderShellDevPanel } from "../src/shells/unified/shell-dev-panel.ts";

test("shell-dev rail maps to dedicated main focus", () => {
  assert.equal(isRailTab("shell-dev"), true);
  assert.equal(mainFocusForRail("shell-dev"), "shell-dev");
});

test("shell dev panel exposes embedded web slot and dual lanes", () => {
  const html = renderShellDevPanel("http://127.0.0.1:5173/", "http://127.0.0.1:5174/");
  assert.match(html, /data-shell-dev-web-slot/);
  assert.match(html, /data-shell-dev-main-url/);
  assert.match(html, /data-shell-dev-promote/);
  assert.match(html, /data-shell-dev-adopt-merge/);
  assert.match(html, /一键合入实验分支/);
  assert.match(html, /data-shell-dev-sync-sandbox/);
  assert.match(html, /壳开发预览/);
});
