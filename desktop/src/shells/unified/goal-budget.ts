import type { GoalStateEvent } from "../../api/ws";

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

/**
 * Top-of-chat pause/budget banner removed: it was noisy (e.g. process_restart
 * "任务已暂停 · 应用重启中断了此前的执行") and not actionable enough for the main page.
 * Keep the helper so goal.state handlers stay stable; always hide the notice.
 */
export function renderGoalBudgetNotice(_goal: GoalStateEvent): string {
  return "";
}