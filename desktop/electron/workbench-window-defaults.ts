/**
 * Default outer BrowserWindow size for the desktop workbench cold start.
 * Opening splash uses the same dimensions so hide/show does not resize the window.
 */
export const WORKBENCH_DEFAULT_WIDTH = 960;
export const WORKBENCH_DEFAULT_HEIGHT = 720;

export function workbenchDefaultWindowSize(): { width: number; height: number } {
  return { width: WORKBENCH_DEFAULT_WIDTH, height: WORKBENCH_DEFAULT_HEIGHT };
}
