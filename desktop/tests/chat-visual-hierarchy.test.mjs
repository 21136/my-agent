import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../src/shells/unified/unified.css", import.meta.url), "utf8");

function rule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "s"));
  assert.ok(match, `missing visual hierarchy rule: ${selector}`);
  return match[1];
}

test("chat transcript keeps the wallpaper while softening it beneath reading content", () => {
  const stage = rule('html[data-theme] .unified-shell[data-main-focus="chat"][data-perspective="project"] .unified-stage');
  assert.match(stage, /background:\s*color-mix\(in srgb, var\(--ma-bg\)\s+\d+%, transparent\)/);
  const chat = rule('html[data-theme] .unified-shell[data-main-focus="chat"][data-perspective="project"] .unified-chat');
  assert.match(chat, /padding:\s*\d+(?:\.\d+)?rem\s+clamp\(/);
  assert.match(chat, /scroll-padding-block:/);
});

test("chat chrome separates navigation, status and composer without changing shell geometry", () => {
  const topbar = rule('html[data-theme] .unified-shell[data-main-focus="chat"] .unified-topbar');
  assert.match(topbar, /background:\s*color-mix\(/);
  assert.match(topbar, /border-bottom:\s*1px solid/);
  assert.match(topbar, /backdrop-filter:\s*blur\(/);
  const dock = rule('html[data-theme] .unified-shell[data-main-focus="chat"] .unified-composer-dock');
  assert.match(dock, /background:\s*color-mix\(/);
  assert.match(dock, /border-top:/);
  const meta = rule('html[data-theme] .unified-shell[data-main-focus="chat"] .unified-composer-meta');
  assert.match(meta, /padding:/);
  assert.match(meta, /border-bottom:/);
  assert.doesNotMatch(css, /\.unified-shell\s*\{[^}]*\b(?:width|min-width|max-width):/s);
});

test("assistant and user messages gain breathing room and narrow-screen gutters", () => {
  const assistant = rule('html[data-theme] .unified-shell[data-perspective="project"][data-main-focus="chat"] #unified-chat .unified-turn-assistant:is(.unified-message-bubble, .unified-message-reading)');
  assert.match(assistant, /background:\s*color-mix\(in srgb, var\(--ma-surface\)\s+\d+%, transparent\)/);
  assert.match(assistant, /backdrop-filter:\s*blur\(/);
  assert.match(css, /@media\s*\(max-width:\s*640px\)[\s\S]*?\.unified-chat\s*\{[^}]*padding:/);
  assert.match(css, /@media\s*\(max-width:\s*640px\)[\s\S]*?\.unified-turn-user[^}]*max-width:/);
});

