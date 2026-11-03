/**
 * Default outer BrowserWindow size for the desktop workbench cold start.
 * Opening splash uses the same dimensions so hide/show does not resize the window.
 */
export const WORKBENCH_DEFAULT_WIDTH = 960;
export const WORKBENCH_DEFAULT_HEIGHT = 720;

export type WorkAreaSize = { width: number; height: number };

/**
 * Scale a design size down to fit inside maxWidth x maxHeight.
 * Keeps aspect ratio; never upscales; never crops by stretching independently.
 */
export function fitSizeWithinBox(
  width: number,
  height: number,
  maxWidth: number,
  maxHeight: number,
): { width: number; height: number } {
  if (!(width > 0 && height > 0 && maxWidth > 0 && maxHeight > 0)) {
    return { width: WORKBENCH_DEFAULT_WIDTH, height: WORKBENCH_DEFAULT_HEIGHT };
  }
  const scale = Math.min(maxWidth / width, maxHeight / height, 1);
  return {
    width: Math.max(1, Math.floor(width * scale)),
    height: Math.max(1, Math.floor(height * scale)),
  };
}

export function workbenchDefaultWindowSize(
  workArea?: WorkAreaSize | null,
): { width: number; height: number } {
  if (!workArea || !(workArea.width > 0 && workArea.height > 0)) {
    return { width: WORKBENCH_DEFAULT_WIDTH, height: WORKBENCH_DEFAULT_HEIGHT };
  }
  return fitSizeWithinBox(
    WORKBENCH_DEFAULT_WIDTH,
    WORKBENCH_DEFAULT_HEIGHT,
    workArea.width,
    workArea.height,
  );
}
