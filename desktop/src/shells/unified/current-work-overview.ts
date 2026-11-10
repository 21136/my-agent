import type { WorkRecordsOverviewGroup, WorkRecordsOverviewPageFacts, WorkRecordsOverviewResultEvent } from "../../api/ws";
import {
  type CurrentWorkLiveSessionInput,
  currentWorkLiveConfirmPending,
  currentWorkLiveQueueCount,
  renderCurrentWorkLiveQueueHtml,
} from "./current-work-live-session.ts";
import {
  formatOverviewHistorySummary,
  formatOverviewRunStatus,
  formatOverviewVerificationStatus,
} from "./current-work-activity-copy.ts";
import {
  formatOverviewItemHeadline,
  formatOverviewPassiveUnreadNote,
  OVERVIEW_ITEM_STATUS_LABELS,
} from "./current-work-queue-copy.ts";
import { WORK_CARD_DEFAULT_VISIBLE_REQUIREMENTS } from "./work-card-presentation.ts";

export const CURRENT_WORK_OVERVIEW_QUERY = "当前工作整体概况";
export type CurrentWorkOverviewRequestResult = "sent" | "busy" | "unavailable" | "failed";

/** Shown in the 调度 tab before selected-project overview + schedule_preview are authorized. */
export const CURRENT_WORK_SCHEDULE_EMPTY_MARKUP = `<div class="current-work-zone-empty current-work-schedule-empty" role="status">
<p>调度是多项目工作台：先看只读预览，再按你的授权<strong>并行</strong>启动任务；不会把事项丢回排队或塞满上下文。</p>
<ol class="cw-schedule-empty-steps">
<li>在下方「选择项目来源」勾选最多 6 个已映射项目</li>
<li>点「查看所选项目」，并在底部确认条逐项授权只读聚合</li>
<li>授权后此处显示候选 / 受阻说明与「按预览启动」；<span class="cw-schedule-nowrap">不会自动开跑</span></li>
</ol>
<p class="cw-schedule-empty-note">可跑候选出现后会预填任务与路径，经你确认后再启动。</p>
</div>`;

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

const STATUS_LABELS: Record<string, string> = OVERVIEW_ITEM_STATUS_LABELS;
const HISTORY_COLLAPSED_PREVIEW_ROWS = 2;
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
  const onlyStatus = statusCounts.length === 1 && statusCounts[0][0] === group.status;
  const statusHtml = onlyStatus
    ? `<span class="unified-current-work-group-status">${escapeHtml(aggregateStatus)}</span><span class="unified-current-work-group-counts">${escapeHtml(String(statusCounts[0][1]))} 项要求</span>`
    : `<span class="unified-current-work-group-status">${escapeHtml(aggregateStatus)}</span><span class="unified-current-work-group-counts">${escapeHtml(countSummary)}</span>`;
  return `<div class="unified-current-work-group-summary">${statusHtml}</div>`;
}
const DETAIL_BATCH_SIZE = WORK_CARD_DEFAULT_VISIBLE_REQUIREMENTS;

export function collectWorkRecordDependencyRefs(
  itemElement: Pick<HTMLElement, "querySelectorAll"> | null,
  requirementId: string,
): Array<{ item_id: string; requirement_id: string }> {
  const editor = Array.from(itemElement?.querySelectorAll<HTMLElement>("fieldset[data-work-dependency-editor-for]") ?? [])
    .find(candidate => candidate.dataset.workDependencyEditorFor === requirementId);
  return Array.from(editor?.querySelectorAll<HTMLInputElement>("input[data-work-dependency-ref-item]:checked") ?? [])
    .map(input => ({ item_id: input.dataset.workDependencyRefItem ?? "", requirement_id: input.dataset.workDependencyRefRequirement ?? "" }));
}

function itemAttentionRank(item: WorkRecordsOverviewGroup["items"][number]): number {
  const order = ["blocked", "unknown", "in_progress", "stopped", "pending", "completed"];
  const rank = order.indexOf(item.status);
  return rank < 0 ? 1 : rank;
}

function itemNeedsAttention(item: WorkRecordsOverviewGroup["items"][number]): boolean {
  return itemAttentionRank(item) < 4 || item.current_results.some(result => result.read_status !== "viewed");
}

/** Authoritative actionable item count from server projection (stable across group paging). */
export function overviewAttentionItemTotal(result: WorkRecordsOverviewResultEvent): number | null {
  const total = result.visibility_counts.attention_visible_items;
  return Number.isSafeInteger(total) && total >= 0 ? total : null;
}

export function overviewAttentionGroupTotal(result: WorkRecordsOverviewResultEvent): number | null {
  const total = result.visibility_counts.attention_visible_groups;
  return Number.isSafeInteger(total) && total >= 0 ? total : null;
}

