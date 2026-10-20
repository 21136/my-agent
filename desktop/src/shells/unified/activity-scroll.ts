export interface VerticalScrollBounds {
  top: number;
  bottom: number;
}

/** Return the smallest vertical scroll delta that reveals a target in a viewport. */
export function activityRevealScrollDelta(
  container: VerticalScrollBounds,
  target: VerticalScrollBounds,
  inset = 12,
): number {
  const containerHeight = Math.max(0, container.bottom - container.top);
  const safeInset = Math.min(Math.max(0, inset), containerHeight / 2);
  const visibleTop = container.top + safeInset;
  const visibleBottom = container.bottom - safeInset;
  const targetHeight = Math.max(0, target.bottom - target.top);

  if (targetHeight > visibleBottom - visibleTop) return target.top - visibleTop;
  if (target.top < visibleTop) return target.top - visibleTop;
  if (target.bottom > visibleBottom) return target.bottom - visibleBottom;
  return 0;
}
