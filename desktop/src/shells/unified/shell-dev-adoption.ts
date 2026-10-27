type AdoptResult = { ok: boolean; message: string; detail?: string; steps?: string[]; merged?: boolean; synced?: boolean; recoveryStash?: string };
type AdoptionDesktopApi = {
  mergeSandboxIntoStable?: () => Promise<AdoptResult>;
  getSandboxAdoptStatus?: () => Promise<Record<string, unknown>>;
};
/** Keep view orchestration separate from the main-process authorization boundary. */
export function createShellDevAdoptionController(
  api: AdoptionDesktopApi | undefined,
  feedback: (text: string, tone?: "ok" | "error" | "busy") => void,
  setBusy: (busy: boolean) => void,
) {
  let busy = false;

  function formatResult(result: AdoptResult): string {
    return [result.message, result.steps?.join(" → "), result.detail,
      result.recoveryStash ? "本次恢复备份：" + result.recoveryStash : ""].filter(Boolean).join("\n");
  }

  async function merge(): Promise<void> {
    if (busy) return;
    if (!api?.mergeSandboxIntoStable) { feedback("请在 Electron 桌面工作台中使用合入功能；网页仅供预览。", "error"); return; }
    busy = true; setBusy(true);
    try {
      feedback("正在检查合入范围，请在桌面原生窗口确认；取消不会修改代码。", "busy");
      const result = await api.mergeSandboxIntoStable();
      feedback(formatResult(result), result.ok ? "ok" : "error");
    } catch (error) { feedback("合入未完成：" + (error instanceof Error ? error.message : String(error)), "error"); }
    finally { busy = false; setBusy(false); }
  }

  /** One-click path: refresh status first, surface fatal blockers in-panel, then same native adopt merge. */
  async function oneClick(): Promise<void> {
    if (busy) return;
    if (!api?.mergeSandboxIntoStable) { feedback("请在 Electron 桌面工作台中使用合入功能；网页仅供预览。", "error"); return; }
    busy = true; setBusy(true);
    try {
      if (api.getSandboxAdoptStatus) {
        feedback("正在检查合入状态…", "busy");
        const status = await api.getSandboxAdoptStatus();
        const blockers = Array.isArray(status.fatalBlockers) ? status.fatalBlockers : [];
        if (blockers.length) {
          feedback(blockers.map(String).join("\n"), "error");
          return;
        }
      }
      feedback("正在检查合入范围，请在桌面原生窗口确认；取消不会修改代码。", "busy");
      const result = await api.mergeSandboxIntoStable();
      feedback(formatResult(result), result.ok ? "ok" : "error");
    } catch (error) { feedback("合入未完成：" + (error instanceof Error ? error.message : String(error)), "error"); }
    finally { busy = false; setBusy(false); }
  }

  async function inspect(): Promise<void> {
    if (!api?.getSandboxAdoptStatus) { feedback("请在 Electron 桌面工作台中检查合入状态。", "error"); return; }
    try {
      const status = await api.getSandboxAdoptStatus();
      const blockers = Array.isArray(status.fatalBlockers) ? status.fatalBlockers : [];
      if (blockers.length) { feedback(blockers.map(String).join("\n"), "error"); return; }
      const count = (value: unknown) => Array.isArray(value) ? value.length : 0;
      feedback([
        "已有实验提交：" + (status.commitsAhead ?? "未知") + "；待保存文件：" + count(status.sandboxDirtyProduct),
        "稳定区待备份改动：" + count(status.stableDirtyProduct),
        "点击一键完成合入后，还会由桌面主进程展示实际文件范围并请求确认。",
        "代码合入不代表真实运行或模型验收已通过。",
      ].join("\n"));
    } catch (error) { feedback("检查失败：" + String(error), "error"); }
  }
  return { merge, oneClick, inspect };
}
