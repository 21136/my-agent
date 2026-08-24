import assert from 'node:assert/strict';
import {test} from 'node:test';
import {restoreClippedMarkdownFence, parseMarkdown} from '../src/markdown.js';
import {sliceTextByWrappedRows} from '../src/render/display-text.js';

test('clipped fenced markdown restores the inherited code block', () => {
  const source = ['```python', 'first()', 'second()', 'third()', '```', 'after'].join('\n');
  const slice = sliceTextByWrappedRows(source, 80, 2, 2);
  const restored = restoreClippedMarkdownFence(source, slice.text, 80, 2);

  assert.deepEqual(parseMarkdown(restored).map((block) => block.kind), ['code']);
  assert.match(restored, /^```python\nsecond\(\)\nthird\(\)\n```$/);
});
