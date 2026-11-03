import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const topbar = readFileSync(new URL("../src/shells/unified/topbar.ts", import.meta.url), "utf8");
const cssPath = new URL("../src/shells/unified/topbar-menu.css", import.meta.url);
const index = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");

 test("topbar exposes concise destinations directly and no longer renders the overflow overlay", () => {
  for (const [id, label] of [["docs", "文档"], ["music", "听歌"], ["shell-dev", "双车道"], ["settings", "设置"]]) {
    assert.ok(topbar.includes(`["${id}", "${label}"]`));
  }
  assert.doesNotMatch(topbar, /<details class="unified-topbar-menu"/);
  assert.doesNotMatch(topbar, /aria-label="更多操作"/);
  assert.doesNotMatch(topbar, /全局模型选择|自动识别语义/);
  assert.doesNotMatch(topbar, /终端与服务/);
  assert.match(topbar, /unified-topbar-shortcut/);
  assert.match(index, /settings-open-runtime-status/);
});

test("topbar shortcuts remain compact and keyboard-visible", () => {
  const css = readFileSync(cssPath, "utf8");
  assert.match(css, /\.unified-topbar-shortcut/);
  assert.match(css, /\.unified-topbar-shortcut:focus-visible/);
  assert.match(css, /@media \(max-width: 520px\)/);
});
