import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const stylesheet = readFileSync(new URL("../src/shells/unified/neon-twin.css", import.meta.url), "utf8");
const baseStylesheet = readFileSync(new URL("../src/shells/unified/unified.css", import.meta.url), "utf8");
const unifiedShell = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
const welcomeThemeAssets = [
  ["light", "presence-light.png"],
  ["dark", "presence-dark.png"],
] as const;
const chatThemeAssets = [
  ["light", "chat-background-light.png"],
  ["dark", "chat-background-dark.png"],
] as const;
const sessionSwitchAssets = [
  ["light", "session-switch-light.jpg"],
  ["dark", "session-switch-dark.jpg"],
] as const;

function assertWallpaperRule(theme: string, asset: string, stateSelector: string): void {
  const rule = new RegExp(
    `html\\[data-theme="${theme}"\\]\\s+\\.unified-shell\\[data-main-focus="chat"\\]\\s+\\.unified-main:has\\(${stateSelector}\\)\\s*\\{([^}]*)\\}`,
    "s",
  );
  const match = stylesheet.match(rule);
  assert.ok(match, `missing ${theme} wallpaper on stable unified-main`);
  assert.match(match[1], new RegExp(`url\\(\"?\\.\\/assets\\/${asset.replaceAll(".", "\\.")}`));
  assert.match(match[1], /background-size:\s*cover/);
  assert.match(match[1], /background-position:\s*center/);
}

test("welcome wallpapers are anchored to the stable outer main and cover the composer area", () => {
  for (const [theme, asset] of welcomeThemeAssets) {
    assertWallpaperRule(theme, asset, "\\.unified-presence-stage");
  }
});

test("active-chat wallpapers are anchored to the stable outer main, not the scroll viewport", () => {
  for (const [theme, asset] of chatThemeAssets) {
    assertWallpaperRule(theme, asset, "\\.unified-turn-user,\\s*\\.unified-turn-assistant");
  }
  assert.doesNotMatch(stylesheet, /\.unified-chat:has\(/);
});

test("chat stage and transcript are transparent so the fixed main wallpaper remains continuous", () => {
  assert.match(
    stylesheet,
    /html\[data-theme\]\s+\.unified-shell\[data-main-focus="chat"\]\s+\.unified-stage,\s*html\[data-theme\]\s+\.unified-shell\[data-main-focus="chat"\]\s+\.unified-chat\s*\{\s*background:\s*transparent/s,
  );
  const mainRule = baseStylesheet.match(/\.unified-main\s*\{([^}]*)\}/s);
  const chatRule = baseStylesheet.match(/(?:^|\r?\n)\.unified-chat\s*\{([^}]*)\}/s);
  assert.ok(mainRule && /height:\s*100%/.test(mainRule[1]) && /overflow:\s*hidden/.test(mainRule[1]));
  assert.ok(chatRule && /overflow-y:\s*auto/.test(chatRule[1]));
});

test("composer spans the chat width with a restrained translucent surface", () => {
  const widthRule = stylesheet.match(
    /html\[data-theme\]\s+\.unified-shell\[data-main-focus="chat"\]\s+\.unified-composer-dock\s*>\s*\*\s*\{([^}]*)\}/s,
  );
  assert.ok(widthRule, "missing full-width composer dock child rule");
  assert.match(widthRule[1], /max-width:\s*none/);
  assert.match(widthRule[1], /width:\s*100%/);
  assert.match(widthRule[1], /margin-inline:\s*0/);
  assert.match(
    stylesheet,
    /html\[data-theme\]\s+\.unified-shell\[data-main-focus="chat"\]\s+\.unified-composer\s*\{[^}]*background:\s*color-mix\([^}]*transparent/s,
  );
});

test("session switch surfaces reference themed JPG wallpapers", () => {
  for (const [theme, asset] of sessionSwitchAssets) {
    const rule = new RegExp(
      `html\\[data-theme="${theme}"\\]\\s+\\.sidebar-sessions,[\\s\\S]*?url\\(\"?\\.\\/assets\\/${asset.replaceAll(".", "\\.")}`,
    );
    assert.match(stylesheet, rule);
  }
});

test("both welcome and active-chat background assets are valid 16:9 PNG images", () => {
  for (const [, asset] of [...welcomeThemeAssets, ...chatThemeAssets]) {
    const bytes = readFileSync(new URL(`../src/shells/unified/assets/${asset}`, import.meta.url));
    assert.equal(bytes.subarray(1, 4).toString("ascii"), "PNG");
    const width = bytes.readUInt32BE(16);
    const height = bytes.readUInt32BE(20);
    assert.ok(Math.abs(width / height - 16 / 9) < 0.01, `${asset} should be 16:9`);
  }
});

test("empty presence content still renders before turn-card layout handles project chats", () => {
  const renderStart = unifiedShell.indexOf("function doRender(): void");
  const emptyGuard = unifiedShell.indexOf("if (!curPrints.length && !chat.isWorking())", renderStart);
  const turnCardBranch = unifiedShell.indexOf("if (useTurnCardLayout())", renderStart);
  assert.ok(renderStart >= 0, "doRender must exist");
  assert.ok(emptyGuard > renderStart && emptyGuard < turnCardBranch, "empty state must be handled before project turn-card rendering");
  const emptyBranch = unifiedShell.slice(emptyGuard, turnCardBranch);
  assert.match(emptyBranch, /const presenceHtml = renderBlocksGrouped\(\[\]\)/);
  assert.match(emptyBranch, /chatEl\.innerHTML\s*=\s*presenceHtml/);
});

test("initial shell marks its default chat focus before any navigation event", () => {
  const shellOpen = unifiedShell.match(/<div class="unified-shell"[^>]*>/);
  assert.ok(shellOpen, "unified shell root must exist");
  assert.match(shellOpen[0], /data-main-focus="chat"/);
});
