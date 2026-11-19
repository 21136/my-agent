import type { ChildProcess, SpawnOptions } from "node:child_process";

export type AutoLaunchResult = { ok: true; reused: boolean } | { ok: false; message: string };
export type LauncherShutdownOutcome =
  | { status: "not-running" }
  | { status: "exited"; forced: boolean }
  | { status: "spawn-failed" }
  | { status: "unknown"; forced: boolean };

export function oneClickChildSpawnOptions(input: { cwd: string; env: NodeJS.ProcessEnv }): SpawnOptions {
  return { cwd: input.cwd, env: input.env, stdio: ["ignore", "ignore", "ignore", "ipc"], windowsHide: false };
}

export const ONE_CLICK_FAILURES = {
  sidecar: "实验运行时未能启用自动模式。请检查实验区运行时、档案和 0x567-flash 配置。",
  renderer: "实验窗口加载失败。请保持稳定桌面运行，再点击重试。",
  occupied: "实验会话已被其他窗口或终端占用，请先退出该实验会话。",
} as const;

export function createAutoEvolutionLauncher(options: {
  prepare: (signal: AbortSignal) => Promise<void>;
  start: () => ChildProcess;
  readyMs: number;
  focusMs?: number;
  shutdownMs?: number;
  killWaitMs?: number;
}) {
  let current: ChildProcess | undefined;
  let pending: Promise<AutoLaunchResult> | undefined;
  let closed = false;
  let focusId = 0;
  let closePromise: Promise<LauncherShutdownOutcome> | undefined;
  let cancelActiveExchange: (() => void) | undefined;
  let cancelPendingPreparation: (() => void) | undefined;
  const abort = new AbortController();
  const failure = (message: string): AutoLaunchResult => ({ ok: false, message });
  const dispose = (child: ChildProcess) => {
    // Disconnect lets the Electron child run its normal sidecar cleanup.
    if (child.connected) child.disconnect();
  };
  function waitForExit(child: ChildProcess, waitMs: number): Promise<boolean> {
    if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true);
    return new Promise((resolve) => {
      let settled = false;
      const finish = (exited: boolean) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        child.off("exit", onExit).off("close", onExit);
        resolve(exited);
      };
      const onExit = () => finish(true);
      const timer = setTimeout(() => finish(false), waitMs);
      child.once("exit", onExit).once("close", onExit);
      if (child.exitCode !== null || child.signalCode !== null) finish(true);
    });
  }

  function exchange(child: ChildProcess, focus: boolean): Promise<void> {
    return new Promise((resolve, reject) => {
      const id = ++focusId;
      const timer = setTimeout(() => finish(new Error(focus
        ? "实验窗口未响应，请关闭实验窗口后重试。"
        : "实验窗口启动超时，请检查 Docker 和实验运行时后重试。")), focus ? options.focusMs ?? 5000 : options.readyMs);
      const gone = () => finish(new Error("实验窗口未能保持运行，请重试。"));
      const message = (raw: unknown) => {
        if (!raw || typeof raw !== "object") return;
        const value = raw as { type?: string; id?: number; code?: keyof typeof ONE_CLICK_FAILURES };
        if ((!focus && value.type === "renderer-ready") || (focus && value.type === "focused" && value.id === id)) finish();
        else if (value.type === "failed" && value.code && Object.hasOwn(ONE_CLICK_FAILURES, value.code)) {
          finish(new Error(ONE_CLICK_FAILURES[value.code]));
        }
      };
      function finish(error?: Error) {
        clearTimeout(timer);
        child.off("message", message).off("exit", gone).off("close", gone).off("disconnect", gone).off("error", gone);
        if (cancelActiveExchange === cancel) cancelActiveExchange = undefined;
        if (error) reject(error); else resolve();
      }
      const cancel = () => finish(new Error("稳定桌面正在退出。"));
      cancelActiveExchange = cancel;
      child.on("message", message).once("exit", gone).once("close", gone).once("disconnect", gone).once("error", gone);
      if (!child.connected) gone();
      else if (focus) child.send({ type: "focus", id }, (error) => { if (error) gone(); });
    });
  }

  async function run(): Promise<AutoLaunchResult> {
    if (current && !current.connected) {
      const previous = current;
      if (!await waitForExit(previous, options.shutdownMs ?? 5000)) return failure("实验窗口正在关闭，请稍后重试。可等窗口退出后再启动。");
      if (current === previous) current = undefined;
      if (closed) return failure("稳定桌面正在退出。");
    }
    let child = current;
    const reused = Boolean(child?.connected);
    try {
      if (!child?.connected) {
        const preparation = options.prepare(abort.signal);
        const cancelled = new Promise<never>((_resolve, reject) => {
          cancelPendingPreparation = () => reject(new Error("稳定桌面正在退出。"));
        });
        try { await Promise.race([preparation, cancelled]); }
        finally { cancelPendingPreparation = undefined; }
        if (closed) return failure("稳定桌面正在退出。");
        child = current = options.start();
        const spawned = child;
        // Keep a handler after readiness too; transport errors must not crash the stable shell.
        spawned.on("error", () => {});
        const clear = () => { if (current === spawned) current = undefined; };
        spawned.on("error", () => { if (spawned.pid === undefined) clear(); });
        spawned.once("exit", clear).once("close", clear);
      }
      await exchange(child, reused);
      return { ok: true, reused };
    } catch (error) {
      if (child && child.connected) dispose(child);
      return failure(closed ? "稳定桌面正在退出。" : error instanceof Error ? error.message : "无法启动自动改造。");
    }
  }

  return {
    get active() { return Boolean(pending || current); },
    launch(): Promise<AutoLaunchResult> {
      if (closed) return Promise.resolve(failure("稳定桌面正在退出。"));
      if (pending) return pending;
      pending = run().finally(() => { pending = undefined; });
      return pending;
    },
    close(): Promise<LauncherShutdownOutcome> {
      closed = true;
      abort.abort();
      if (closePromise) return closePromise;
      const child = current;
      const launchPending = pending;
      if (!child) {
        if (!launchPending) return closePromise = Promise.resolve({ status: "not-running" });
        closePromise = new Promise<LauncherShutdownOutcome>((resolve) => {
          let settled = false;
          let graceTimer: NodeJS.Timeout;
          let killWaitTimer: NodeJS.Timeout | undefined;
          const finish = (outcome: LauncherShutdownOutcome) => {
            if (settled) return;
            settled = true;
            clearTimeout(graceTimer);
            if (killWaitTimer) clearTimeout(killWaitTimer);
            resolve(outcome);
          };
          graceTimer = setTimeout(() => {
            killWaitTimer = setTimeout(() => {
              cancelPendingPreparation?.();
              finish({ status: "unknown", forced: false });
            }, options.killWaitMs ?? 2_000);
          }, options.shutdownMs ?? 5_000);
          void launchPending.then(() => finish({ status: "not-running" }), () => finish({ status: "not-running" }));
        });
        return closePromise;
      }
      closePromise = new Promise<LauncherShutdownOutcome>((resolve) => {
        cancelActiveExchange?.();
        let settled = false;
        let forced = false;
        let spawnFailed = false;
        let killWaitTimer: NodeJS.Timeout | undefined;
        const finish = (outcome: LauncherShutdownOutcome) => {
          if (settled) return;
          settled = true;
          clearTimeout(graceTimer);
          if (killWaitTimer) clearTimeout(killWaitTimer);
          child.off("exit", onTerminal).off("close", onTerminal).off("error", onError);
          if (current === child) current = undefined;
          resolve(outcome);
        };
        const terminalOutcome = (): LauncherShutdownOutcome => spawnFailed || child.pid === undefined
          ? { status: "spawn-failed" }
          : { status: "exited", forced };
        const onTerminal = () => finish(terminalOutcome());
        const onError = () => { if (child.pid === undefined) spawnFailed = true; };
        const forceStop = () => {
          if (child.exitCode !== null || child.signalCode !== null) { finish(terminalOutcome()); return; }
          forced = true;
          if (child.pid !== undefined) {
            try { child.kill(); } catch { /* still wait for a terminal event before reporting */ }
          } else {
            spawnFailed = true;
          }
          killWaitTimer = setTimeout(() => finish({ status: "unknown", forced: true }), options.killWaitMs ?? 2_000);
        };
        const graceTimer = setTimeout(forceStop, options.shutdownMs ?? 5_000);
        child.once("exit", onTerminal).once("close", onTerminal).on("error", onError);
        if (child.exitCode !== null || child.signalCode !== null) { finish(terminalOutcome()); return; }
        if (child.connected) {
          try { child.send({ type: "shutdown" }, () => { /* disconnect path and grace timer own recovery */ }); }
          catch { /* retain the graceful deadline; IPC may be closing concurrently */ }
        }
      });
      return closePromise;
    },
  };
}
