import assert from 'node:assert/strict';
import test from 'node:test';
import {formatToolElapsed} from '../src/components/formatToolElapsed.js';

test('formatToolElapsed renders seconds and minutes', () => {
  assert.equal(formatToolElapsed(0), '0s');
  assert.equal(formatToolElapsed(12), '12s');
  assert.equal(formatToolElapsed(60), '1m');
  assert.equal(formatToolElapsed(72), '1m 12s');
  assert.equal(formatToolElapsed(252), '4m 12s');
});
