export type FrameScheduler = {
  schedule: (callback: () => void) => void;
  cancel: () => void;
};

/** Coalesce hot-path UI paints to one callback per animation frame. */
export function createFrameScheduler(): FrameScheduler {
  let handle: ReturnType<typeof setTimeout> | number | undefined;

  const cancel = () => {
    if (handle === undefined) return;
    if (typeof handle === 'number' && typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(handle);
    } else {
      clearTimeout(handle as ReturnType<typeof setTimeout>);
    }
    handle = undefined;
  };

  const schedule = (callback: () => void) => {
    if (handle !== undefined) return;
    if (typeof requestAnimationFrame === 'function') {
      handle = requestAnimationFrame(() => {
        handle = undefined;
        callback();
      });
      return;
    }
    handle = setTimeout(() => {
      handle = undefined;
      callback();
    }, 16);
  };

  return {schedule, cancel};
}