function countVisibleAttentionItems(result: WorkRecordsOverviewResultEvent): number {
  let count = 0;
  for (const group of result.groups) {
    if (group.visibility !== "visible") continue;
    for (const item of group.items) {
      if (itemNeedsAttention(item)) count += 1;
    }
  }
  return count;
}

function detailLoadButton(page: WorkRecordsOverviewPageFacts | null | undefined, loaded: number,
  label: string, loadingCursor?: string | null): string {
  if (!page?.has_more || !page.next_cursor) return "";
  const remaining = Math.max(0, page.total - loaded);
  const disabled = loadingCursor === page.next_cursor;
  return `<button type="button" class="unified-btn unified-btn-ghost unified-current-work-detail-more" data-current-work-detail-kind="${page.kind}" data-current-work-detail-group-kind="${escapeHtml(page.group_kind)}" data-current-work-detail-group-id="${escapeHtml(page.group_id)}"${page.item_id ? ` data-current-work-detail-item-id="${escapeHtml(page.item_id)}"` : ""} data-current-work-detail-cursor="${escapeHtml(page.next_cursor)}"${disabled ? " disabled" : ""}>${disabled ? "正在加载…" : `还有 ${remaining} ${label} · 展开更多`}</button>`;
}

function renderIndependentReorganizeBar(mergeMode: boolean): string {
  return `<div class="unified-current-work-reorganize" data-cw-reorganize-bar role="region" aria-label="事项整理">
    <p class="cw-reorganize-hint">拆分：展开事项后对单条要求点「拆出为新事项」。合并：勾选两个事项，将来源的可合并要求并入首选。</p>
    <div class="cw-reorganize-actions">
      ${mergeMode
        ? `<button type="button" class="unified-btn unified-btn-ghost" data-cw-merge-apply>合并到首选</button>
           <button type="button" class="unified-btn unified-btn-ghost" data-cw-merge-cancel>取消</button>`
        : `<button type="button" class="unified-btn unified-btn-ghost" data-cw-merge-toggle>合并事项…</button>`}
    </div>
  </div>`;
}

