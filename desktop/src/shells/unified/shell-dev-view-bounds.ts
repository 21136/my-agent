export type ShellDevBoundsRect = { left: number; top: number; right: number; bottom: number };

export function shellDevPreviewBounds(input: {
  slot: ShellDevBoundsRect;
  preview: ShellDevBoundsRect;
  viewport: ShellDevBoundsRect;
}): { x: number; y: number; width: number; height: number } | null {
  const left = Math.max(input.slot.left, input.preview.left, input.viewport.left);
  const top = Math.max(input.slot.top, input.preview.top, input.viewport.top);
  const right = Math.min(input.slot.right, input.preview.right, input.viewport.right);
  const bottom = Math.min(input.slot.bottom, input.preview.bottom, input.viewport.bottom);
  if (right <= left || bottom <= top) return null;
  return { x: left, y: top, width: right - left, height: bottom - top };
}
