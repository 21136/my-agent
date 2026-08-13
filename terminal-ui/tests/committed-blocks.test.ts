import assert from 'node:assert/strict';
import {test} from 'node:test';
import {
  committedTranscriptBlocks,
  filterExpiredNotices,
  isEphemeralPlanNotice,
  trailingAssistantStreaming,
  trailingThinkingActive,
} from '../src/repl/committed-blocks.js';

test('committedTranscriptBlocks strips live thinking and assistant overlays', () => {
  const blocks = [
    {kind: 'user', text: 'hi'},
    {kind: 'thinking', text: '', collapsed: false},
  ] as const;
  assert.deepEqual(
    committedTranscriptBlocks(blocks, {stripActiveThinking: true}),
    [{kind: 'user', text: 'hi'}],
  );

  const streaming = [
    ...blocks.slice(0, -1),
    {kind: 'thinking', text: 'done', collapsed: true},
    {kind: 'assistant_streaming', name: '打工仔', body: 'partial', turnIndex: 1},
  ] as const;
  assert.deepEqual(
    committedTranscriptBlocks(streaming, {stripStreamingAssistant: true}),
    [
      {kind: 'user', text: 'hi'},
      {kind: 'thinking', text: 'done', collapsed: true},
    ],
  );
});

test('trailing live flags follow working state and live assistant buffer', () => {
  const blocks = [{kind: 'thinking', text: '', collapsed: false}] as const;
  assert.equal(trailingThinkingActive(blocks, true), true);
  assert.equal(trailingThinkingActive(blocks, false), false);
  assert.equal(trailingThinkingActive([], true, '', 'auto-plan · pro'), true);
  assert.equal(trailingThinkingActive([], true, 'reasoning'), true);
  assert.equal(trailingAssistantStreaming(blocks, true, 'stream'), true);
  assert.equal(trailingAssistantStreaming(blocks, true, ''), false);
});

test('ephemeral plan notices hide after ttl', () => {
  const text = '[Terminal] auto-plan 判定 · reason';
  assert.equal(isEphemeralPlanNotice(text), true);
  const blocks = [{kind: 'notice', text, ephemeral: true, shownAt: 1000}] as const;
  assert.equal(filterExpiredNotices(blocks, 1000 + 4999).length, 1);
  assert.equal(filterExpiredNotices(blocks, 1000 + 5000).length, 0);
});
