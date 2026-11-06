import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { mainFocusForRail, isRailTab, RAIL_TAB_LABELS } from "../src/shells/unified/rail.ts";
import { renderShellDevPanel } from "../src/shells/unified/shell-dev-panel.ts";

test("shell-dev rail maps to dedicated main focus", () => {
  assert.equal(isRailTab("shell-dev"), true);
  assert.equal(mainFocusForRail("shell-dev"), "shell-dev");
  assert.equal(RAIL_TAB_LABELS["shell-dev"].label, "双车道");
});

test("product dual-lane default shows status + peer CTAs, details collapsed", () => {
  const html = renderShellDevPanel("http://127.0.0.1:5173/", "http://127.0.0.1:5174/");
  assert.match(html, /data-dual-lane-panel/);
  assert.match(html, /id="shell-dev-page-heading">双车道</);
  assert.match(html, /data-lane="stable"/);
  assert.match(html, /data-lane="sandbox"/);

  // Default product surface: lane name + status line + primary CTA.
  assert.match(html, /data-shell-dev-stable-status/);
  assert.match(html, /data-shell-dev-sandbox-status/);
  assert.match(html, /data-shell-dev-sync-stable/);
  assert.match(html, /data-shell-dev-one-click/);
  assert.match(html, /用稳定刷实验/);
  assert.match(html, /开实验会话/);
  assert.match(html, /合入稳定/);
  assert.doesNotMatch(html, /一键完成合入/);
  assert.doesNotMatch(html, /壳开发预览/);
  assert.doesNotMatch(html, /dual-lane-card-note/);
  assert.doesNotMatch(html, /5173 \/ 8765/);

  // Git fields live under details, not as always-visible admin grid.
  assert.match(html, /data-shell-dev-stable-details/);
  assert.match(html, /data-shell-dev-sandbox-details/);
  assert.match(html, /<summary>详情<\/summary>/);
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

  // Default details are closed (no open attribute on lane details).
  assert.doesNotMatch(html, /<details[^>]*open[^>]*data-shell-dev-stable-details/);
  assert.doesNotMatch(html, /<details[^>]*open[^>]*data-shell-dev-sandbox-details/);

  // Peer CTAs share the same class / visual weight; neither buried under more menu.
  assert.match(html, /dual-lane-peer-cta[\s\S]*data-shell-dev-sync-stable/);
  assert.match(html, /dual-lane-peer-cta[\s\S]*data-shell-dev-one-click/);
  assert.doesNotMatch(html, /class="shell-dev-more"/);
  const syncIdx = html.indexOf("data-shell-dev-sync-stable");
  const adoptIdx = html.indexOf("data-shell-dev-one-click");
  const helpIdx = html.indexOf("data-shell-dev-help");
  assert.ok(syncIdx >= 0 && adoptIdx >= 0 && helpIdx > syncIdx && helpIdx > adoptIdx);

  // Blockers live on the adopt (experiment) card, not a nested Host flow.
  const sandboxCard = html.slice(html.indexOf('data-lane="sandbox"'), html.indexOf("dual-lane-preview"));
  assert.match(sandboxCard, /data-shell-dev-dual-blockers/);
  assert.match(sandboxCard, /data-shell-dev-sandbox-session/);
  assert.match(sandboxCard, /data-shell-dev-restart-sandbox/);
  assert.match(sandboxCard, /重载实验运行时/);
  assert.match(sandboxCard, /data-shell-dev-one-click/);
});

test("preview is a peer section with primary + overflow chrome", () => {
  const html = renderShellDevPanel("http://127.0.0.1:5173/", "http://127.0.0.1:5174/");
  const cardsIdx = html.indexOf("dual-lane-cards");
  const previewIdx = html.indexOf("dual-lane-preview");
  const slotIdx = html.indexOf("data-shell-dev-web-slot");
  assert.ok(cardsIdx >= 0 && previewIdx > cardsIdx, "lane cards before preview section");
  assert.ok(slotIdx > previewIdx, "web slot inside preview peer section");
  assert.match(html, /aria-label="预览"/);
  assert.match(html, /data-shell-dev-reload/);
  assert.match(html, /shell-dev-preview-more/);
  assert.match(html, /data-shell-dev-popout/);
  assert.match(html, /data-shell-dev-promote/);
  assert.match(html, /data-shell-dev-reset-main/);
  // Overflow holds secondary preview actions.
  const moreIdx = html.indexOf("shell-dev-preview-more");
  assert.ok(html.indexOf("data-shell-dev-popout") > moreIdx);
  assert.ok(html.indexOf("data-shell-dev-promote") > moreIdx);
  assert.ok(html.indexOf("data-shell-dev-reset-main") > moreIdx);
});

