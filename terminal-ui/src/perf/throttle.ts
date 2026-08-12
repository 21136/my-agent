export type Throttled<T extends (...args: never[]) => void> = T & {
  cancel: () => void;
  flush: () => void;
};

export function throttle<T extends (...args: never[]) => void>(
  callback: T,
  waitMs = 16,
): Throttled<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let lastRun = 0;
  let pendingArgs: Parameters<T> | undefined;

  const run = () => {
    timer = undefined;
    if (!pendingArgs) return;
    const args = pendingArgs;
    pendingArgs = undefined;
    lastRun = Date.now();
    callback(...args);
  };

  const throttled = ((...args: Parameters<T>) => {
    pendingArgs = args;
    const elapsed = Date.now() - lastRun;
    if (!lastRun || elapsed >= waitMs) {
      if (timer) clearTimeout(timer);
      run();
      return;
    }
    if (!timer) timer = setTimeout(run, waitMs - elapsed);
  }) as Throttled<T>;

  throttled.cancel = () => {
    if (timer) clearTimeout(timer);
    timer = undefined;
    pendingArgs = undefined;
  };

  throttled.flush = () => {
    if (timer) clearTimeout(timer);
    run();
  };

  return throttled;
}
