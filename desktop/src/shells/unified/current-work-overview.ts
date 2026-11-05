import type { WorkRecordsOverviewGroup, WorkRecordsOverviewPageFacts, WorkRecordsOverviewResultEvent } from "../../api/ws";
import { WORK_CARD_DEFAULT_VISIBLE_REQUIREMENTS } from "./work-card-presentation.ts";

export const CURRENT_WORK_OVERVIEW_QUERY = "当前工作整体概况";
export type CurrentWorkOverviewRequestResult = "sent" | "busy" | "unavailable" | "failed";

export interface CurrentWorkOverviewPanel {
  visible: boolean;
  open: boolean;
  label: string;
  bodyHtml: string;
  canExpand: boolean;
  moreLabel: string;
  loadingMore: boolean;
}


/** Ten-minute query cadence while visible; local time never hides, and server visibility remains authoritative. */
export const CURRENT_WORK_VISIBILITY_RECHECK_MS = 10 * 60 * 1000;

const STATUS_LABELS: Record<string, string> = {
  blocked: "受阻", unknown: "结果待确认", in_progress: "执行状态待确认", stopped: "已停止",
  pending: "待开始", completed: "已记录完成", no_requirements: "暂无要求",
};
const escapeHtml = (value: unknown): string => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#39;");

function unreadCountSummary(result: WorkRecordsOverviewResultEvent): string | null {
  const total = result.unread_important_results_total;
  const truncated = result.unread_important_results_truncated;
  if (Number.isSafeInteger(total) && typeof truncated === "boolean") {
    return truncated && total === undefined ? `至少 ${Math.max(100, result.unread_important_results.length)}` : `${total}`;
  }
  if (truncated === true) return `至少 ${Math.max(100, result.unread_important_results.length)}`;
  // Legacy responses without both facts do not let the sample length prove a total.
  return null;
}

function groupNeedsAttention(group: WorkRecordsOverviewResultEvent["groups"][number]): boolean {
  if (group.visibility !== "visible" || group.requirement_count <= 0) return true;
  const counts = Object.entries(group.status_counts);
  if (!counts.length || counts.some(([status, count]) => !Number.isSafeInteger(count) || count < 0 || (count > 0 && status !== "completed"))) return true;
  if ((group.status_counts.completed ?? 0) !== group.requirement_count) return true;
  const requirements = group.items.flatMap(item => item.requirements);
  if (requirements.length !== group.requirement_count || requirements.some(requirement => requirement.status !== "completed")) return true;
  const results = group.items.flatMap(item => item.current_results);
  const resultsByRequirement = new Map<string, typeof results>();
  for (const result of results) resultsByRequirement.set(result.requirement_id, [...(resultsByRequirement.get(result.requirement_id) ?? []), result]);
  return requirements.some(requirement => {
    const matching = resultsByRequirement.get(requirement.requirement_id) ?? [];
    return matching.length !== 1 || matching[0].read_status !== "viewed";
  });
}

const OVERVIEW_REFRESH_STATES = new Set(["queued", "running", "stopping", "settled", "paused", "failed"]);

export function shouldRefreshOverviewForExecutionState(previous: string | undefined, next: string): boolean {
  return OVERVIEW_REFRESH_STATES.has(next) && previous !== next;
}
export function currentWorkOverviewRecheckDelayMs(result: WorkRecordsOverviewResultEvent | null): number {
  const suggested = result?.visibility_recheck_after_ms;
  if (typeof suggested === "number" && Number.isSafeInteger(suggested)
      && suggested >= 60_000 && suggested <= 30 * 60 * 1000) return suggested;
  return CURRENT_WORK_VISIBILITY_RECHECK_MS;
}
export function shouldScheduleOverviewVisibilityRecheck(result: WorkRecordsOverviewResultEvent | null): boolean {
  return Boolean(result && result.state === "visible" && result.visibility_counts.visible_groups > 0);
}
/** Keep read, visible groups until the server's 30-minute projection marks them collapsed. */
export function shouldShowCurrentWorkOverview(result: WorkRecordsOverviewResultEvent | null): boolean {
  if (!result) return false;
  const visibleCount = result.visibility_counts.visible_groups;
  if (result.state === "no_records" && visibleCount === 0 && result.unread_status === "known"
    && result.unread_important_results.length === 0) return false;
  if (result.unread_status !== "known" || unreadCountSummary(result) === null
    || result.unread_important_results.length > 0 || result.visibility_limitations.length > 0) return true;
  if (result.state === "error" || result.state === "cancelled" || result.state === "expired") return true;
  if (visibleCount === 0) return result.state !== "collapsed" && result.state !== "visible" && result.state !== "no_records";
  return true;
}
export function shouldRestartOverviewAfterExpiredCursor(state: string, cursor?: string): boolean {
  return Boolean(cursor) && state === "expired";
}