function renderGroup(
  group: WorkRecordsOverviewGroup,
  expandedRequirementItemIds: ReadonlySet<string>,
  dependencyCandidateItems: WorkRecordsOverviewGroup["items"],
  loadingDetailCursor?: string | null,
  itemReorganize?: { mergeMode: boolean; pickedItemIds: ReadonlySet<string> },
): string {
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
    const itemKey = JSON.stringify([group.kind, group.id, item.item_id]);
    const selected = expandedRequirementItemIds.has(itemKey);
    const { statusLabel, hint } = formatOverviewItemHeadline(item);
    const captureStatus = group.kind === "linked_source_snapshot"
      ? `捕获时：${STATUS_LABELS[item.status_at_capture ?? ""] ?? "状态待确认"}`
      : statusLabel;
    const requirementsById = new Map(item.requirements.map(requirement => [requirement.requirement_id, requirement]));
    const allRequirementRows = item.requirements.map(requirement => {
      const reqStatus = group.kind === "linked_source_snapshot" ? `捕获时：${STATUS_LABELS[requirement.status_at_capture ?? ""] ?? "状态待确认"}` : STATUS_LABELS[requirement.status] ?? "状态待确认";
      const dependencies = Array.isArray(requirement.depends_on) ? requirement.depends_on : [];
      const dependencyRefs = Array.isArray(requirement.depends_on_refs) ? requirement.depends_on_refs : [];
      const dependencyRows = dependencies.map(dependencyId => {
        const dependency = requirementsById.get(dependencyId);
        const dependencyStatus = dependency
          ? group.kind === "linked_source_snapshot"
            ? STATUS_LABELS[dependency.status_at_capture ?? ""] ?? "状态待确认"
            : STATUS_LABELS[dependency.status] ?? "状态待确认"
          : "依赖未包含在当前授权事项中，状态无法核实";
        const isBlocked = !dependency || (group.kind === "linked_source_snapshot"
          ? dependency.status_at_capture !== "completed" : dependency.status !== "completed");
        return `<li>${escapeHtml(dependency?.text || dependencyId)} · ${escapeHtml(dependencyStatus)}${isBlocked ? "（尚未完成，当前要求受阻）" : "（已完成）"}</li>`;
      }).join("");
      const referenceRows = dependencyRefs.map(ref => {
        const targetItem = group.items.find(candidate => candidate.item_id === ref.item_id);
        const target = targetItem?.requirements.find(candidate => candidate.requirement_id === ref.requirement_id);
        const state = target ? (group.kind === "linked_source_snapshot"
          ? STATUS_LABELS[target.status_at_capture ?? ""] ?? "状态待确认"
          : STATUS_LABELS[target.status] ?? "状态待确认") : "目标未包含在当前来源概况中，状态无法核实";
        const sourceLabel = targetItem ? `${targetItem.title || targetItem.item_id} · ${target?.text || ref.requirement_id}` : `${ref.item_id} · ${ref.requirement_id}`;
        return `<li>跨事项依赖：${escapeHtml(sourceLabel)} · ${escapeHtml(state)}</li>`;
      }).join("");
      const edgeCount = dependencies.length + dependencyRefs.length;
      const dependencySummary = edgeCount
        ? `<div class="unified-current-work-dependency-edges" aria-label="依赖关系（${edgeCount}）"><span class="cw-dep-label">依赖关系（${edgeCount}）</span><ul>${dependencyRows}${referenceRows}</ul></div>` : "";
      return `<li>${escapeHtml(requirement.text || "（要求文本为空）")} · ${reqStatus}${dependencySummary}</li>`;
    });
    const requirementPage = item.requirements_page;
    const history = selected ? item.requirements.map(requirement => {
      const rows = requirement.history ?? [];
      const page = requirement.history_page;
      if (!rows.length && !page?.has_more) return "";
      const rowHtml = rows.map(row => {
        const historyStatus = formatOverviewRunStatus(row.status);
        const summaryText = formatOverviewHistorySummary(row.summary, row.status);
        const verificationLabel = formatOverviewVerificationStatus(row.verification_status);
        return `<li>${escapeHtml(historyStatus)} · ${escapeHtml(summaryText)} · ${escapeHtml(verificationLabel)}</li>`;
      }).join("");
      const totalLabel = `${rows.length}/${page ? page.offset + rows.length : rows.length}${page?.has_more ? "+" : ""}`;
      const projectId = group.kind === "project" ? group.id : "";
      const load = page?.has_more && page.next_cursor ? `<button type="button" data-requirement-history data-project-id="${escapeHtml(projectId)}" data-item-id="${escapeHtml(item.item_id)}" data-requirement-id="${escapeHtml(requirement.requirement_id)}" data-cursor="${escapeHtml(page.next_cursor)}">加载更多历史</button>` : "";
      if (rows.length <= HISTORY_COLLAPSED_PREVIEW_ROWS && !page?.has_more) {
        return `<section class="unified-current-work-history" aria-label="最近历史"><h5>最近历史（${totalLabel}）</h5><ul>${rowHtml}</ul></section>`;
      }
      return `<details class="unified-current-work-history-fold" aria-label="历史尝试"><summary>查看历史尝试（${totalLabel}）</summary><ul>${rowHtml}</ul>${load}</details>`;
    }).join("") : "";
    const localRequirementRemainder = !requirementPage && allRequirementRows.length > DETAIL_BATCH_SIZE
      ? `<div class="unified-current-work-requirements-more"><p class="cw-more-caption">还有 ${allRequirementRows.length - DETAIL_BATCH_SIZE} 项要求</p><ul>${allRequirementRows.slice(DETAIL_BATCH_SIZE).join("")}</ul></div>` : "";
    const requirementRows = !requirementPage && allRequirementRows.length > DETAIL_BATCH_SIZE
      ? allRequirementRows.slice(0, DETAIL_BATCH_SIZE).join("") : allRequirementRows.join("");
    const requirementCount = requirementPage ? `${item.requirements.length}/${requirementPage.total}` : `${item.requirements.length}`;
    const requirementDetails = item.requirements.length
      ? `<section class="unified-current-work-requirements" data-current-work-item-id="${escapeHtml(itemKey)}"${selected ? ' data-selected="1"' : " hidden"}><h5>要求（${requirementCount}）</h5><ul>${requirementRows}</ul>${localRequirementRemainder}${detailLoadButton(requirementPage, item.requirements.length, "项要求", loadingDetailCursor)}</section>`
      : `<span class="unified-current-work-requirement-count">要求（0）</span>`;
    const hintHtml = hint ? `<span class="unified-current-work-item-hint">${escapeHtml(hint)}</span>` : "";
    const canReviseHere = group.kind === "independent";
    const canReviseRemote = group.kind === "project" && Number.isSafeInteger(group.source_revision) && group.source_revision! >= 0;
    const canCancelWorker = group.kind === "project";
    const remoteControlsInner = canReviseHere || canReviseRemote || canCancelWorker
      ? `${canReviseRemote ? `<button type="button" data-open-source-session="${escapeHtml(group.source_session_id ?? "")}" data-open-source-project="${escapeHtml(group.id)}">打开来源会话</button>` : ""}${item.requirements.map(req => `<div><textarea data-work-record-new-text-for="${escapeHtml(req.requirement_id)}" aria-label="修订要求">${escapeHtml(req.text)}</textarea><button type="button" data-work-record-control data-item-id="${escapeHtml(item.item_id)}" data-requirement-id="${escapeHtml(req.requirement_id)}" data-action="revise_requirement" data-expected-status="${escapeHtml(req.status)}"${canReviseRemote ? ` data-source-project-id="${escapeHtml(group.id)}" data-source-revision="${group.source_revision}"` : ""}>修订要求</button>${canCancelWorker && req.status === "in_progress" ? `<button type="button" data-work-record-control data-item-id="${escapeHtml(item.item_id)}" data-requirement-id="${escapeHtml(req.requirement_id)}" data-action="cancel_execution" data-expected-status="in_progress"${canReviseRemote ? ` data-source-project-id="${escapeHtml(group.id)}" data-source-revision="${group.source_revision}"` : ""}>请求停止 worker</button>` : ""}${canReviseHere ? `<button type="button" data-work-record-control data-item-id="${escapeHtml(item.item_id)}" data-requirement-id="${escapeHtml(req.requirement_id)}" data-action="copy_requirement_to_new_item">拆出为新事项</button>` : ""}${!canReviseRemote && group.kind === "independent" && ["pending", "queued", "waiting_confirmation", "stopped", "blocked"].includes(req.status) ? `<fieldset data-work-dependency-editor-for="${escapeHtml(req.requirement_id)}"><legend>依赖目标（同会话、同范围）</legend>${dependencyCandidateItems.flatMap(candidate => candidate.requirements.map(target => ({ candidate, target }))).filter(({ candidate, target }) => candidate.item_id !== item.item_id || target.requirement_id !== req.requirement_id).map(({ candidate, target }) => `<label><input type="checkbox" data-work-dependency-ref-item="${escapeHtml(candidate.item_id)}" data-work-dependency-ref-requirement="${escapeHtml(target.requirement_id)}"${(req.depends_on_refs ?? []).some(ref => ref.item_id === candidate.item_id && ref.requirement_id === target.requirement_id) ? " checked" : ""}>${escapeHtml(candidate.title || candidate.item_id)} · ${escapeHtml(target.text)} · ${escapeHtml(STATUS_LABELS[target.status] ?? "状态待确认")}</label>`).join("")}<button type="button" data-work-record-control data-item-id="${escapeHtml(item.item_id)}" data-requirement-id="${escapeHtml(req.requirement_id)}" data-action="set_dependencies" data-expected-status="${escapeHtml(req.status)}">保存依赖</button></fieldset>` : ""}${canReviseRemote ? ["queue", "stop", "continue", "wait_confirmation", "release_confirmation"].map(action => `<button type="button" data-work-record-control data-item-id="${escapeHtml(item.item_id)}" data-requirement-id="${escapeHtml(req.requirement_id)}" data-action="${action}" data-expected-status="${escapeHtml(req.status)}" data-source-project-id="${escapeHtml(group.id)}" data-source-revision="${group.source_revision}">${action}</button>`).join("") : ""}${canReviseRemote && req.status === "unknown" && req.claim_recovery_needed ? `<fieldset><legend>人工检查未知执行 claim</legend><p>须先核对该运行的进程/host 状态。没有权威停止证据时只能记录核查，claim 仍保留为未解决；不会释放或重派。</p><textarea data-claim-inspection-for="${escapeHtml(req.requirement_id)}" aria-label="人工核查证据" maxlength="4000" placeholder="记录核查对象、时间、观察到的证据；不确定请明确写明"></textarea><button type="button" data-work-record-control data-item-id="${escapeHtml(item.item_id)}" data-requirement-id="${escapeHtml(req.requirement_id)}" data-action="inspect_unknown_claim" data-expected-status="unknown" data-source-project-id="${escapeHtml(group.id)}" data-source-revision="${group.source_revision}">记录核查（保持未解决）</button></fieldset>` : ""}</div>`).join("")}` : "";
    const remoteControls = remoteControlsInner
      ? `<section class="unified-current-work-controls" data-cw-ops${selected ? ' data-selected="1"' : " hidden"}><h5>操作</h5>${remoteControlsInner}</section>`
      : "";
    const mergeMode = Boolean(itemReorganize?.mergeMode) && group.kind === "independent";
    const mergePick = mergeMode
      ? `<label class="cw-merge-pick"><input type="checkbox" data-cw-merge-pick data-item-id="${escapeHtml(item.item_id)}"${itemReorganize?.pickedItemIds.has(item.item_id) ? " checked" : ""} /><span>纳入合并</span></label>`
      : "";
    const selectedClass = selected ? " is-selected" : "";
    return `<section class="unified-current-work-item${selectedClass}" data-cw-item-key="${escapeHtml(itemKey)}" data-item-id="${escapeHtml(item.item_id)}"><div class="unified-current-work-item-head-row">${mergePick}<button type="button" class="unified-current-work-item-head" data-cw-select-item="${escapeHtml(itemKey)}" aria-pressed="${selected ? "true" : "false"}"><strong>${escapeHtml(item.title || "未命名事项")}</strong><span>${escapeHtml(captureStatus)}</span>${hintHtml}</button></div>${requirementDetails}${history}${remoteControls}</section>`;
  };
  const itemRows = initialItems.map(renderItem).join("");
  const itemMore = serverPaged
    ? detailLoadButton(group.items_page, group.items.length, "项", loadingDetailCursor)
    : (() => {
      const rest = sortedItems.filter(item => !shownIds.has(item.item_id));
      return rest.length ? `<div class="unified-current-work-items-more"><p class="cw-more-caption">还有 ${rest.length} 项（已优先显示需关注事项）</p><div>${rest.map(renderItem).join("")}</div></div>` : "";
    })();
  const title = group.kind === "linked_source_snapshot" ? "来源会话快照" : group.kind === "project" ? `项目 · ${group.id}` : group.items.length === 1 && group.items[0].title.trim() ? group.items[0].title.trim() : "独立事项组";
  const source = group.kind === "linked_source_snapshot" ? group.source_snapshot : undefined;
  const remoteSource = group.kind === "project" && group.source_session_id
    ? `<p>来源项目 ${escapeHtml(group.id)} · 会话 ${escapeHtml(group.source_session_id)} · 记录版本 ${group.source_revision ?? 0}</p>` : "";
  const timerLabels: Record<string, string> = { waiting: "查看计时尚未完成", not_viewed: "明确查看尚未确认", unavailable: "查看计时证据不可用", unknown: "查看计时状态未知", elapsed: "查看计时已到期" };
  const timer = group.kind === "linked_source_snapshot" ? timerLabels[group.visibility_timer_status] ?? "查看计时状态未知" : "";
  const provenance = source ? `<p>项目 ${escapeHtml(source.project_id)} · 来源会话 ${escapeHtml(source.source_session_id)} · 捕获时间 ${escapeHtml(source.captured_at)}</p><p>仅为来源会话捕获时状态，不代表当前映射；结果未知、未验证。${source.viewed_at === null ? "卡片查看状态未确认。" : `卡片查看时间 ${escapeHtml(source.viewed_at)}；不代表各项结果已查看。`}${timer}，不代表当前工作完成。${source.superseded_unviewed_count > 0 ? `旧快照 ${source.superseded_unviewed_count} 个查看计时尚未确认。` : ""}</p>` : "";
  return `<article class="unified-current-work-group" data-cw-group-kind="${escapeHtml(group.kind)}"><h4>${escapeHtml(title)}</h4>${remoteSource}${provenance}${renderGroupSummary(group)}<div class="unified-current-work-items">${itemRows || `<p>组内事项暂不可展开</p>`}</div>${itemMore}</article>`;
}

