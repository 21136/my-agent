import assert from 'node:assert/strict';
import {test} from 'node:test';
import {
  formatStreamingThinkingLines,
  formatThinkingLines,
  sliceTextByWrappedRows,
  stripInlineMarkdown,
  tailLines,
} from '../src/render/display-text.js';
import {
  getViewportBlockEntries,
  reviewNewOutputRows,
  transcriptFooterRows,
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

test('formatStreamingThinkingLines caps wrapped rows for live reasoning', () => {
  const long = Array.from({length: 20}, () => 'x'.repeat(100)).join('\n');
  const {lines, clippedTop} = formatStreamingThinkingLines(long, 80, 8);
  assert.equal(lines.length, 8);
  assert.equal(clippedTop, true);
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

test('viewport clips the top of an oversized newest block so the footer stays visible', () => {
  const blocks: TerminalBlock[] = [
    {
      kind: 'assistant',
      name: '打工仔',
      body: Array.from({length: 30}, (_, index) => `line-${index + 1}`).join('\n'),
      turnIndex: 1,
    },
  ];
  const {entries, clippedTop, clippedBottom} = getViewportBlockEntries(blocks, 5, 80);
  assert.equal(entries.length, 1);
  assert.equal(entries[0]?.skipRows, 27);
  assert.equal(entries[0]?.maxRows, 5);
  assert.equal(clippedTop, true);
  assert.equal(clippedBottom, false);
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

test('transcriptFooterRows accounts for working and review composer hints', () => {
  assert.equal(transcriptFooterRows(false, false, false), 4);
  assert.equal(transcriptFooterRows(true, false, false), 5);
  assert.equal(transcriptFooterRows(true, false, true), 6);
  assert.equal(transcriptFooterRows(false, true, true), 5);
});

test('review output count never goes below zero', () => {
  assert.equal(reviewNewOutputRows(18, 10), 8);
  assert.equal(reviewNewOutputRows(8, 10), 0);
});
