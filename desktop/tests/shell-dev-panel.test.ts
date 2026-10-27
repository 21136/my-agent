import assert from "node:assert/strict";
import { test } from "node:test";
import { mainFocusForRail, isRailTab, RAIL_TAB_LABELS } from "../src/shells/unified/rail.ts";
import { renderShellDevPanel } from "../src/shells/unified/shell-dev-panel.ts";

test("shell-dev rail maps to dedicated main focus", () => {
  assert.equal(isRailTab("shell-dev"), true);
  assert.equal(mainFocusForRail("shell-dev"), "shell-dev");
  assert.equal(RAIL_TAB_LABELS["shell-dev"].label, "双车道");
});

test("flat dual-lane panel exposes peer lane cards and peer CTAs", () => {
  const html = renderShellDevPanel("http://127.0.0.1:5173/", "http://127.0.0.1:5174/");
  assert.match(html, /data-dual-lane-panel/);
  assert.match(html, /id="shell-dev-page-heading">双车道</);
  assert.match(html, /data-lane="stable"/);
  assert.match(html, /data-lane="sandbox"/);
  assert.match(html, /data-shell-dev-stable-branch/);
  assert.match(html, /data-shell-dev-sandbox-branch/);
  assert.match(html, /data-shell-dev-stable-head/);
  assert.match(html, /data-shell-dev-sandbox-head/);
  assert.match(html, /data-shell-dev-stable-dirty/);
  assert.match(html, /data-shell-dev-sandbox-dirty/);
  assert.match(html, /data-shell-dev-stable-ahead/);
  assert.match(html, /data-shell-dev-commits-ahead/);
  assert.match(html, /data-shell-dev-main-url/);
  assert.match(html, /data-shell-dev-url/);
  assert.match(html, /data-shell-dev-sync-stable/);
  assert.match(html, /data-shell-dev-one-click/);
  assert.match(html, /用稳定刷实验/);
  assert.match(html, /合入稳定/);
  assert.doesNotMatch(html, /一键完成合入/);
  assert.doesNotMatch(html, /壳开发预览/);

  // Peer CTAs share the same class / visual weight; neither buried under 更多.
  assert.match(html, /dual-lane-peer-cta[\s\S]*data-shell-dev-sync-stable/);
  assert.match(html, /dual-lane-peer-cta[\s\S]*data-shell-dev-one-click/);
  assert.doesNotMatch(html, /class="shell-dev-more"/);
  const syncIdx = html.indexOf("data-shell-dev-sync-stable");
  const adoptIdx = html.indexOf("data-shell-dev-one-click");
  const helpIdx = html.indexOf("data-shell-dev-help");
  assert.ok(syncIdx >= 0 && adoptIdx >= 0 && helpIdx > syncIdx && helpIdx > adoptIdx);

  // Blockers live on the adopt (实验) card, not a nested Host flow.
  const sandboxCard = html.slice(html.indexOf('data-lane="sandbox"'), html.indexOf("dual-lane-preview"));
  assert.match(sandboxCard, /data-shell-dev-dual-blockers/);
  assert.match(sandboxCard, /data-shell-dev-one-click/);
});

test("preview is a peer section, not the parent of lane ops", () => {
  const html = renderShellDevPanel("http://127.0.0.1:5173/", "http://127.0.0.1:5174/");
  const cardsIdx = html.indexOf("dual-lane-cards");
  const previewIdx = html.indexOf("dual-lane-preview");
  const slotIdx = html.indexOf("data-shell-dev-web-slot");
  assert.ok(cardsIdx >= 0 && previewIdx > cardsIdx, "lane cards before preview section");
  assert.ok(slotIdx > previewIdx, "web slot inside preview peer section");
  assert.match(html, /aria-label="预览"/);
  assert.match(html, /data-shell-dev-popout/);
  assert.match(html, /data-shell-dev-reload/);
  assert.match(html, /data-shell-dev-promote/);
  assert.match(html, /data-shell-dev-reset-main/);
});

test("legacy / advanced entry points collapse under help, review starts closed", () => {
  const html = renderShellDevPanel("http://127.0.0.1:5173/", "http://127.0.0.1:5174/");
  assert.match(html, /data-shell-dev-help/);
  assert.match(html, /帮助与旧入口/);
  const helpIdx = html.indexOf("data-shell-dev-help");
  for (const sel of [
    "data-shell-dev-sandbox-session",
    "data-shell-dev-adopt-status",
    "data-shell-dev-managed-review",
    "data-shell-dev-adopt-merge",
  ]) {
    assert.ok(html.indexOf(sel) > helpIdx, `${sel} should sit under help`);
  }
  assert.match(html, /data-evolution-begin/);
  assert.match(html, /data-managed-review-panel/);
  assert.match(html, /data-managed-source-selection/);
  assert.doesNotMatch(html, /<details[^>]*open[^>]*data-managed-review-panel/);
  // Short hint only — no wall of concepts always visible.
  const hintMatch = html.match(/<p class="shell-dev-hint">([\s\S]*?)<\/p>/);
  assert.ok(hintMatch);
  assert.equal(hintMatch[1].includes("旧整支合入按钮仅用于隔离夹具"), false);
  assert.match(hintMatch[1], /对等/);
});
