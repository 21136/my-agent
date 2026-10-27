import { createManagedReviewCatalog } from "./managed-review-catalog.ts";

export type ManagedReviewResult = { ok: true; report: Record<string, any> } | { ok: false; message: string };

export function serializeManagedReview(report: Record<string, any>): string {
  const history = Array.isArray(report.history?.paths) ? report.history.paths.length : 0;
  const delta = Array.isArray(report.effectiveDelta?.paths) ? report.effectiveDelta.paths.length : 0;
  const summarizeInventory = (inventory: { entries?: Record<string, unknown>[] } | undefined) => {
    const entries = Array.isArray(inventory?.entries) ? inventory.entries : [];
    return {
      scanned: entries.length,
      rawDiverged: entries.filter((entry) => entry.rawDiverged === true).length,
      staged: entries.filter((entry) => entry.staged === true).length,
      untracked: entries.filter((entry) => entry.state === "untracked").length,
      missing: entries.filter((entry) => entry.state === "missing_working_file").length,
      modeDiverged: entries.filter((entry) => entry.modeDiverged === true).length,
    };
  };
  const stable = summarizeInventory(report.stable?.inventory);
  const sandbox = summarizeInventory(report.sandbox?.inventory);
  const counts = (label: string, inventory: ReturnType<typeof summarizeInventory>) =>
    `${label}：扫描条目总数 ${inventory.scanned} 项；原始字节差异 ${inventory.rawDiverged} 项；已暂存 ${inventory.staged} 项；未跟踪 ${inventory.untracked} 项；工作文件缺失 ${inventory.missing} 项；模式差异 ${inventory.modeDiverged} 项`;
  const blockers = Array.isArray(report.blockers) ? report.blockers.length : 0;
  return [
    "只读源码审查 · 摘要",
    `审查摘要：${String(report.review_digest ?? "未知")}`,
    `历史触及路径（原始提交范围）：${history} 项`,
    `当前净树差异（merge-base → sandbox HEAD）：${delta} 项`,
    `原始字节本地差异（sandbox；不等同 Git clean-filter/EOL 状态）：${sandbox.rawDiverged} 项`,
    "两侧统计可重叠；用于审查，不代表已保留或已确认任务归属。",
    counts("稳定区保留审查", stable),
    counts("sandbox 归属审查", sandbox),
    `阻塞项：${blockers} 项`,
    "执行=false · 可合入=false · 可启动=false；审查摘要不是执行收据。",
    "完整报告（下方 JSON 含未截断的精确路径）：",
    JSON.stringify(report, null, 2),
  ].join("\n");
}

function clearSafely(callback?: () => void): void {
  try { callback?.(); } catch { /* UI reset must not strand the controller in a busy state. */ }
}

export function createManagedReviewController(options: {
  inspect: () => Promise<ManagedReviewResult>;
  button: { disabled: boolean };
  output: { textContent: string | null; hidden: boolean };
  isDesktop: boolean;
  onReset?: () => void;
  onReport?: (report: Record<string, any>) => void;
}) {
  let busy = false;
  return {
    async inspect(): Promise<void> {
      if (busy) return;
      busy = true;
      options.button.disabled = true;
      try {
        clearSafely(options.onReset);
        options.output.textContent = "";
        options.output.hidden = true;
        if (!options.isDesktop) {
          options.output.textContent = "需要 Electron 桌面版才能读取受限的管理源码审查报告；网页预览不会发起网络或 Git 请求。";
          options.output.hidden = false;
          return;
        }
        const result = await options.inspect();
        if (result.ok === false) {
          options.output.textContent = result.message;
          options.output.hidden = false;
          return;
        }
        if (options.onReport) {
          try { createManagedReviewCatalog(result.report); }
          catch {
            let fullText: string;
            try { fullText = JSON.stringify(result.report, null, 2); }
            catch { fullText = "报告无法序列化为 JSON。"; }
            options.output.textContent = `只读审查目录暂不可用；以下为安全文本形式的完整报告：\n${fullText}`;
            options.output.hidden = false;
            return;
          }
        }
        try {
          // Keep the historical text-only controller contract for callers without the new browser callback.
          options.onReport?.(result.report);
          options.output.textContent = serializeManagedReview(result.report);
          options.output.hidden = Boolean(options.onReport);
        } catch {
          clearSafely(options.onReset);
          let fullText: string;
          try { fullText = JSON.stringify(result.report, null, 2); }
          catch { fullText = "报告无法序列化为 JSON。"; }
          options.output.textContent = `只读审查目录暂不可用；以下为安全文本形式的完整报告：\n${fullText}`;
          options.output.hidden = false;
        }
      } catch {
        clearSafely(options.onReset);
        options.output.textContent = "源码审查暂不可用，请检查受支持的工作区布局。";
        options.output.hidden = false;
      } finally {
        busy = false;
        options.button.disabled = false;
      }
    },
  };
}



