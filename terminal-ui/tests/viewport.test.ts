import assert from 'node:assert/strict';
import {test} from 'node:test';
import {
  formatThinkingLines,
  sliceTextByWrappedRows,
  stripInlineMarkdown,
  tailLines,
} from '../src/render/display-text.js';
import {
  getViewportBlockEntries,
  transcriptRowBudget,
} from '../src/perf/virtual-list.js';
import type {TerminalBlock} from '../src/types.js';

test('stripInlineMarkdown removes reasoning emphasis markers', () => {
  assert.equal(
    stripInlineMarkdown('**Summarizing** my-llm `project`'),
    'Summarizing my-llm project',
  );
});

test('formatThinkingLines wraps wide reasoning rows', () => {
  const long = 'x'.repeat(120);
  const lines = formatThinkingLines(long, 80);
  assert.ok(lines.length > 1);
  assert.ok(lines.every((line) => line.length <= 70));
});

test('tailLines keeps the newest reasoning tail', () => {
  const {text, clipped} = tailLines('a\nb\nc\nd', 2);
  assert.equal(text, 'c\nd');
  assert.equal(clipped, true);
});

test('viewport keeps the newest blocks when transcript exceeds budget', () => {
  const blocks: TerminalBlock[] = [
    {kind: 'user', text: 'old question'},
    {kind: 'thinking', text: 'old trace'},
    {kind: 'assistant', name: '打工仔', body: 'old answer', turnIndex: 1},
    {kind: 'user', text: 'new question'},
    {kind: 'thinking', text: 'fresh trace line'},
    {kind: 'assistant_streaming', name: '打工仔', body: 'streaming…', turnIndex: 2},
  ];
  const {entries, clippedTop} = getViewportBlockEntries(blocks, 4, 80);
  assert.equal(clippedTop, true);
  assert.ok(entries.some((entry) => entry.block.kind === 'assistant_streaming'));
  assert.ok(!entries.some((entry) => entry.index === 0));
});

test('sliceTextByWrappedRows exposes middle of a long assistant reply', () => {
  const body = Array.from({length: 30}, (_, index) => `line-${index + 1}`).join('\n');
  const middle = sliceTextByWrappedRows(body, 80, 10, 5);
  assert.equal(middle.text, Array.from({length: 5}, (_, index) => `line-${index + 11}`).join('\n'));
  assert.equal(middle.clippedTop, true);
  assert.equal(middle.clippedBottom, true);
});

test('viewport scrollUpRows reveals older transcript blocks', () => {
  const blocks: TerminalBlock[] = [
    {kind: 'user', text: 'old question'},
    {kind: 'thinking', text: 'old trace'},
    {kind: 'assistant', name: '打工仔', body: 'old answer', turnIndex: 1},
    {kind: 'user', text: 'new question'},
    {kind: 'thinking', text: 'fresh trace line'},
    {kind: 'assistant_streaming', name: '打工仔', body: 'streaming…', turnIndex: 2},
  ];
  const tail = getViewportBlockEntries(blocks, 4, 80, 0);
  const scrolled = getViewportBlockEntries(blocks, 4, 80, 20);
  assert.equal(tail.clippedBottom, false);
  assert.equal(scrolled.clippedBottom, true);
  assert.ok(scrolled.entries.some((entry) => entry.index === 0));
  assert.ok(!tail.entries.some((entry) => entry.index === 0));
});

test('transcriptRowBudget reserves footer space', () => {
  assert.ok(transcriptRowBudget(24, true) < 24);
  assert.ok(transcriptRowBudget(24, false) < transcriptRowBudget(40, false));
});
