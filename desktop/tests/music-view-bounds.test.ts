import assert from 'node:assert/strict';
import { test } from 'node:test';
import { clampMusicViewBounds } from '../electron/music-view-bounds.ts';

test('clamps an in-project web view rectangle to the workbench content area', () => {
  assert.deepEqual(
    clampMusicViewBounds({ x: -24, y: 90, width: 480, height: 900 }, { width: 960, height: 720 }),
    { x: 0, y: 90, width: 456, height: 630 },
  );
});
test('rejects invalid or fully offscreen view bounds', () => {
  assert.equal(clampMusicViewBounds(null, { width: 960, height: 720 }), null);
  assert.equal(clampMusicViewBounds({ x: 990, y: 0, width: 100, height: 100 }, { width: 960, height: 720 }), null);
  assert.equal(clampMusicViewBounds({ x: 0, y: 0, width: Number.NaN, height: 100 }, { width: 960, height: 720 }), null);
});
