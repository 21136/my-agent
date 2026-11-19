import type { ChildProcess } from "node:child_process";

export type SidecarShutdownOutcome =
  | { status: "exited"; forced: boolean }
  | { status: "spawn-failed" }
  | { status: "unknown"; forced: boolean };
const pendingStops = new WeakMap<ChildProcess, Promise<SidecarShutdownOutcome>>();

/** End the dedicated sidecar's stdin lifetime pipe, then use bounded, owned-child fallback. */
export function stopSidecarGracefully(child: ChildProcess, graceMs: number, killWaitMs = 2_000): Promise<SidecarShutdownOutcome> {
  const pending = pendingStops.get(child);
  if (pending) return pending;
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve({ status: "exited", forced: false });
  const stop = new Promise<SidecarShutdownOutcome>((resolve) => {
    let forced = false;
    let spawnFailed = false;
    let settled = false;
    let graceTimer: NodeJS.Timeout;
    let killWaitTimer: NodeJS.Timeout | undefined;
    const finish = (outcome: SidecarShutdownOutcome) => {
      if (settled) return;
      settled = true;
      clearTimeout(graceTimer);
      if (killWaitTimer) clearTimeout(killWaitTimer);
      child.off("exit", onExit);
      child.off("close", onClose);
      child.off("error", onError);
      resolve(outcome);
    };
    const onExit = () => finish(spawnFailed ? { status: "spawn-failed" } : { status: "exited", forced });
    const onClose = () => finish(spawnFailed ? { status: "spawn-failed" } : { status: "exited", forced });
    const onError = () => { if (child.pid === undefined) spawnFailed = true; };
    const forceAfterGrace = () => {
      if (child.exitCode !== null || child.signalCode !== null) {
        finish({ status: "exited", forced: false });
        return;
      }
      forced = true;
      if (child.pid !== undefined) {
        try { child.kill(); } catch { /* still wait for exit/close before reporting */ }
      }
      killWaitTimer = setTimeout(() => finish({ status: "unknown", forced: true }), Math.max(0, killWaitMs));
    };
    graceTimer = setTimeout(forceAfterGrace, Math.max(0, graceMs));
    child.once("exit", onExit).once("close", onClose).on("error", onError);
    try {
      if (child.stdin && !child.stdin.destroyed && child.stdin.writable) child.stdin.end();
    } catch {
      // A closed pipe is equivalent to EOF; bounded terminal-event waits still apply.
    }
  });
  pendingStops.set(child, stop);
  return stop;
}