/** Retry an unsupported optional pagination capability once through the legacy full-response contract. */
export function shouldFallbackOverviewDetailPaging(requested: boolean, state: string, reason: unknown): boolean {
  return requested && state === "error"
    && ["invalid_request", "invalid_detail_paging", "detail_paging_required"].includes(String(reason));
}

function validDetailPageFacts(value: unknown): value is WorkRecordsOverviewPageFacts {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const page = value as Record<string, unknown>;
  return (page.kind === "items" || page.kind === "requirements")
    && (page.group_kind === "project" || page.group_kind === "independent" || page.group_kind === "linked_source_snapshot")
    && typeof page.group_id === "string" && Boolean(page.group_id)
    && (page.item_id === null || typeof page.item_id === "string" && Boolean(page.item_id))
    && (page.kind === "items" ? page.item_id === null : typeof page.item_id === "string" && Boolean(page.item_id))
    && Number.isSafeInteger(page.offset) && Number(page.offset) >= 0
    && Number.isSafeInteger(page.total) && Number(page.total) >= 0
    && typeof page.has_more === "boolean"
    && (page.next_cursor === null || typeof page.next_cursor === "string" && Boolean(page.next_cursor))
    && (page.has_more ? typeof page.next_cursor === "string" : page.next_cursor === null)
    && Number(page.offset) <= Number(page.total);
}

/** Append only a server-confirmed page from the same snapshot and exact server offset. */
export function appendCurrentWorkOverviewPage(
  current: WorkRecordsOverviewResultEvent,
  next: WorkRecordsOverviewResultEvent,
): WorkRecordsOverviewResultEvent | null {
  if (next.state !== "visible" || next.session_id !== current.session_id || next.snapshot_id !== current.snapshot_id
    || next.page.total_visible_groups !== current.page.total_visible_groups
    || next.page.offset !== current.groups.filter(group => group.visibility === "visible").length
    || next.groups.some(group => group.visibility !== "visible"
      || current.groups.some(existing => existing.kind === group.kind && existing.id === group.id))) return null;
  return { ...next, groups: [...current.groups.filter(group => group.visibility === "visible"), ...next.groups] };
}
function sameDetailPage(actual: unknown, expected: WorkRecordsOverviewPageFacts): boolean {
  if (!actual || typeof actual !== "object" || Array.isArray(actual)) return false;
  const value = actual as Record<string, unknown>;
  const keys = ["kind", "group_kind", "group_id", "item_id", "offset", "total", "has_more", "next_cursor"];
  return Object.keys(value).length === keys.length && keys.every(key => value[key] === (expected as unknown as Record<string, unknown>)[key]);
}

