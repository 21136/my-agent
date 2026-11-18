import type { ChildProcess } from "node:child_process";

export type AutoLaunchResult = { ok: true; reused: boolean } | { ok: false; message: string };

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
}) {
  let current: ChildProcess | undefined;
  let pending: Promise<AutoLaunchResult> | undefined;
  let closed = false;
  let focusId = 0;
  const abort = new AbortController();
  const failure = (message: string): AutoLaunchResult => ({ ok: false, message });
  const dispose = (child: ChildProcess) => {
    // Disconnect lets the Electron child run its normal sidecar cleanup.
    if (child.connected) child.disconnect();
  };

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
        if ((!focus && value.type === "ready") || (focus && value.type === "focused" && value.id === id)) finish();
        else if (value.type === "failed" && value.code && Object.hasOwn(ONE_CLICK_FAILURES, value.code)) {
          finish(new Error(ONE_CLICK_FAILURES[value.code]));
        }
      };
      function finish(error?: Error) {
        clearTimeout(timer);
        child.off("message", message).off("exit", gone).off("disconnect", gone).off("error", gone);
        if (error) reject(error); else resolve();
      }
      child.on("message", message).once("exit", gone).once("disconnect", gone).once("error", gone);
      if (!child.connected) gone();
      else if (focus) child.send({ type: "focus", id }, (error) => { if (error) gone(); });
    });
  }

  async function run(): Promise<AutoLaunchResult> {
    let child = current;
    const reused = Boolean(child?.connected);
    try {
      if (!child?.connected) {
        await options.prepare(abort.signal);
        if (closed) return failure("稳定桌面正在退出。");
        child = current = options.start();
        const spawned = child;
        // Keep a handler after readiness too; transport errors must not crash the stable shell.
        spawned.on("error", () => {});
        const clear = () => { if (current === spawned) current = undefined; };
        spawned.once("exit", clear).once("disconnect", clear);
      }
      await exchange(child, reused);
      return { ok: true, reused };
    } catch (error) {
      if (child && !reused) dispose(child);
      return failure(closed ? "稳定桌面正在退出。" : error instanceof Error ? error.message : "无法启动自动改造。");
    }
  }

  return {
    get active() { return Boolean(pending || current?.connected); },
    launch(): Promise<AutoLaunchResult> {
      if (closed) return Promise.resolve(failure("稳定桌面正在退出。"));
      if (pending) return pending;
      pending = run().finally(() => { pending = undefined; });
      return pending;
    },
    close() {
      closed = true;
      abort.abort();
      if (current) dispose(current);
    },
  };
}
