export interface MusicViewBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}
export interface MusicViewViewport { width: number; height: number }

/** Clamp renderer-provided CSS-pixel bounds to the BrowserWindow content area. */
export function clampMusicViewBounds(raw: unknown, viewport: MusicViewViewport): MusicViewBounds | null {
  if (!raw || typeof raw !== 'object') return null;
  const rect = raw as Partial<MusicViewBounds>;
  if (![rect.x, rect.y, rect.width, rect.height, viewport.width, viewport.height].every(value => typeof value === 'number' && Number.isFinite(value))) return null;
  if (rect.width! <= 0 || rect.height! <= 0 || viewport.width <= 0 || viewport.height <= 0) return null;
  const x = Math.max(0, Math.floor(rect.x!));
  const y = Math.max(0, Math.floor(rect.y!));
  const right = Math.min(viewport.width, Math.ceil(rect.x! + rect.width!));
  const bottom = Math.min(viewport.height, Math.ceil(rect.y! + rect.height!));
  if (right <= x || bottom <= y) return null;
  return { x, y, width: right - x, height: bottom - y };
}
