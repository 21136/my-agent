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
  assert.match(html, /data-shell-dev-url/);
  assert.match(html, /data-shell-dev-promote/);
  assert.match(html, /data-shell-dev-reset-main/);
  assert.match(html, /data-shell-dev-reload/);
  assert.match(html, /data-shell-dev-popout/);
  assert.match(html, /data-shell-dev-one-click/);
  assert.match(html, /data-shell-dev-adopt-merge/);
  assert.match(html, /data-shell-dev-adopt-status/);
  assert.match(html, /data-shell-dev-sandbox-session/);
  assert.match(html, /data-shell-dev-managed-review/);
  assert.match(html, /data-managed-review-panel/);
  assert.match(html, /data-managed-review-browser/);
  assert.match(html, /data-managed-source-selection/);
  assert.match(html, /data-evolution-begin/);
  assert.match(html, /data-evolution-inspect/);
  assert.match(html, /data-evolution-stop/);
  assert.match(html, /壳开发预览/);
  assert.match(html, /一键完成合入/);
  assert.match(html, /日常合入点这里/);
  assert.match(html, /独立窗口预览/);
  assert.match(html, /采用预览为主壳/);
  assert.match(html, /返回稳定版/);
  assert.match(html, /旧整支合入（仅隔离夹具）/);
  assert.match(html, /shell-dev-more/);
  assert.match(html, /shell-dev-notes/);
  assert.match(html, /shell-dev-section/);
  assert.match(html, /shell-dev-one-click/);
});

test("shell dev panel keeps preview chrome compact by default", () => {
  const html = renderShellDevPanel("http://127.0.0.1:5173/", "http://127.0.0.1:5174/");
  // Long warnings live under collapsible notes, not the always-visible hint.
  const hintMatch = html.match(/<p class="shell-dev-hint">([\s\S]*?)<\/p>/);
  assert.ok(hintMatch, "missing shell-dev-hint");
  assert.equal(hintMatch[1].includes("旧整支合入按钮仅用于隔离夹具"), false);
  assert.match(hintMatch[1], /日常合入点这里/);
  assert.match(html, /<details class="shell-dev-notes">/);
  assert.match(html, /shell-dev-notes[\s\S]*一键完成合入/);

  // One-click CTA is outside 更多 and before secondary actions.
  const oneClickIdx = html.indexOf("data-shell-dev-one-click");
  const moreIdx = html.indexOf('class="shell-dev-more"');
  const sandboxIdx = html.indexOf("data-shell-dev-sandbox-session");
  const adoptIdx = html.indexOf("data-shell-dev-adopt-merge");
  assert.ok(oneClickIdx >= 0 && moreIdx > oneClickIdx);
  assert.ok(moreIdx >= 0 && sandboxIdx > moreIdx && adoptIdx > moreIdx);
  // Review + source selection start collapsed so the web slot keeps height.
  assert.match(html, /<details class="shell-dev-section"[^>]*data-managed-review-panel/);
  assert.match(html, /<details class="shell-dev-section"[^>]*aria-label="源码范围选择"/);
  assert.doesNotMatch(html, /<details[^>]*open[^>]*data-managed-review-panel/);
});
