import assert from 'node:assert/strict';
import { test } from 'node:test';
import { clampMusicViewBounds } from '../electron/music-view-bounds.ts';
import { musicPageZoomFactor, bindMusicPageZoom } from '../electron/music-view-zoom.ts';

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

test('music page zoom is bounded to the Bilibili viewport and resizes with the view', () => {
  assert.equal(musicPageZoomFactor(870), 0.725);
  assert.equal(musicPageZoomFactor(1200), 1);
  assert.equal(musicPageZoomFactor(640), 0.7);
  assert.equal(musicPageZoomFactor(Number.NaN), 1);
});

test('music page zoom is restored after navigation and uses the current view width', () => {
  const listeners = new Map<string, Array<() => void>>();
  let zoom = 1;
  let width = 870;
  const contents = {
    on: (event: string, listener: () => void) => {
      const entries = listeners.get(event) ?? [];
      entries.push(listener);
      listeners.set(event, entries);
    },
    setZoomFactor: (factor: number) => { zoom = factor; },
  };
  const sync = bindMusicPageZoom(contents, () => width);
  const emit = (event: string) => listeners.get(event)?.forEach(listener => listener());

  assert.deepEqual([...listeners.keys()].sort(), ['did-finish-load', 'did-navigate']);
  emit('did-navigate');
  assert.equal(zoom, 0.725);
  width = 1000;
  sync();
  assert.equal(zoom, 0.833);
  zoom = 1;
  emit('did-finish-load');
  assert.equal(zoom, 0.833);
});