/** Merge a backend detail cursor page only into its exact visible group/item snapshot. */
export function mergeCurrentWorkOverviewDetailPage(
  current: WorkRecordsOverviewResultEvent,
  response: WorkRecordsOverviewResultEvent,
): WorkRecordsOverviewResultEvent | null {
  const page = response.detail_page;
  if (!page || response.state !== "visible" || response.session_id !== current.session_id
    || response.snapshot_id !== current.snapshot_id || response.groups.length !== 1
    || !validDetailPageFacts(page)) return null;
  const targetGroups = current.groups.filter(group => group.visibility === "visible"
    && group.kind === page.group_kind && group.id === page.group_id);
  const incoming = response.groups[0];
  if (targetGroups.length !== 1 || incoming.visibility !== "visible"
    || incoming.kind !== page.group_kind || incoming.id !== page.group_id) return null;
  const target = targetGroups[0];
  const targetIndex = current.groups.indexOf(target);
  const groups = current.groups.slice();
  const group = { ...target };
  if (page.kind === "items") {
    if (page.item_id !== null || !sameDetailPage(incoming.items_page, page)
      || incoming.items_page?.offset !== target.items.length
      || incoming.items.length === 0 || incoming.items.length > page.total - page.offset
      || incoming.item_count !== target.item_count || incoming.requirement_count !== target.requirement_count) return null;
    const existingIds = new Set(target.items.map(item => item.item_id));
    if (incoming.items.some(item => !item.item_id || existingIds.has(item.item_id))) return null;
    group.items = [...target.items, ...incoming.items];
    group.items_page = page;
  } else {
    if (typeof page.item_id !== "string" || incoming.items_page !== null || incoming.items.length !== 1
      || incoming.items[0].item_id !== page.item_id || !sameDetailPage(incoming.items[0].requirements_page, page)) return null;
    const matches = target.items.filter(item => item.item_id === page.item_id);
    if (matches.length !== 1) return null;
    const targetItem = matches[0];
    const detailItem = incoming.items[0];
    if (page.offset !== targetItem.requirements.length || detailItem.requirements.length === 0
      || detailItem.requirements.length > page.total - page.offset) return null;
    const existingRequirements = new Set(targetItem.requirements.map(requirement => requirement.requirement_id));
    const existingResults = new Set(targetItem.current_results.map(result => `${result.requirement_id}\0${result.result_id}`));
    if (detailItem.requirements.some(requirement => !requirement.requirement_id || existingRequirements.has(requirement.requirement_id))
      || detailItem.current_results.some(result => existingResults.has(`${result.requirement_id}\0${result.result_id}`))) return null;
    group.items = target.items.map(item => item === targetItem ? {
      ...item,
      requirements: [...item.requirements, ...detailItem.requirements],
      current_results: [...item.current_results, ...detailItem.current_results],
      requirements_page: page,
    } : item);
  }
  groups[targetIndex] = group;
  return { ...current, groups };
}

function groupAttentionCount(result: WorkRecordsOverviewResultEvent): number {
  return result.groups.filter(groupNeedsAttention).length;
}

const GROUP_STATUS_ORDER = ["blocked", "unknown", "in_progress", "stopped", "pending", "completed"];
const GROUP_STATUS_LABELS = STATUS_LABELS;

function renderGroupSummary(group: WorkRecordsOverviewResultEvent["groups"][number]): string {
  const statusCounts = Object.entries(group.status_counts)
    .filter(([, count]) => Number.isSafeInteger(count) && count > 0)
    .sort(([left], [right]) => {
      const leftRank = GROUP_STATUS_ORDER.indexOf(left);
      const rightRank = GROUP_STATUS_ORDER.indexOf(right);
      return (leftRank < 0 ? GROUP_STATUS_ORDER.length : leftRank)
        - (rightRank < 0 ? GROUP_STATUS_ORDER.length : rightRank) || left.localeCompare(right);
    });
  const countSummary = statusCounts.length === 1 && statusCounts[0][1] === group.requirement_count
    ? `${group.requirement_count} 项要求`
    : statusCounts.length ? statusCounts.map(([status, count]) => `${GROUP_STATUS_LABELS[status] ?? "状态待确认"} ${count}`).join(" · ")
      : `${group.requirement_count} 项要求`;
  const aggregateStatus = GROUP_STATUS_LABELS[group.status] ?? "状态待确认";
  return `<div class="unified-current-work-group-summary"><span class="unified-current-work-group-status">${aggregateStatus}</span><span class="unified-current-work-group-counts">${countSummary}</span></div>`;
}
const DETAIL_BATCH_SIZE = WORK_CARD_DEFAULT_VISIBLE_REQUIREMENTS;

