/** Default workbench content size — uniform zoom-out when the embed slot is smaller. */
export const SHELL_DEV_REF_WIDTH = 960;
export const SHELL_DEV_REF_HEIGHT = 720;

export function shellDevEmbedZoom(width: number, height: number): number {
  if (width <= 0 || height <= 0) return 1;
  const scale = Math.min(width / SHELL_DEV_REF_WIDTH, height / SHELL_DEV_REF_HEIGHT);
  return Math.max(0.5, Math.min(1, scale));
}
