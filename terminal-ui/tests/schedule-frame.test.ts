import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createFrameScheduler} from '../src/perf/schedule-frame.js';

test('createFrameScheduler coalesces multiple schedules into one callback', async () => {
  let calls = 0;
  const scheduler = createFrameScheduler();
  scheduler.schedule(() => {
    calls += 1;
  });
  scheduler.schedule(() => {
    calls += 1;
  });
  await new Promise((resolve) => setTimeout(resolve, 32));
  assert.equal(calls, 1);
  scheduler.cancel();
});
