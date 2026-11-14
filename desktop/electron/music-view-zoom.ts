import type { WebContents } from 'electron';

/** Keep Bilibili's desktop layout inside narrow music WebContentsViews only. */
const BILIBILI_LAYOUT_WIDTH = 1200;
const MIN_MUSIC_PAGE_ZOOM = 0.7;

export function musicPageZoomFactor(viewportWidth: number): number {
  if (!Number.isFinite(viewportWidth) || viewportWidth <= 0) return 1;
  const factor = Math.min(1, viewportWidth / BILIBILI_LAYOUT_WIDTH);
  return Math.max(MIN_MUSIC_PAGE_ZOOM, Math.round(factor * 1000) / 1000);
}

/** Electron resets page zoom during navigation; reapply after commit and load. */
export function bindMusicPageZoom(contents: Pick<WebContents, 'on' | 'setZoomFactor'>, getViewportWidth: () => number): () => void {
  const sync = () => contents.setZoomFactor(musicPageZoomFactor(getViewportWidth()));
  contents.on('did-navigate', sync);
  contents.on('did-finish-load', sync);
  return sync;
}