function itemAttentionRank(item: WorkRecordsOverviewGroup["items"][number]): number {
  const order = ["blocked", "unknown", "in_progress", "stopped", "pending", "completed"];
  const rank = order.indexOf(item.status);
  return rank < 0 ? 1 : rank;
}

function itemNeedsAttention(item: WorkRecordsOverviewGroup["items"][number]): boolean {
  return itemAttentionRank(item) < 4 || item.current_results.some(result => result.read_status !== "viewed");
}

function detailLoadButton(page: WorkRecordsOverviewPageFacts | null | undefined, loaded: number,
  label: string, loadingCursor?: string | null): string {
  if (!page?.has_more || !page.next_cursor) return "";
  const remaining = Math.max(0, page.total - loaded);
  const disabled = loadingCursor === page.next_cursor;
  return `<button type="button" class="unified-btn unified-btn-ghost unified-current-work-detail-more" data-current-work-detail-kind="${page.kind}" data-current-work-detail-group-kind="${escapeHtml(page.group_kind)}" data-current-work-detail-group-id="${escapeHtml(page.group_id)}"${page.item_id ? ` data-current-work-detail-item-id="${escapeHtml(page.item_id)}"` : ""} data-current-work-detail-cursor="${escapeHtml(page.next_cursor)}"${disabled ? " disabled" : ""}>${disabled ? "正在加载…" : `还有 ${remaining} ${label} · 展开更多`}</button>`;
}