function buildLiveQueueSection(
  groupsHtml: string,
  structuredGroupCount: number,
  liveSession: CurrentWorkLiveSessionInput | null | undefined,
): { bodyHtml: string; queueCount: number } {
  const liveHtml = renderCurrentWorkLiveQueueHtml(liveSession);
  const liveCount = currentWorkLiveQueueCount(liveSession);
  const combined = [groupsHtml, liveHtml].filter(Boolean).join("");
  const bodyHtml = combined
    || `<p class="current-work-zone-empty">暂无可见工作组</p>`;
  return { bodyHtml, queueCount: structuredGroupCount + liveCount };
}

export function buildCurrentWorkOverviewPanel(
  result: WorkRecordsOverviewResultEvent | null,
  options: {
    open: boolean;
    loadingMore?: boolean;
    unavailable?: boolean;
    expandedRequirementItemIds?: ReadonlySet<string>;
    detailLoadingCursor?: string | null;
    liveSession?: CurrentWorkLiveSessionInput | null;
    /** Unbound companion-home: mapped project sessions available for multi-project schedule tab. */
    mappedMultiProjectSchedule?: boolean;
    itemReorganize?: { mergeMode: boolean; pickedItemIds: ReadonlySet<string> };
  },
): CurrentWorkOverviewPanel {
  const liveSession = options.liveSession ?? null;
  const liveHtml = renderCurrentWorkLiveQueueHtml(liveSession);
  const liveCount = currentWorkLiveQueueCount(liveSession);
  const liveConfirmPending = currentWorkLiveConfirmPending(liveSession);
  if (!result) {
    if (!options.unavailable) {
      return { visible: false, open: options.open, label: "", bodyHtml: "", canExpand: false, moreLabel: "", loadingMore: false };
    }
    const queueBody = liveHtml || `<p class="current-work-zone-empty">暂无可见工作组</p>`;
    const label = liveConfirmPending ? "当前工作 · 等待确认"
      : liveCount ? "当前工作 · 进行中"
        : "当前工作 · 正在核实";
    const statusWarning = liveCount
      ? `<p class="unified-current-work-warning" role="status">工作记录概况尚在核实；下列为当前会话中的实时队列。</p>`
      : `<p class="unified-current-work-warning" role="status">当前会话概况正在核实，结果返回前不隐藏入口。</p>`;
    const queuePanel = `<section class="cw-panel is-active" data-cw-panel="queue" aria-label="队列"><div class="current-work-board"><header class="current-work-zone-head"><h3>队列</h3><span>${liveCount}</span></header><div class="current-work-zone-body">${queueBody}</div></div></section>`;
    return {
      visible: true,
      open: options.open,
      label,
      bodyHtml: `${statusWarning}<div class="current-work-page-body" data-cw-segment="queue">${queuePanel}</div>`,
      canExpand: false,
      moreLabel: "",
      loadingMore: false,
    };
  }
  const mappedScheduleEntry = Boolean(options.mappedMultiProjectSchedule);
  const visible = shouldShowCurrentWorkOverview(result) || liveCount > 0 || mappedScheduleEntry;
  const unreadCountText = unreadCountSummary(result);
  const unreadCount = unreadCountText === null ? null : Number.parseInt(unreadCountText.replace(/\D/g, ""), 10);
  const attentionCount = groupAttentionCount(result);
  const waitingForServerCollapse = result.state === "visible" && result.unread_status === "known"
    && unreadCount === 0 && unreadCountText !== null && result.visibility_limitations.length === 0 && !result.page.has_more
    && result.groups.length === result.visibility_counts.visible_groups && result.groups.length > 0
    && !result.groups.some(groupNeedsAttention);
  let statusHtml = "";
  if (result.unread_status !== "known") statusHtml = `<p class="unified-current-work-warning" role="status">部分结果的查看状态未知；暂不据此隐藏当前工作。</p>`;
  if (result.state === "error" || result.state === "cancelled") statusHtml += `<p class="unified-current-work-warning" role="status">概况暂不可确认，可刷新重试。</p>`;
  if (result.state === "expired") statusHtml += `<p class="unified-current-work-warning" role="status">概况数据已变化，正在重新查询。</p>`;
  if (result.visibility_limitations.length) statusHtml += `<p class="unified-current-work-warning">部分事项按保守范围展示。</p>`;
  let scheduleHtml = "";
  if (result.aggregation_scope === "selected_project_sessions" && result.schedule_preview) {
    const preview = result.schedule_preview;
    const candidateRows = preview.candidates.map(row =>
      `<li>${escapeHtml(row.project_id)} · ${escapeHtml(row.item_id)} · ${escapeHtml(row.requirement_id)} · 优先级 ${row.priority}</li>`).join("");
    const blockedRows = preview.blocked.map(row =>
      `<li>${escapeHtml(row.project_id)} · ${escapeHtml(row.item_id)} · ${escapeHtml(row.requirement_id)}：${row.reasons.map(escapeHtml).join("；")}</li>`).join("");
    const limits = preview.limitations.map(escapeHtml).filter(Boolean).join("；");
    scheduleHtml = `<div class="unified-current-work-schedule" role="region" aria-label="只读调度预览"><header><h4>只读调度预览</h4><p>不会启动任务，也不代表运行授权。</p></header>${preview.candidates.length ? `<ul class="cw-queue-list">${candidateRows}</ul>` : "<p>当前没有可显示的候选</p>"}${blockedRows ? `<h5>受阻</h5><ul class="cw-queue-list is-blocked">${blockedRows}</ul>` : ""}${limits ? `<p class="cw-schedule-limits">${limits}</p>` : ""}</div>`;
  }
  const activeGroups = result.groups.filter(group => group.visibility === "visible");
  const expandedRequirementItemIds = options.expandedRequirementItemIds ?? new Set<string>();
  const dependencyCandidateItems = activeGroups.filter(group => group.kind === "independent").flatMap(group => group.items);
  const serverAttentionItems = overviewAttentionItemTotal(result);
  const serverAttentionGroups = overviewAttentionGroupTotal(result);
  const visibleAttentionCount = serverAttentionGroups ?? activeGroups.filter(groupNeedsAttention).length;
  const visibleAttentionItemCount = serverAttentionItems ?? countVisibleAttentionItems(result);
  const passiveUnreadNote = formatOverviewPassiveUnreadNote(unreadCountText, visibleAttentionItemCount);
  // Keep server order for overview contracts/tests; mark attention visually instead of re-sorting columns.
  const hasIndependentItems = activeGroups.some(group => group.kind === "independent" && group.items.length > 0);
  const reorganizePrefix = hasIndependentItems
    ? renderIndependentReorganizeBar(Boolean(options.itemReorganize?.mergeMode))
    : "";
  const groupsHtml = activeGroups.map(group => {
    const html = renderGroup(group, expandedRequirementItemIds, dependencyCandidateItems, options.detailLoadingCursor, options.itemReorganize);
    return groupNeedsAttention(group) ? `<div class="cw-attention-wrap">${html}</div>` : html;
  }).join("");
  const loadedGroupCount = activeGroups.length;
  const totalVisibleGroups = result.page.total_visible_groups;
  const queueHeaderCount = String(loadedGroupCount + liveCount);
  const { bodyHtml: queueBodyHtml } = buildLiveQueueSection(
    `${reorganizePrefix}${groupsHtml}`, loadedGroupCount, liveSession);
  const attentionLabel = serverAttentionItems !== null && serverAttentionItems > 0
    ? `当前工作 · ${serverAttentionItems} 项需关注`
    : serverAttentionItems === null && visibleAttentionItemCount > 0
    ? "当前工作 · 有待办事项"
    : "";
  const label = mappedScheduleEntry && !shouldShowCurrentWorkOverview(result) && liveCount === 0
    ? "当前工作 · 多项目调度"
    : liveConfirmPending && !activeGroups.length
    ? "当前工作 · 等待确认"
    : attentionLabel || (visibleAttentionCount > 0
      ? `当前工作 · ${visibleAttentionCount} 组需关注`
      : result.unread_status !== "known" && (liveCount > 0 || attentionCount > 0 || liveConfirmPending)
      ? "当前工作 · 状态待确认"
      : result.unread_status !== "known"
      ? "当前工作 · 概况待核实"
      : result.page.has_more ? `当前工作 · 还有余项`
        : waitingForServerCollapse ? "当前工作 · 已查看，待收起" : "当前工作");
  if (liveCount && !activeGroups.length) {
    statusHtml += `<p class="unified-current-work-warning" role="status">Structured 工作组尚未出现；下列来自当前会话中的编排或授权状态。</p>`;
  }
  const remaining = Math.max(0, result.page.total_visible_groups - activeGroups.length);
  const canExpand = result.state === "visible" && result.page.has_more && Boolean(result.page.next_cursor);
  const unreadMetric = result.unread_status === "known" && unreadCountText !== null
    ? (visibleAttentionItemCount === 0 && Number.parseInt(unreadCountText.replace(/\D/g, ""), 10) > 8
      ? "较多" : unreadCountText)
    : "未知";
  const unreadOverviewLine = result.unread_status === "known"
    ? (passiveUnreadNote ?? "暂无未打开记录")
    : "未打开记录数未知";
  const summaryLine = [
    `${result.visibility_counts.visible_groups} 组当前可见`,
    visibleAttentionCount ? `${visibleAttentionCount} 组需关注` : "",
    unreadOverviewLine,
  ].filter(Boolean).join(" · ");
  const overviewPanel = `<section class="cw-panel" data-cw-panel="overview" hidden aria-label="概况"><div class="current-work-strip">${statusHtml}<p class="unified-current-work-summary">${summaryLine}</p></div><div class="cw-metric-row"><div class="cw-metric"><span>可见组</span><strong>${result.visibility_counts.visible_groups}</strong></div><div class="cw-metric"><span>需关注</span><strong>${visibleAttentionCount}</strong></div><div class="cw-metric"><span>未打开</span><strong>${escapeHtml(unreadMetric)}</strong></div></div></section>`;
  const queuePanel = `<section class="cw-panel is-active" data-cw-panel="queue" aria-label="队列"><div class="current-work-board"><header class="current-work-zone-head"><h3>队列</h3><span title="已加载工作组 / 可见组总数">${queueHeaderCount}</span></header><div class="current-work-zone-body">${queueBodyHtml}</div></div></section>`;
  const schedulePanel = `<section class="cw-panel" data-cw-panel="schedule" hidden aria-label="调度">${scheduleHtml || CURRENT_WORK_SCHEDULE_EMPTY_MARKUP}<div data-cw-schedule-slot></div></section>`;
  const bodyHtml = `<div class="current-work-page-body" data-cw-segment="queue">${overviewPanel}${queuePanel}${schedulePanel}</div>`;
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

type CurrentWorkShellSegment = "overview" | "queue" | "schedule";

function extractCwPanelInnerHtml(bodyHtml: string, panel: CurrentWorkShellSegment): string {
  const pattern = new RegExp(`<section\\s+class="cw-panel[^"]*"\\s+data-cw-panel="${panel}"[^>]*>`, "i");
  const startMatch = bodyHtml.match(pattern);
  if (!startMatch || startMatch.index === undefined) return "";
  let index = startMatch.index + startMatch[0].length;
  let depth = 1;
  const tagPattern = /<\/section>|<section\b/gi;
  tagPattern.lastIndex = index;
  let tagMatch: RegExpExecArray | null;
  while ((tagMatch = tagPattern.exec(bodyHtml)) !== null) {
    if (tagMatch[0].toLowerCase() === "</section>") {
      depth -= 1;
      if (depth === 0) return bodyHtml.slice(index, tagMatch.index);
    } else {
      depth += 1;
    }
  }
  return "";
}

function splitCurrentWorkPanelBodies(bodyHtml: string, extrasHtml: string): Record<CurrentWorkShellSegment, string> {
  const overview = extractCwPanelInnerHtml(bodyHtml, "overview");
  let queue = extractCwPanelInnerHtml(bodyHtml, "queue");
  let schedule = extractCwPanelInnerHtml(bodyHtml, "schedule");
  if (extrasHtml) {
    if (schedule.includes("data-cw-schedule-slot")) {
      schedule = schedule.replace('<div data-cw-schedule-slot></div>', `<div data-cw-schedule-slot>${extrasHtml}</div>`);
    } else {
      schedule += extrasHtml;
    }
  }
  return {
    overview: overview || `<p class="current-work-zone-empty">暂无概况数据。</p>`,
    queue: queue || `<p class="current-work-zone-empty">暂无进行中的工作。</p>`,
    schedule: schedule || CURRENT_WORK_SCHEDULE_EMPTY_MARKUP,
  };
}

/** In-shell side panel: conversation stays primary; work overview is secondary chrome. */
export function renderCurrentWorkShellPanel(
  panel: CurrentWorkOverviewPanel,
  extrasHtml = "",
  defaultSegment: CurrentWorkShellSegment = "queue",
): string {
  if (!panel.visible) {
    return `<div class="current-work-shell" data-current-work-shell>
      <header class="unified-current-work-heading">
        <div><span>当前会话</span><h3 id="current-work-page-heading">当前工作</h3></div>
        <button type="button" class="unified-current-work-close" id="unified-current-work-close" aria-label="收起当前工作" title="收起">×</button>
      </header>
      <p class="current-work-zone-empty" role="status">暂无可见工作概况。</p>
    </div>`;
  }
  const segments = splitCurrentWorkPanelBodies(panel.bodyHtml, extrasHtml);
  const segmentTab = (id: CurrentWorkShellSegment, label: string) => {
    const selected = defaultSegment === id;
    return `<button type="button" class="cw-segment" role="tab" data-cw-segment-select="${id}" aria-selected="${selected ? "true" : "false"}" tabindex="${selected ? "0" : "-1"}">${label}</button>`;
  };
  const segmentPanel = (id: CurrentWorkShellSegment, html: string) =>
    `<div class="cw-segment-panel unified-current-work-content" data-cw-segment-panel="${id}" role="tabpanel"${defaultSegment === id ? "" : " hidden"}>${html}</div>`;
  return `<div class="current-work-shell" data-current-work-shell data-cw-segment="${defaultSegment}">
    <header class="unified-current-work-heading">
      <div><span>当前会话</span><h3 id="current-work-page-heading">${escapeHtml(panel.label)}</h3></div>
      <button type="button" class="unified-current-work-close" id="unified-current-work-close" aria-label="收起当前工作" title="收起">×</button>
    </header>
    <nav class="cw-segments" role="tablist" aria-label="当前工作分区">
      ${segmentTab("overview", "概况")}
      ${segmentTab("queue", "进行中")}
      ${segmentTab("schedule", "调度")}
    </nav>
    <div class="unified-current-work-popover current-work-scroll" id="unified-current-work-popover">
      ${segmentPanel("overview", segments.overview)}
      ${segmentPanel("queue", segments.queue)}
      ${segmentPanel("schedule", segments.schedule)}
    </div>
    <footer class="unified-current-work-actions">
      ${panel.canExpand ? `<button type="button" class="unified-btn unified-btn-ghost" id="unified-current-work-more" data-current-work-more ${panel.loadingMore ? "disabled" : ""}>${escapeHtml(panel.moreLabel)}</button>` : ""}
      <button type="button" class="unified-btn unified-btn-ghost" id="unified-current-work-ask" data-current-work-ask>在对话中查询</button>
    </footer>
    <div class="cw-contract-body" hidden aria-hidden="true">${panel.bodyHtml}</div>
  </div>`;
}

/** @deprecated Full-page landing removed; kept as alias for tests/importers. */
export function renderCurrentWorkWorkbenchPage(panel: CurrentWorkOverviewPanel, extrasHtml = ""): string {
  return renderCurrentWorkShellPanel(panel, extrasHtml);
}
