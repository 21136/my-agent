import type { GoalStateEvent } from "../../api/ws";

const PAUSE_REASONS: Record<string, string> = {
  budget_exhausted: "该目标处于预算暂停状态",
  repair_exhausted: "修复尝试已达到上限",
  user: "你已暂停此目标",
  timeout: "执行因等待超时而暂停",
  cancelled: "执行已取消",
  budget_extended: "预算已更新，任务尚未恢复",
  process_restart: "应用重启中断了此前的执行",
};

const HISTORICAL_PAUSE_PATTERNS = [
  /目标已暂停\s*[（(]\s*budget_exhausted\s*[）)]/i,
  /(?:任务|目标)已暂停.{0,120}(?:应用重启中断了此前的执行|budget_exhausted)/s,
  /应用重启中断了此前的执行/,
  /目标保持安全暂停/,
  /本回合工具执行额度已用尽/,
  /上一回合因预算上限暂停/,
];

/** Adds a neutral transcript label; it deliberately makes no claim about current goal state. */
export function renderHistoricalGoalPauseMarker(text: string): string {
  if (!HISTORICAL_PAUSE_PATTERNS.some((pattern) => pattern.test(text))) return "";
  return '<small class="unified-message-history-status" role="note">历史暂停记录 · 仅描述当时状态，不代表当前状态</small>';
}

const MANUALLY_RECOVERABLE_PAUSES = new Set([
  "budget_exhausted",
  "process_restart",
  "budget_extended",
]);

function escapeHtml(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]!);
}

/** Render a compact pause notice without making recovery decisions on the server's behalf. */
export function renderGoalBudgetNotice(goal: GoalStateEvent): string {
  if (goal.status !== "paused") return "";

  const reason = goal.pause_reason
    ? (PAUSE_REASONS[goal.pause_reason] || `暂停原因：${goal.pause_reason}`)
    : "暂停原因未提供";
  const escapedReason = escapeHtml(reason);
  const canManualRequestRecover = goal.pause_reason !== null
    && MANUALLY_RECOVERABLE_PAUSES.has(goal.pause_reason);
  const guidance = canManualRequestRecover
    ? goal.pause_reason === "process_restart"
      ? "检查已保存的进展后，可明确发送下一步请求恢复处理。权限确认仍按原规则执行。"
      : goal.pause_reason === "budget_extended"
        ? "预算已更新；检查当前进展后，可明确发送下一步请求继续处理。权限确认仍按原规则执行。"
        : "先检查当前进展与已完成的改动；需要继续时，明确发送下一步请求。权限确认仍按原规则执行。"
    : "原目标保持暂停，普通请求不会解除该暂停。请先检查当前进展；如需新建目标，在当前会话选择动手模式，在输入框发送 /goal new: &lt;新目标内容&gt;（将括号内内容换成具体任务）。发送后会新建目标并开始处理，不会恢复原目标；原目标记录保留，权限与确认规则仍然适用。";

  return `<section class="goal-budget-notice is-paused" data-goal-status="paused" role="status" aria-live="polite">
    <div class="goal-budget-heading"><strong>任务已暂停</strong><span class="goal-budget-pause-reason">${escapedReason}</span></div>
    <p class="goal-budget-guidance">${guidance}</p>
  </section>`;
}

