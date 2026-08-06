import type { PlanSuggestion } from "../../api/ws";
import { escapeHtml } from "../chat-state";

export type MainFocus = "chat" | "plan_review" | "plan_full";

export interface PlanReviewUiState {
  mainFocus: MainFocus;
  reviewIndex: number;
  reviewFocusId: string | null;
}

export function actionableSuggestions(suggestions: PlanSuggestion[]): PlanSuggestion[] {
  return suggestions.filter((s) => Boolean(s.action));
}

export function acceptLabel(s: PlanSuggestion): string {
  if (s.action === "drop_task") return "删除";
  if (
    s.risk === "gate" ||
    s.action === "add_task" ||
    s.action === "move_task" ||
    s.action === "apply_patch"
  ) {
    return "采纳写入";
  }
  return "采纳";
}

/** Plan patch path for adopt flash (BUG-026 / T-4811). */
export function adoptPathFromSuggestion(s: PlanSuggestion): string {
  const path =
    s.payload && typeof s.payload.path === "string" ? s.payload.path.trim() : "";
  if (path) return path;
  const m = s.title.match(/改\s+(\S+)/);
  return m?.[1] ?? "计划文件";
}

export function truncateSummary(text: string, max = 80): string {
  const t = text.trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1)}…`;
}

export function diffStats(diff: string): string {
  const adds = (diff.match(/^\+/gm) || []).length;
  const dels = (diff.match(/^-/gm) || []).length;
  if (!adds && !dels) return "";
  return `+${adds} −${dels}`;
}

export function clampReviewIndex(index: number, total: number): number {
  if (total <= 0) return 0;
  return Math.max(0, Math.min(index, total - 1));
}

export function renderPlanReviewPanel(opts: {
  suggestions: PlanSuggestion[];
  reviewIndex: number;
  adoptPendingId?: string | null;
}): string {
  const queue = actionableSuggestions(opts.suggestions);
  const index = clampReviewIndex(opts.reviewIndex, queue.length);
  const item = queue[index] ?? null;

  if (!item) {
    return `<div class="unified-plan-review-empty">
      <p class="overlay-empty">暂无待采纳提案</p>
      <button type="button" class="unified-btn" data-plan-review-action="back">返回聊天</button>
    </div>`;
  }

  const diffRaw =
    item.payload && typeof item.payload.diff === "string" ? item.payload.diff.trim() : "";
  const pathRaw =
    item.payload && typeof item.payload.path === "string" ? item.payload.path.trim() : "";
  const stats = diffRaw ? diffStats(diffRaw) : "";
  const pathLine = pathRaw ? `<div class="unified-plan-review-path">${escapeHtml(pathRaw)}</div>` : "";
  const diffBlock = diffRaw
    ? `<pre class="unified-plan-review-diff">${escapeHtml(diffRaw)}</pre>`
    : `<pre class="unified-plan-review-diff unified-plan-review-diff-empty">${escapeHtml(item.body)}</pre>`;

  const position = queue.length > 1 ? `计划审阅 · ${index + 1}/${queue.length}` : "计划审阅";
  const statsBadge = stats
    ? `<span class="unified-plan-review-stats">${escapeHtml(stats)}</span>`
    : "";
  const isPending = opts.adoptPendingId === item.id;
  const acceptBtnLabel = isPending ? "采纳中…" : acceptLabel(item);

  return `<div class="unified-plan-review-inner">
    <header class="unified-plan-review-header">
      <button type="button" class="unified-btn unified-plan-review-back" data-plan-review-action="back">← 返回聊天</button>
      <span class="unified-plan-review-position">${escapeHtml(position)}</span>
      <button type="button" class="unified-btn" data-plan-review-action="open-full">查看完整计划</button>
    </header>
    <div class="unified-plan-review-meta">
      <h2 class="unified-plan-review-title">${escapeHtml(item.title)}</h2>
      ${statsBadge}
      <p class="unified-plan-review-summary">${escapeHtml(item.body)}</p>
      ${pathLine}
    </div>
    <div class="unified-plan-review-body">${diffBlock}</div>
    <footer class="unified-plan-review-actions">
      <button type="button" class="unified-btn unified-btn-accent" data-plan-review-action="accept" data-suggestion-id="${escapeHtml(item.id)}"${isPending ? " disabled" : ""}>${escapeHtml(acceptBtnLabel)}</button>
      <button type="button" class="unified-btn" data-plan-review-action="ignore" data-suggestion-id="${escapeHtml(item.id)}">忽略</button>
      <button type="button" class="unified-btn" data-plan-review-action="prev"${index <= 0 ? " disabled" : ""}>上一条</button>
      <button type="button" class="unified-btn" data-plan-review-action="next"${index >= queue.length - 1 ? " disabled" : ""}>下一条</button>
    </footer>
  </div>`;
}

export function renderPlanFullHeader(): string {
  return `<header class="unified-plan-review-header">
    <button type="button" class="unified-btn unified-plan-review-back" data-plan-review-action="back">← 返回聊天</button>
    <span class="unified-plan-review-position">完整计划</span>
    <span></span>
  </header>`;
}
