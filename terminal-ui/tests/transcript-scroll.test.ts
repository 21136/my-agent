import assert from 'node:assert/strict';
import test from 'node:test';
import {scrollTranscriptRows} from '../src/perf/virtual-list.js';

test('scrollTranscriptRows clamps within transcript bounds', () => {
  assert.equal(scrollTranscriptRows(0, 5, 10), 5);
  assert.equal(scrollTranscriptRows(8, 5, 10), 10);
  assert.equal(scrollTranscriptRows(4, -6, 10), 0);
});
