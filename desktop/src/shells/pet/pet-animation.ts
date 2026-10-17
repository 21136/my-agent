export const WORKING_FRAME_COUNT = 12;
export const WORKING_FPS = 10;

export function workingFramePath(assetRoot: string, frameIndex: number): string {
  const normalized = ((frameIndex % WORKING_FRAME_COUNT) + WORKING_FRAME_COUNT) % WORKING_FRAME_COUNT;
  return `${assetRoot}/working-${String(normalized + 1).padStart(2, "0")}.png`;
}
