import type { WorkRecordsOverviewGroup } from "../../api/ws.ts";

/** User-facing item / group statuses for the 当前工作 queue (not run-history enums). */
export const OVERVIEW_ITEM_STATUS_LABELS: Record<string, string> = {
  blocked: "受阻",
  unknown: "结果待你确认",
  waiting_confirmation: "等待你确认",
  in_progress: "进行中",
  stopped: "已停止",
  queued: "排队中",
  pending: "待开始",
  completed: "已记录完成",
  no_requirements: "暂无要求",
};

export type OverviewItemHeadline = {
  statusLabel: string;
  hint: string | null;
};

function requirementById(item: WorkRecordsOverviewGroup["items"][number]) {
  return new Map(item.requirements.map((requirement) => [requirement.requirement_id, requirement]));
}

function isActionableUnreadResult(
  item: WorkRecordsOverviewGroup["items"][number],
  requirementId: string,
  readStatus: string | undefined,
): boolean {
  if (readStatus !== "unread") return false;
  const requirement = requirementById(item).get(requirementId);
  if (!requirement) return item.status === "unknown" || item.status === "waiting_confirmation";
  return requirement.status === "unknown"
    || requirement.status === "waiting_confirmation"
    || requirement.status === "blocked";
}

/** One status chip + optional short hint; avoids「状态 + 未读结果」jargon stacks. */
export function formatOverviewItemHeadline(item: WorkRecordsOverviewGroup["items"][number]): OverviewItemHeadline {
  const status = typeof item.status === "string" ? item.status : "";
  const statusLabel = OVERVIEW_ITEM_STATUS_LABELS[status] ?? "状态待确认";
  const actionableUnread = item.current_results.some((result) =>
    isActionableUnreadResult(item, result.requirement_id, result.read_status));
  if (actionableUnread) {
    return { statusLabel, hint: "有结果待看" };
  }
  const passiveUnread = item.current_results.some((result) => result.read_status === "unread");
  if (passiveUnread) {
    return { statusLabel, hint: "另有未打开记录" };
  }
  if (item.current_results.some((result) => result.read_status === "unknown")) {
    return { statusLabel, hint: "查看状态不明" };
  }
  return { statusLabel, hint: null };
}

export function formatOverviewPassiveUnreadNote(
  unreadCountText: string | null,
  attentionItemCount: number,
): string | null {
  if (unreadCountText === null) return null;
  const count = Number.parseInt(unreadCountText.replace(/\D/g, ""), 10);
  if (!Number.isFinite(count) || count <= 0) return null;
  if (attentionItemCount > 0) {
    return `另有 ${unreadCountText} 条结果尚未打开`;
  }
  if (unreadCountText.startsWith("至少")) {
    return `${unreadCountText} 条结果尚未打开（仅记录，不表示待处理）`;
  }
  return count <= 8
    ? `${unreadCountText} 条结果尚未打开（仅记录）`
    : `多条结果尚未打开（仅记录，详情在概况）`;
}