function renderGroup(group: WorkRecordsOverviewGroup, expandedRequirementItemIds: ReadonlySet<string>,
  loadingDetailCursor?: string | null): string {
  const sortedItems = group.items.map((item, index) => ({ item, index }))
    .sort((left, right) => Number(itemNeedsAttention(right.item)) - Number(itemNeedsAttention(left.item))
      || itemAttentionRank(left.item) - itemAttentionRank(right.item) || left.index - right.index)
    .map(row => row.item);
  const serverPaged = Boolean(group.items_page);
  const attentionItems = sortedItems.filter(itemNeedsAttention);
  const ordinaryItems = sortedItems.filter(item => !itemNeedsAttention(item));
  // Preserve every already-returned attention item. The five-item local fallback
  // bounds ordinary rows when talking to an older full-response backend.
  const initialItems = serverPaged ? group.items : [
    ...attentionItems,
    ...ordinaryItems.slice(0, Math.max(0, DETAIL_BATCH_SIZE - attentionItems.length)),
  ];
  const shownIds = new Set(initialItems.map(item => item.item_id));
  const renderItem = (item: WorkRecordsOverviewGroup["items"][number]) => {
    const status = group.kind === "linked_source_snapshot" ? `捕获时：${STATUS_LABELS[item.status_at_capture ?? ""] ?? "状态待确认"}` : STATUS_LABELS[item.status] ?? "状态待确认";
    const allRequirementRows = item.requirements.map(requirement => {
      const reqStatus = group.kind === "linked_source_snapshot" ? `捕获时：${STATUS_LABELS[requirement.status_at_capture ?? ""] ?? "状态待确认"}` : STATUS_LABELS[requirement.status] ?? "状态待确认";
      return `<li>${escapeHtml(requirement.text || "（要求文本为空）")} · ${reqStatus}</li>`;
    });
    const requirementPage = item.requirements_page;
    const localRequirementRemainder = !requirementPage && allRequirementRows.length > DETAIL_BATCH_SIZE
      ? `<details class="unified-current-work-requirements-more"><summary>还有 ${allRequirementRows.length - DETAIL_BATCH_SIZE} 项要求，展开</summary><ul>${allRequirementRows.slice(DETAIL_BATCH_SIZE).join("")}</ul></details>` : "";
    const requirementRows = !requirementPage && allRequirementRows.length > DETAIL_BATCH_SIZE
      ? allRequirementRows.slice(0, DETAIL_BATCH_SIZE).join("") : allRequirementRows.join("");
    const requirementCount = requirementPage ? `${item.requirements.length}/${requirementPage.total}` : `${item.requirements.length}`;
    const requirementDetails = item.requirements.length
      ? `<details class="unified-current-work-requirements" data-current-work-item-id="${escapeHtml(JSON.stringify([group.kind, group.id, item.item_id]))}"${expandedRequirementItemIds.has(JSON.stringify([group.kind, group.id, item.item_id])) ? " open" : ""}><summary>要求（${requirementCount}）</summary><ul>${requirementRows}</ul>${localRequirementRemainder}${detailLoadButton(requirementPage, item.requirements.length, "项要求", loadingDetailCursor)}</details>`
      : `<span class="unified-current-work-requirement-count">要求（0）</span>`;
    const resultMarkers = item.current_results.map(result => result.read_status === "unread"
      ? `<span class="unified-current-work-unread">未读结果</span>`
      : result.read_status === "unknown" ? `<span class="unified-current-work-unknown">查看状态未知</span>` : "").join("");
    return `<section class="unified-current-work-item"><div><strong>${escapeHtml(item.title || "未命名事项")}</strong><span>${status}</span>${resultMarkers}</div>${requirementDetails}</section>`;
  };
  const itemRows = initialItems.map(renderItem).join("");
  const itemMore = serverPaged
    ? detailLoadButton(group.items_page, group.items.length, "项", loadingDetailCursor)
    : (() => {
      const rest = sortedItems.filter(item => !shownIds.has(item.item_id));
      return rest.length ? `<details class="unified-current-work-items-more"><summary>还有 ${rest.length} 项（已优先显示需关注事项），展开查看</summary><div>${rest.map(renderItem).join("")}</div></details>` : "";
    })();
  const title = group.kind === "linked_source_snapshot" ? "来源会话快照" : group.kind === "project" ? `项目 · ${group.id}` : group.items.length === 1 && group.items[0].title.trim() ? group.items[0].title.trim() : "独立事项组";
  const source = group.kind === "linked_source_snapshot" ? group.source_snapshot : undefined;
  const timerLabels: Record<string, string> = { waiting: "查看计时尚未完成", not_viewed: "明确查看尚未确认", unavailable: "查看计时证据不可用", unknown: "查看计时状态未知", elapsed: "查看计时已到期" };
  const timer = group.kind === "linked_source_snapshot" ? timerLabels[group.visibility_timer_status] ?? "查看计时状态未知" : "";
  const provenance = source ? `<p>项目 ${escapeHtml(source.project_id)} · 来源会话 ${escapeHtml(source.source_session_id)} · 捕获时间 ${escapeHtml(source.captured_at)}</p><p>仅为来源会话捕获时状态，不代表当前映射；结果未知、未验证。${source.viewed_at === null ? "卡片查看状态未确认。" : `卡片查看时间 ${escapeHtml(source.viewed_at)}；不代表各项结果已查看。`}${timer}，不代表当前工作完成。${source.superseded_unviewed_count > 0 ? `旧快照 ${source.superseded_unviewed_count} 个查看计时尚未确认。` : ""}</p>` : "";
  return `<article class="unified-current-work-group"><h4>${escapeHtml(title)}</h4>${provenance}${renderGroupSummary(group)}<div class="unified-current-work-items">${itemRows || `<p>组内事项暂不可展开</p>`}</div>${itemMore}</article>`;
}

