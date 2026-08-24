import assert from 'node:assert/strict';
import test from 'node:test';
import {
  estimateLiveTranscriptRows,
  maxTranscriptScrollUp,
  scrollTranscriptRows,
} from '../src/perf/virtual-list.js';

test('scrollTranscriptRows clamps within transcript bounds', () => {
  assert.equal(scrollTranscriptRows(0, 5, 10), 5);
  assert.equal(scrollTranscriptRows(8, 5, 10), 10);
  assert.equal(scrollTranscriptRows(4, -6, 10), 0);
});

test('scroll bounds expose review mode whenever content exceeds the viewport', () => {
  const blocks = [
    {kind: 'user', text: 'one'},
    {kind: 'user', text: 'two'},
    {kind: 'user', text: 'three'},
  ] as const;
  const maxScrollUp = maxTranscriptScrollUp(blocks, 2, 80);
  assert.equal(maxScrollUp > 0, true);
  assert.equal(scrollTranscriptRows(0, 1, maxScrollUp), 1);
});

test('scroll bounds include live assistant output', () => {
  const liveRows = estimateLiveTranscriptRows('正在分析', 'x'.repeat(240), 80);
  assert.ok(liveRows > 0);
  assert.ok(maxTranscriptScrollUp([], 4, 80, liveRows) > 0);
  assert.ok(
    maxTranscriptScrollUp(
      [{kind: 'assistant_streaming', name: '打工仔', body: '', turnIndex: 1}],
      4,
      80,
      liveRows,
    ) > 0,
  );
});
