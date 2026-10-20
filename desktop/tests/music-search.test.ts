import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { test } from 'node:test';
import { isRailTab, mainFocusForRail, normalizeRailTab } from '../src/shells/unified/rail.ts';
const urlModule = new URL('../electron/bilibili-search.ts', import.meta.url);
const musicModule = new URL('../src/shells/unified/music-panel.ts', import.meta.url);

test('music gets a dedicated rail destination without changing existing destinations', () => {
  assert.equal(mainFocusForRail('music'), 'music');
  assert.equal(isRailTab('music'), true);
  assert.equal(normalizeRailTab('music'), 'music');
  assert.equal(mainFocusForRail('now'), 'chat');
  assert.equal(mainFocusForRail('docs'), 'document');
  assert.equal(mainFocusForRail('projects'), 'projects');
  assert.equal(mainFocusForRail('settings'), 'settings');
});
test('Bilibili search accepts a song name and builds a fixed official search URL', async () => {
  assert.ok(existsSync(urlModule), 'Bilibili search URL builder must exist');
  const { buildBilibiliSearchUrl } = await import(urlModule.href);
  assert.equal(buildBilibiliSearchUrl('  周杰伦 夜曲  '), 'https://search.bilibili.com/all?keyword=%E5%91%A8%E6%9D%B0%E4%BC%A6+%E5%A4%9C%E6%9B%B2');
  assert.equal(buildBilibiliSearchUrl('  '), null);
  assert.equal(buildBilibiliSearchUrl('a'.repeat(201)), null);
});
test('music panel searches by title without requiring a pasted BV link', async () => {
  assert.ok(existsSync(musicModule), 'music panel must exist');
  const { renderMusicPanel } = await import(musicModule.href);
  const html = renderMusicPanel();
  assert.match(html, /data-music-search-form/);
  assert.match(html, /id="music-search-query"/);
  assert.match(html, /输入歌名或歌手/);
  assert.doesNotMatch(html, /placeholder="[^"]*BV|name="bvid"/);
});

