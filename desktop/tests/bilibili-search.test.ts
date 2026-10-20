import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildBilibiliSearchUrl } from '../electron/bilibili-search.ts';

test('builds a fixed Bilibili search destination from a song title', () => {
  assert.equal(buildBilibiliSearchUrl('  周杰伦 夜曲  '), 'https://search.bilibili.com/all?keyword=%E5%91%A8%E6%9D%B0%E4%BC%A6+%E5%A4%9C%E6%9B%B2');
});
test('rejects missing, non-text, and overlong searches', () => {
  assert.equal(buildBilibiliSearchUrl('  '), null);
  assert.equal(buildBilibiliSearchUrl(null), null);
  assert.equal(buildBilibiliSearchUrl('x'.repeat(201)), null);
});
test('normalizes controls and whitespace without allowing destination injection', () => {
  assert.equal(buildBilibiliSearchUrl('歌\n手\t名'), 'https://search.bilibili.com/all?keyword=%E6%AD%8C+%E6%89%8B+%E5%90%8D');
  assert.match(buildBilibiliSearchUrl('https://example.com')!, /^https:\/\/search\.bilibili\.com\/all\?/);
});
import { isOfficialBilibiliUrl } from '../electron/bilibili-search.ts';

test('only HTTPS Bilibili and short-link hosts stay inside the isolated player window', () => {
  assert.equal(isOfficialBilibiliUrl('https://search.bilibili.com/all?keyword=night'), true);
  assert.equal(isOfficialBilibiliUrl('https://www.bilibili.com/video/BV1xx'), true);
  assert.equal(isOfficialBilibiliUrl('https://b23.tv/abc123'), true);
  assert.equal(isOfficialBilibiliUrl('http://www.bilibili.com/video/BV1xx'), false);
  assert.equal(isOfficialBilibiliUrl('https://bilibili.com.attacker.example/'), false);
  assert.equal(isOfficialBilibiliUrl('https://attacker.example/bilibili.com'), false);
  assert.equal(isOfficialBilibiliUrl('https://user@www.bilibili.com/'), false);
  assert.equal(isOfficialBilibiliUrl('https://www.bilibili.com:8443/'), false);
});