export function buildCurrentWorkOverviewPanel(
  result: WorkRecordsOverviewResultEvent | null,
  options: { open: boolean; loadingMore?: boolean; unavailable?: boolean; expandedRequirementItemIds?: ReadonlySet<string>; detailLoadingCursor?: string | null },
): CurrentWorkOverviewPanel {
  if (!result) return options.unavailable
    ? { visible: true, open: options.open, label: "当前工作 · 正在核实", bodyHtml: `<p class="unified-current-work-warning" role="status">当前会话概况正在核实，结果返回前不隐藏入口。</p>`, canExpand: false, moreLabel: "", loadingMore: false }
    : { visible: false, open: options.open, label: "", bodyHtml: "", canExpand: false, moreLabel: "", loadingMore: false };
  const visible = shouldShowCurrentWorkOverview(result);
  const unreadCountText = unreadCountSummary(result);
  const unreadCount = unreadCountText === null ? null : Number.parseInt(unreadCountText.replace(/\D/g, ""), 10);
  const attentionCount = groupAttentionCount(result);
  const waitingForServerCollapse = result.state === "visible" && result.unread_status === "known"
    && unreadCount === 0 && unreadCountText !== null && result.visibility_limitations.length === 0 && !result.page.has_more
    && result.groups.length === result.visibility_counts.visible_groups && result.groups.length > 0
    && !result.groups.some(groupNeedsAttention);
  const label = result.unread_status !== "known"
    ? "当前工作 · 状态待确认"
    : unreadCountText === null ? "当前工作 · 未读结果数未知"
      : unreadCountText.startsWith("至少") ? `当前工作 · 未读 ${unreadCountText}`
        : unreadCount! > 0 ? `当前工作 · 未读 ${unreadCountText}`
          : attentionCount > 0 ? `当前工作 · ${attentionCount} 组需关注`
        : result.page.has_more ? `当前工作 · 还有余项`
          : waitingForServerCollapse ? "当前工作 · 已查看，待收起" : "当前工作";  let statusHtml = "";
  if (result.unread_status !== "known") statusHtml = `<p class="unified-current-work-warning" role="status">部分结果的查看状态未知；暂不据此隐藏当前工作。</p>`;
  if (result.state === "error" || result.state === "cancelled") statusHtml += `<p class="unified-current-work-warning" role="status">概况暂不可确认，可刷新重试。</p>`;
  if (result.state === "expired") statusHtml += `<p class="unified-current-work-warning" role="status">概况数据已变化，正在重新查询。</p>`;
  if (result.visibility_limitations.length) statusHtml += `<p class="unified-current-work-warning">部分事项按保守范围展示。</p>`;
  const activeGroups = result.groups.filter(group => group.visibility === "visible");
  const expandedRequirementItemIds = options.expandedRequirementItemIds ?? new Set<string>();
  const groupsHtml = activeGroups.map(group => renderGroup(group, expandedRequirementItemIds, options.detailLoadingCursor)).join("");
  const remaining = Math.max(0, result.page.total_visible_groups - activeGroups.length);
  const canExpand = result.state === "visible" && result.page.has_more && Boolean(result.page.next_cursor);
  const unreadSummary = result.unread_status === "known" && unreadCountText !== null
    ? `${unreadCountText} 项未读结果` : "未读结果数未知";
  const bodyHtml = `${statusHtml}<p class="unified-current-work-summary">${result.visibility_counts.visible_groups} 组当前可见 · ${unreadSummary}</p>${groupsHtml}`;
  return {
    visible, open: options.open, label, bodyHtml, canExpand,
    moreLabel: options.loadingMore ? "正在展开…" : `展开更多${remaining ? `（约 ${remaining} 组）` : ""}`,
    loadingMore: options.loadingMore ?? false,
  };
}

/** Dispatch a fresh natural-language query; kept alongside the structured overview UI. */
export function dispatchCurrentWorkOverviewRequest(options: {
  busy: boolean;
  allowed: boolean;
  send: (query: string) => void;
}): CurrentWorkOverviewRequestResult {
  if (options.busy) return "busy";
  if (!options.allowed) return "unavailable";
  try {
    options.send(CURRENT_WORK_OVERVIEW_QUERY);
    return "sent";
  } catch {
    return "failed";
  }
}