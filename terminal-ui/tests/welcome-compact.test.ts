import assert from 'node:assert/strict';
import {test} from 'node:test';
import {reduceEvents} from '../src/reduce/events.js';

/** Legacy terminal_app: welcome compacts once transcript has content (not per streaming line). */
test('welcome should compact after first transcript block', () => {
  const empty = reduceEvents([{type: 'session.init', greet: '晚上好'}]);
  assert.equal(empty.blocks.length, 0);

  const started = reduceEvents([
    {type: 'session.init', greet: '晚上好'},
    {type: 'user.message', text: '来个快速排序'},
    {type: 'turn.start'},
  ]);
  assert.ok(started.blocks.length >= 1);
});