test("legacy / advanced entry points collapse under help, review starts closed", () => {
  const html = renderShellDevPanel("http://127.0.0.1:5173/", "http://127.0.0.1:5174/");
  assert.match(html, /data-shell-dev-help/);
  assert.match(html, /帮助与旧入口/);
  const helpIdx = html.indexOf("data-shell-dev-help");
  const sessionIdx = html.indexOf("data-shell-dev-sandbox-session");
  assert.ok(sessionIdx >= 0 && sessionIdx < helpIdx, "开实验会话 is the sandbox-card primary CTA");
  for (const sel of [
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
  // Short product hint ? no ops jargon wall.
  const hintMatch = html.match(/<p class="shell-dev-hint">([\s\S]*?)<\/p>/);
  assert.ok(hintMatch);
  assert.equal(hintMatch[1].includes("旧整支合入按钮仅用于隔离夹具"), false);
  assert.ok(hintMatch[1].length < 40, `hint too long: ${hintMatch[1]}`);
  assert.match(hintMatch[1], /实验会话|合入/);
});

test("dual-lane expands the existing tool region while keeping chat and composer mounted", () => {
  const css = readFileSync(new URL("../src/shells/unified/shell-dev-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.unified-stage:has\([^\n]*#unified-shell-dev:not\(\[hidden\]\)[^\n]*\)\s*\{[^}]*flex-direction:\s*column/s);
  assert.match(css, /#unified-shell-dev:not\(\[hidden\]\)[^\n]*\.unified-chat[^\n]*\{[^}]*display:\s*none/s);
  assert.match(css, /#unified-shell-dev:not\(\[hidden\]\)[^\n]*\.unified-tool-region[^\n]*\{[^}]*flex:\s*1 1 auto/s);
  assert.match(css, /\.unified-main:has\(#unified-shell-dev:not\(\[hidden\]\)\)[\s\S]*?\.unified-composer-dock[\s\S]*?\)\s*\{\s*display:\s*none !important;/);
  assert.match(css, /\.shell-dev-page\s*\{[^}]*width:\s*100%[^}]*max-width:\s*none/s);
  const shell = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  assert.match(shell, /<main class="unified-chat" id="unified-chat"><\/main>/);
  assert.match(shell, /<textarea class="unified-input" id="unified-input"/);
  assert.match(shell, /shellDevEl\.hidden = focus !== "shell-dev"/);
});

test("dual-lane visuals distinguish both lanes and stay contained on narrow screens", () => {
  const css = readFileSync(new URL("../src/shells/unified/shell-dev-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.dual-lane-card\[data-lane="stable"\] \.dual-lane-card-head::before/);
  assert.match(css, /\.dual-lane-card\[data-lane="sandbox"\] \.dual-lane-card-head::before/);
  assert.match(css, /\.dual-lane-status::before/);
  assert.match(css, /\.dual-lane-peer-cta[\s\S]*min-height:\s*2\.75rem/);
  assert.match(css, /\.shell-dev-web-slot[\s\S]*box-sizing:\s*border-box/);
  assert.match(css, /@media \(max-width:\s*720px\)[\s\S]*\.dual-lane-cards[\s\S]*grid-template-columns:\s*1fr/);
  assert.match(css, /\.shell-dev-evolution-actions[\s\S]*flex-wrap:\s*wrap/);
});

test("dual-lane preview keeps its 18rem floor and lets the page scroll on desktop and mobile", () => {
  const css = readFileSync(new URL("../src/shells/unified/shell-dev-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.shell-dev-page\s*\{[^}]*height:\s*auto[^}]*min-height:\s*100%/s);

  const mobileStart = css.lastIndexOf("@media (max-width: 720px)");
  const previewStart = css.lastIndexOf(".dual-lane-preview {", mobileStart - 1);
  assert.ok(previewStart >= 0 && previewStart < mobileStart);
  const previewRule = css.slice(previewStart, css.indexOf("}", previewStart));
  assert.match(previewRule, /flex:\s*1 0 auto/);
  assert.match(previewRule, /min-height:\s*18rem/);

  const scrollRule = css.match(/\.unified-shell-dev\s*\{([^}]+)\}/)?.[1] ?? "";
  assert.match(scrollRule, /overflow-y:\s*auto/);
  assert.match(scrollRule, /overflow-x:\s*hidden/);
  const mobileRules = css.slice(mobileStart);
  assert.match(mobileRules, /\.dual-lane-preview\s*\{[^}]*flex:\s*0 0 auto[^}]*min-height:\s*18rem/s);
  assert.match(mobileRules, /\.shell-dev-page\s*\{[^}]*height:\s*auto/);
});
