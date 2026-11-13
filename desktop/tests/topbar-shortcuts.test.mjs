import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const topbarPath = new URL("../src/shells/unified/topbar.ts", import.meta.url);
const topbar = readFileSync(topbarPath, "utf8");
const index = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");

test("topbar exposes exactly the four requested direct destinations without an overflow menu", () => {
  const destinations = topbar.match(/const DESTINATIONS = \[([\s\S]*?)\] as const/)?.[1] ?? "";
  for (const [id, label] of [["docs", "文档"], ["music", "听歌"], ["shell-dev", "双车道"], ["settings", "设置"]]) {
    assert.ok(destinations.includes(`["${id}", "${label}"]`));
  }
  assert.equal((destinations.match(/\["/g) ?? []).length, 4);
  assert.doesNotMatch(topbar, /<details class="unified-topbar-menu"/);
  assert.doesNotMatch(topbar, /aria-label="更多操作"/);
  assert.match(topbar, /unified-topbar-shortcut/);
  assert.match(index, /selectSingleChatTool/);
});

test("direct topbar shortcuts keep keyboard focus visible and fit narrow windows", () => {
  const css = readFileSync(new URL("../src/shells/unified/topbar-shortcuts.css", import.meta.url), "utf8");
  assert.match(css, /\.unified-topbar-shortcut:focus-visible/);
  assert.match(css, /@media \(max-width: 520px\)/);
});

test("removing the memory entry does not remove proposal, current-work, or session paths", () => {
  for (const token of [
    "unified-open-proposal",
    "unified-topbar-current-work",
    "unified-request-current-work",
    "onOpenProposals",
    "onToggleCurrentWorkOverview",
    "onOpenSessions",
  ]) assert.ok(topbar.includes(token), `topbar should retain ${token}`);
  assert.doesNotMatch(topbar, /unified-open-memory|unified-topbar-memory-btn|onOpenMemory/);
  assert.doesNotMatch(index, /renderMemoryShelf\(expandEl|renderMemoryShelfPanel\(\);/);
  assert.match(index, /case "evolve\.memories":\s+handleMemoriesEvent\(event\.items\)/);
});
