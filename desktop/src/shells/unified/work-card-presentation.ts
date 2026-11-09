/** §4.5 / DAY-JOURNAL-MEMORY: default visible requirement rows per work card snapshot. */
export const WORK_CARD_DEFAULT_VISIBLE_REQUIREMENTS = 5;

export type WorkCardPresentationStatus = "loading" | "resolved" | "missing" | "unavailable";

export interface WorkCardReferenceInput {
  presentationId: string;
  cardId: string;
  text: string;
}

export interface WorkCardPresentationEntry extends WorkCardReferenceInput {
  sessionId: string;
  status: WorkCardPresentationStatus;
  card: Record<string, any> | null;
  origin: "live" | "history";
}

export type WorkCardMutationResult =
  | { kind: "added" | "resolved" | "missing" | "duplicate"; entry: WorkCardPresentationEntry }
  | { kind: "stale" | "rejected" };

const PRESENTATION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CARD_ID = /^wc-[0-9a-f]{64}$/;
const object = (value: unknown): value is Record<string, any> => Boolean(value && typeof value === "object" && !Array.isArray(value));

const LINKED_COPY_KEYS = [
  "kind", "session_id", "captured_at", "copy_id", "completeness_scope", "project_id",
  "source_session_id", "source_item_digest", "binding_id", "item_id", "item_title",
  "item_status_at_capture", "requirement_id", "requirement_text", "requirement_status_at_capture",
  "worker_id", "worker_run_id", "host_start", "host_lifecycle", "operation_result", "verification",
];
const LINKED_SOURCE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const LINKED_DIGEST = /^[0-9a-f]{64}$/;
const LINKED_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const LINKED_ITEM_STATUSES = new Set(["active", "completed", "in_progress", "unknown", "blocked", "stopped"]);
const LINKED_REQUIREMENT_STATUSES = new Set(["pending", "completed", "in_progress", "unknown", "blocked", "stopped"]);
const LINKED_HOST_STATUSES = new Set(["model_turn_finished", "blocked", "failed", "cancelled", "limit_reached", "unknown"]);
const LINKED_FINISH_REASONS = new Set(["completed", "cancelled", "timeout", "error", "paused", "unknown"]);
const LINKED_RECEIPT = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

function validLinkedItemCopySnapshot(snapshot: unknown, sessionId: string): snapshot is Record<string, any> {
  if (!object(snapshot) || !exactKeys(snapshot, LINKED_COPY_KEYS)
    || snapshot.kind !== "linked_item_copy" || snapshot.session_id !== sessionId
    || snapshot.completeness_scope !== "item_requirement"
    || snapshot.operation_result !== "unknown" || snapshot.verification !== "not_verified"
    || typeof snapshot.captured_at !== "string" || snapshot.captured_at.length > 64
    || !LINKED_TIME.test(snapshot.captured_at) || !Number.isFinite(Date.parse(snapshot.captured_at))) return false;
  for (const key of ["project_id", "source_session_id", "binding_id", "item_id", "requirement_id", "worker_id", "worker_run_id"]) {
    if (typeof snapshot[key] !== "string" || !LINKED_SOURCE_ID.test(snapshot[key])) return false;
  }
  if (typeof snapshot.copy_id !== "string" || !LINKED_DIGEST.test(snapshot.copy_id)
    || typeof snapshot.source_item_digest !== "string" || !LINKED_DIGEST.test(snapshot.source_item_digest)) return false;
  const textWithinLimit = (value: unknown, nonempty: boolean): value is string => {
    if (typeof value !== "string" || (nonempty && value.length === 0)) return false;
    return new TextEncoder().encode(value).length <= 4096;
  };
  if (!textWithinLimit(snapshot.item_title, true) || !textWithinLimit(snapshot.requirement_text, true)
    || typeof snapshot.item_status_at_capture !== "string" || !LINKED_ITEM_STATUSES.has(snapshot.item_status_at_capture)
    || typeof snapshot.requirement_status_at_capture !== "string"
    || !LINKED_REQUIREMENT_STATUSES.has(snapshot.requirement_status_at_capture)) return false;

  const start = snapshot.host_start;
  const lifecycle = snapshot.host_lifecycle;
  return object(start) && exactKeys(start, ["reservation_id", "parent_run_id", "turn_epoch"])
    && typeof start.reservation_id === "string" && LINKED_RECEIPT.test(start.reservation_id)
    && typeof start.parent_run_id === "string" && LINKED_RECEIPT.test(start.parent_run_id)
    && Number.isSafeInteger(start.turn_epoch) && start.turn_epoch > 0
    && object(lifecycle) && exactKeys(lifecycle, ["sequence", "status", "finish_reason"])
    && lifecycle.sequence === start.turn_epoch
    && typeof lifecycle.status === "string" && LINKED_HOST_STATUSES.has(lifecycle.status)
    && typeof lifecycle.finish_reason === "string" && LINKED_FINISH_REASONS.has(lifecycle.finish_reason)
    && (lifecycle.status !== "unknown" || lifecycle.finish_reason === "unknown");
}

const LINKED_PROJECT_COPY_KEYS = ["kind", "session_id", "captured_at", "copy_id", "binding_id", "source"];
const LINKED_PROJECT_SOURCE_KEYS = ["scope", "mapping_claim", "project_id", "source_session_id", "source_revision", "items", "worker_facts"];
const LINKED_PROJECT_ITEM_STATUSES = new Set(["active", "completed", "in_progress", "unknown", "blocked", "stopped"]);
const LINKED_PROJECT_REQUIREMENT_STATUSES = new Set(["pending", "completed", "in_progress", "unknown", "blocked", "stopped"]);
const LINKED_PROJECT_HOST_STATUSES = new Set(["model_turn_finished", "blocked", "failed", "cancelled", "limit_reached", "unknown"]);
const LINKED_PROJECT_FINISH_REASONS = new Set(["completed", "cancelled", "timeout", "error", "paused", "unknown"]);

function validLinkedProjectSessionCopySnapshot(snapshot: unknown, sessionId: string): snapshot is Record<string, any> {
  const sourceId = LINKED_SOURCE_ID;
  const digest = LINKED_DIGEST;
  if (!object(snapshot) || !exactKeys(snapshot, LINKED_PROJECT_COPY_KEYS)
    || snapshot.kind !== "linked_project_session_copy" || snapshot.session_id !== sessionId
    || typeof snapshot.captured_at !== "string" || snapshot.captured_at.length > 64
    || !LINKED_TIME.test(snapshot.captured_at) || !Number.isFinite(Date.parse(snapshot.captured_at))
    || typeof snapshot.copy_id !== "string" || !digest.test(snapshot.copy_id)
    || typeof snapshot.binding_id !== "string" || !sourceId.test(snapshot.binding_id)
    || !object(snapshot.source) || !exactKeys(snapshot.source, LINKED_PROJECT_SOURCE_KEYS)) return false;
  const source = snapshot.source;
  if (source.scope !== "authorized_source_session_only" || source.mapping_claim !== "not_asserted"
    || typeof source.project_id !== "string" || !sourceId.test(source.project_id)
    || typeof source.source_session_id !== "string" || !sourceId.test(source.source_session_id)
    || !Number.isSafeInteger(source.source_revision) || source.source_revision < 0
    || !Array.isArray(source.items) || source.items.length > 500
    || !Array.isArray(source.worker_facts) || source.worker_facts.length < 1 || source.worker_facts.length > 20) return false;
  const items = new Map<string, Set<string>>();
  let requirementCount = 0;
  for (const item of source.items) {
    if (!object(item) || !exactKeys(item, ["item_id", "title", "status", "requirements"])
      || typeof item.item_id !== "string" || !sourceId.test(item.item_id) || items.has(item.item_id)
      || typeof item.title !== "string" || !item.title || new TextEncoder().encode(item.title).length > 4096
      || typeof item.status !== "string" || !LINKED_PROJECT_ITEM_STATUSES.has(item.status)
      || !Array.isArray(item.requirements)) return false;
    const requirements = new Set<string>();
    for (const requirement of item.requirements) {
      if (!object(requirement) || !exactKeys(requirement, ["requirement_id", "text", "status"])
        || typeof requirement.requirement_id !== "string" || !sourceId.test(requirement.requirement_id)
        || requirements.has(requirement.requirement_id)
        || typeof requirement.text !== "string" || !requirement.text
        || new TextEncoder().encode(requirement.text).length > 4096
        || typeof requirement.status !== "string" || !LINKED_PROJECT_REQUIREMENT_STATUSES.has(requirement.status)) return false;
      requirements.add(requirement.requirement_id);
      requirementCount += 1;
      if (requirementCount > 5000) return false;
    }
    items.set(item.item_id, requirements);
  }
  const bindings = new Set<string>();
  let hasAnchor = false;
  for (const fact of source.worker_facts) {
    if (!object(fact) || !exactKeys(fact, ["binding_id", "item_id", "requirement_id", "worker_id", "worker_run_id",
      "host_start", "host_lifecycle", "operation_result", "verification"])
      || !["binding_id", "item_id", "requirement_id", "worker_id", "worker_run_id"]
        .every(key => typeof fact[key] === "string" && sourceId.test(fact[key]))
      || bindings.has(fact.binding_id) || !items.get(fact.item_id)?.has(fact.requirement_id)
      || fact.operation_result !== "unknown" || fact.verification !== "not_verified") return false;
    bindings.add(fact.binding_id);
    if (fact.binding_id === snapshot.binding_id) hasAnchor = true;
    const start = fact.host_start;
    const lifecycle = fact.host_lifecycle;
    if (!object(start) || !exactKeys(start, ["reservation_id", "parent_run_id", "turn_epoch"])
      || typeof start.reservation_id !== "string" || !sourceId.test(start.reservation_id)
      || typeof start.parent_run_id !== "string" || !sourceId.test(start.parent_run_id)
      || !Number.isSafeInteger(start.turn_epoch) || start.turn_epoch < 1
      || !object(lifecycle) || !exactKeys(lifecycle, ["sequence", "status", "finish_reason"])
      || lifecycle.sequence !== start.turn_epoch
      || typeof lifecycle.status !== "string" || !LINKED_PROJECT_HOST_STATUSES.has(lifecycle.status)
      || typeof lifecycle.finish_reason !== "string" || !LINKED_PROJECT_FINISH_REASONS.has(lifecycle.finish_reason)
      || (lifecycle.status === "unknown" && lifecycle.finish_reason !== "unknown")) return false;
  }
  if (!hasAnchor) return false;
  try { return new TextEncoder().encode(JSON.stringify(source)).length <= 65536; }
  catch { return false; }
}

function validProjectScopedRequirement(
  item: Record<string, any>, requirement: Record<string, any>, sessionId: string, projectId: string,
): boolean {
  const result = requirement.latest_result;
  if (item.session_id !== sessionId || item.project_id !== projectId
    || !Array.isArray(item.source_refs)
    || !item.source_refs.every((source: unknown) => object(source)
      && source.kind === "project" && source.id === projectId)
    || requirement.session_id !== sessionId || requirement.project_id !== projectId
    || requirement.item_id !== item.item_id || requirement.current_result_missing !== false
    || !object(requirement.source_ref) || requirement.source_ref.kind !== "project"
    || requirement.source_ref.id !== projectId || !Number.isSafeInteger(requirement.result_count)
    || requirement.result_count < 0) return false;
  // Hidden prior results are not the current run's outcome.
  if (result === null && (requirement.status === "pending" || requirement.status === "in_progress")) {
    return item.status === "unknown" && requirement.omitted_result_count === requirement.result_count
      && (requirement.current_run_id === null
        ? requirement.status === "pending" && requirement.latest_result_is_current === null
        : typeof requirement.current_run_id === "string" && !!requirement.current_run_id
          && requirement.latest_result_is_current === false);
  }
  if (!object(result) || result.session_id !== sessionId || result.project_id !== projectId
    || result.item_id !== item.item_id || result.requirement_id !== requirement.requirement_id
    || typeof result.run_id !== "string" || !result.run_id
    || result.verification_status !== "not_verified") return false;

  const startedUnknown = requirement.status === "unknown"
    && typeof requirement.current_run_id === "string" && !!requirement.current_run_id
    && requirement.latest_result_is_current === true
    && result.status === "unknown" && result.run_id === requirement.current_run_id;
  const blockedBeforeStart = item.status === "unknown" && requirement.status === "blocked"
    && requirement.current_run_id === null && requirement.latest_result_is_current === null
    && result.status === "blocked";
  return startedUnknown || blockedBeforeStart;
}

function validPublicCard(card: unknown, sessionId: string, expectedCardId: string): card is Record<string, any> {
  if (!object(card) || card.card_id !== expectedCardId || !object(card.snapshot)) return false;
  const snapshot = card.snapshot;
  if (snapshot.kind === "linked_item_copy") return validLinkedItemCopySnapshot(snapshot, sessionId);
  if (snapshot.kind === "linked_project_session_copy") return validLinkedProjectSessionCopySnapshot(snapshot, sessionId);
  if (snapshot.session_id !== sessionId || snapshot.completeness !== "complete"
    || typeof snapshot.captured_at !== "string") return false;
  const items = snapshot.kind === "independent" ? [snapshot.card] : snapshot.cards;
  if (!Array.isArray(items)) return false;
  if (snapshot.kind !== "project_scoped") return true; // retain legacy saved-card compatibility
  if (typeof snapshot.project_id !== "string" || !snapshot.project_id
    || snapshot.omitted_item_count !== 0 || snapshot.omitted_requirement_count !== 0
    || snapshot.incomplete_reason !== null || snapshot.missing_current_result_count !== 0
    || snapshot.item_count !== items.length) return false;
  let requirementCount = 0;
  let resultCount = 0;
  for (const item of items) {
    if (!object(item) || item.session_id !== sessionId || item.project_id !== snapshot.project_id
      || typeof item.item_id !== "string" || !item.item_id || typeof item.title !== "string"
      || typeof item.status !== "string" || !Array.isArray(item.source_refs)
      || !item.source_refs.every((source: unknown) => object(source)
        && source.kind === "project" && source.id === snapshot.project_id)
      || !Array.isArray(item.requirements)) return false;
    requirementCount += item.requirements.length;
    for (const requirement of item.requirements) {
      if (!object(requirement) || !validProjectScopedRequirement(item, requirement, sessionId, snapshot.project_id)) return false;
      resultCount += requirement.result_count;
    }
  }
  return snapshot.requirement_count === requirementCount && snapshot.result_count === resultCount;
}

export class WorkCardPresentationRegistry {
  private sessionId: string;
  private entries = new Map<string, WorkCardPresentationEntry>();

  constructor(sessionId: string) {
    this.sessionId = sessionId;
  }

  setSession(sessionId: string): void {
    if (sessionId === this.sessionId) return;
    this.sessionId = sessionId;
    this.entries.clear();
  }

  present(event: unknown): WorkCardMutationResult {
    if (!object(event) || event.type !== "work-card.present"
      || event.session_id !== this.sessionId
      || typeof event.presentation_id !== "string" || !PRESENTATION_ID.test(event.presentation_id)
      || typeof event.card_id !== "string" || !CARD_ID.test(event.card_id)
      || !validPublicCard(event.card, this.sessionId, event.card_id)) return { kind: "rejected" };
    const prior = this.entries.get(event.presentation_id);
    if (prior) {
      if (prior.sessionId !== this.sessionId || prior.cardId !== event.card_id
        || (prior.card !== null && prior.card.card_id !== event.card_id)) return { kind: "rejected" };
      if (prior.origin === "history" && (prior.status !== "resolved" || prior.card === null)) {
        prior.status = "resolved";
        prior.card = event.card;
        return { kind: "resolved", entry: prior };
      }
      return { kind: "duplicate", entry: prior };
    }
    const entry: WorkCardPresentationEntry = {
      sessionId: this.sessionId,
      presentationId: event.presentation_id,
      cardId: event.card_id,
      text: "",
      status: "resolved",
      card: event.card,
      origin: "live",
    };
    this.entries.set(entry.presentationId, entry);
    return { kind: "added", entry };
  }

  reference(reference: WorkCardReferenceInput): WorkCardPresentationEntry | null {
    if (!reference || typeof reference !== "object"
      || typeof reference.presentationId !== "string" || !PRESENTATION_ID.test(reference.presentationId)
      || typeof reference.cardId !== "string" || !CARD_ID.test(reference.cardId)
      || typeof reference.text !== "string") return null;
    const prior = this.entries.get(reference.presentationId);
    if (prior) return prior.sessionId === this.sessionId && prior.cardId === reference.cardId ? prior : null;
    const entry: WorkCardPresentationEntry = {
      ...reference,
      sessionId: this.sessionId,
      status: "loading",
      card: null,
      origin: "history",
    };
    this.entries.set(entry.presentationId, entry);
    return entry;
  }

  resolve(presentationId: string, responseSessionId: string, card: unknown | null): WorkCardMutationResult {
    if (responseSessionId !== this.sessionId) return { kind: "stale" };
    const entry = this.entries.get(presentationId);
    if (!entry || entry.sessionId !== this.sessionId || entry.origin !== "history") return { kind: "stale" };
    if (card === null) {
      entry.status = "missing";
      entry.card = null;
      return { kind: "missing", entry };
    }
    if (!validPublicCard(card, this.sessionId, entry.cardId)) return { kind: "rejected" };
    entry.status = "resolved";
    entry.card = card;
    return { kind: "resolved", entry };
  }

  fail(presentationId: string, responseSessionId: string, message: string): WorkCardMutationResult {
    if (responseSessionId !== this.sessionId) return { kind: "stale" };
    const entry = this.entries.get(presentationId);
    if (!entry || entry.sessionId !== this.sessionId || entry.origin !== "history") return { kind: "stale" };
    entry.status = "unavailable";
    entry.text = message;
    entry.card = null;
    return { kind: "resolved", entry };
  }
  get(presentationId: string): WorkCardPresentationEntry | undefined {
    return this.entries.get(presentationId);
  }

  values(): WorkCardPresentationEntry[] {
    return [...this.entries.values()];
  }
}

export type WorkCardReceivedIdentity = { sessionId: string; presentationId: string; cardId: string };
export type WorkCardReceiveOutcome = {
  mutation: WorkCardMutationResult;
  receiptAttempt: "not_eligible" | "attempted" | "failed";
  error?: unknown;
};

/** Merge the current-session presentation before attempting its idempotent receipt.
 * A successful send is only a WebSocket send attempt, not durable client storage or user viewing.
 */
/** Companion home keeps live project-scoped snapshots in 当前工作, not chat bubbles. */
export function companionHomeDefersWorkCardChatPresentation(
  mainFocus: string,
  card: unknown,
): boolean {
  if (mainFocus !== "chat" || !object(card) || !object(card.snapshot)) return false;
  const kind = card.snapshot.kind;
  return kind === "project_scoped" || kind === "linked_project_session_copy";
}

export function receiveWorkCardPresentation(options: {
  registry: WorkCardPresentationRegistry;
  event: unknown;
  mergeReference: (entry: WorkCardPresentationEntry) => void;
  sendReceived: (identity: WorkCardReceivedIdentity) => void;
}): WorkCardReceiveOutcome {
  const mutation = options.registry.present(options.event);
  if (mutation.kind !== "added" && mutation.kind !== "resolved" && mutation.kind !== "duplicate") {
    return { mutation, receiptAttempt: "not_eligible" };
  }
  options.mergeReference(mutation.entry);
  const identity = { sessionId: mutation.entry.sessionId, presentationId: mutation.entry.presentationId, cardId: mutation.entry.cardId };
  try {
    options.sendReceived(identity);
    return { mutation, receiptAttempt: "attempted" };
  } catch (error) {
    return { mutation, receiptAttempt: "failed", error };
  }
}
export function failPendingHistoryCardRead(options: {
  cardId: string;
  sessionId: string;
  currentSessionId: string;
  pendingReads: Map<string, string>;
  registry: WorkCardPresentationRegistry;
  message: string;
}): boolean {
  const { cardId, sessionId, currentSessionId, pendingReads, registry, message } = options;
  if (sessionId !== currentSessionId || pendingReads.get(cardId) !== sessionId) return false;
  pendingReads.delete(cardId);
  for (const entry of registry.values()) {
    if (entry.origin === "history" && entry.cardId === cardId && entry.status === "loading") {
      registry.fail(entry.presentationId, sessionId, message);
    }
  }
  return true;
}

export function requestHistoryCardRead(options: {
  cardId: string;
  sessionId: string;
  currentSessionId: string;
  pendingReads: Map<string, string>;
  registry: WorkCardPresentationRegistry;
  request: (cardId: string) => void;
}): "requested" | "failed" | "stale" | "duplicate" {
  const { cardId, sessionId, currentSessionId, pendingReads, registry, request } = options;
  if (sessionId !== currentSessionId) return "stale";
  if (pendingReads.has(cardId)) return "duplicate";
  pendingReads.set(cardId, sessionId);
  try {
    request(cardId);
    return "requested";
  } catch (error) {
    failPendingHistoryCardRead({
      cardId, sessionId, currentSessionId, pendingReads, registry,
      message: error instanceof Error ? error.message : String(error),
    });
    return "failed";
  }
}
export function shouldClearPendingHistoryCardReads(previousSessionId: string, nextSessionId: string): boolean {
  return previousSessionId !== "" && previousSessionId !== nextSessionId;
}

export function clearPendingHistoryCardReads(pendingReads: Map<string, string>): void {
  pendingReads.clear();
}

export function escapeWorkCardHtml(value: unknown): string {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function statusLabel(status: unknown): string {
  const labels: Record<string, string> = {
    active: "进行中",
    in_progress: "进行中",
    pending: "待处理",
    blocked: "受阻",
    completed: "已完成",
    stopped: "已停止",
    unknown: "状态未知",
  };
  return labels[String(status)] ?? `状态：${String(status ?? "未提供")}`;
}

function statusSummary(records: unknown[], getStatus: (record: Record<string, any>) => unknown): string {
  const counts = new Map<string, number>();
  for (const record of records) {
    if (!object(record)) continue;
    const label = statusLabel(getStatus(record));
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts].map(([label, count]) => `${label} ${count}`).join(" · ") || "状态未记录";
}

const PREVIEW_TEXT_CHARS = 56;
const PREVIEW_RESULT_CHARS = 88;

function previewTextSnippet(value: unknown, max = PREVIEW_TEXT_CHARS): string {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return "（无要求文本）";
  const chars = [...trimmed];
  return chars.length > max ? `${chars.slice(0, max).join("")}…` : trimmed;
}

function plainTextFromHtml(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function resultPreviewHeadline(requirement: Record<string, any>, snapshot: Record<string, any>): string {
  const result = requirement.latest_result;
  if (!object(result)) return "暂无结果记录";
  const lifecycle = lifecycleSummary(result.summary, result, snapshot);
  if (lifecycle.kind === "valid" && lifecycle.html) {
    const plain = plainTextFromHtml(lifecycle.html);
    return plain.length > PREVIEW_RESULT_CHARS ? `${plain.slice(0, PREVIEW_RESULT_CHARS)}…` : plain;
  }
  if (lifecycle.kind === "invalid") return "机器回执未通过校验";
  const summary = typeof result.summary === "string" ? result.summary.trim() : "";
  if (summary && !summary.includes(LIFECYCLE_KIND)) {
    return summary.length > PREVIEW_RESULT_CHARS ? `${summary.slice(0, PREVIEW_RESULT_CHARS)}…` : summary;
  }
  if (result.verification_status === "passed") return "验证已通过";
  if (result.verification_status === "failed") return "验证未通过";
  if (result.status && result.status !== "unknown") return statusLabel(result.status);
  return "结果状态未知";
}

function renderCollapsedViewedState(viewedAt: unknown): string {
  if (viewedAt === undefined) return "";
  if (viewedAt === null) return '<p class="work-card-preview-meta">卡片查看状态未确认</p>';
  if (typeof viewedAt === "string" && viewedAt.trim()) {
    const at = escapeWorkCardHtml(viewedAt.trim());
    return `<p class="work-card-preview-meta">已查看详情 · <time datetime="${at}">${at}</time> · 不代表认可或验收</p>`;
  }
  return "";
}

function renderCollapsedRequirementHighlights(
  rankedRows: RequirementRow[], items: Record<string, any>[], changeFocus: ChangeFocus | null, limit = 2,
): string {
  if (!rankedRows.length) return "";
  const multiItem = items.length > 1;
  const lines = rankedRows.slice(0, limit).map(row => {
    const focus = changeFocusMatches(row, changeFocus) ? " · 本次更新" : "";
    const itemPrefix = multiItem && row.itemTitle ? `${row.itemTitle} · ` : "";
    const request = previewTextSnippet(row.requirement.text);
    const headline = resultPreviewHeadline(row.requirement, row.snapshot);
    return `<li><span class="work-card-preview-highlight-text">${escapeWorkCardHtml(itemPrefix + request)}</span> · ${escapeWorkCardHtml(headline)} · ${escapeWorkCardHtml(statusLabel(row.requirement.status))}${focus}</li>`;
  }).join("");
  return `<ul class="work-card-preview-highlights" aria-label="要求与结果摘要">${lines}</ul>`;
}

function renderOrdinarySnapshotCollapsedPreview(options: {
  rows: RequirementRow[];
  rankedRows: RequirementRow[];
  items: Record<string, any>[];
  isIndependent: boolean;
  capturedAt: string;
  changeFocus: ChangeFocus | null;
  viewedAt: unknown;
}): string {
  const reqCount = options.rows.length;
  const requirementRollup = reqCount
    ? `${reqCount} 项要求 · ${statusSummary(options.rows, row => row.requirement.status)}`
    : "未保存要求";
  const kindRollup = options.isIndependent ? "独立事项" : "项目工作记录";
  const itemRollup = options.items.length > 1
    ? ` · ${options.items.length} 个事项 · ${statusSummary(options.items, item => item.status)}`
    : options.items.length === 1
      ? ` · 事项 ${statusLabel(options.items[0].status)}`
      : "";
  const captured = options.capturedAt && options.capturedAt !== "时间未提供"
    ? `<p class="work-card-preview-meta">快照时间：<time datetime="${options.capturedAt}">${options.capturedAt}</time></p>`
    : "";
  return [
    `<p class="work-card-preview-meta">${kindRollup} · ${requirementRollup}${itemRollup}</p>`,
    captured,
    renderCollapsedViewedState(options.viewedAt),
    renderCollapsedRequirementHighlights(options.rankedRows, options.items, options.changeFocus),
  ].join("");
}

function linkedWorkerHostSummary(facts: Record<string, any>[]): string {
  if (!facts.length) return "无工作器回执";
  return `${facts.length} 条工作器回执 · ${statusSummary(facts, fact => fact.host_lifecycle?.status ?? "unknown")}`;
}

interface RequirementRow {
  itemId: string;
  itemTitle: string;
  requirement: Record<string, any>;
  snapshot: Record<string, any>;
  order: number;
}

type ChangeFocus = { item_id: string; requirement_id: string; result_id: string };

function changeFocusMatches(row: RequirementRow, focus: ChangeFocus | null): boolean {
  const result = row.requirement.latest_result;
  const snapshot = row.snapshot;
  return !!focus && row.itemId === focus.item_id
    && row.requirement.requirement_id === focus.requirement_id
    && object(result) && result.session_id === snapshot.session_id
    && result.item_id === focus.item_id && result.requirement_id === focus.requirement_id
    && result.result_id === focus.result_id
    && (typeof snapshot.project_id !== "string"
      || (result.project_id === snapshot.project_id
        && row.requirement.project_id === snapshot.project_id));
}

function resolveChangeFocus(snapshot: Record<string, any>): ChangeFocus | null {
  const candidate = snapshot.change_focus;
  const keys = ["item_id", "requirement_id", "result_id"];
  if (!object(candidate) || !exactKeys(candidate, keys)
    || keys.some(key => typeof candidate[key] !== "string" || !candidate[key].trim())) return null;
  const focus = candidate as ChangeFocus;
  const requirementMatches = requirementRows(snapshot).filter(row => row.itemId === focus.item_id
    && row.requirement.requirement_id === focus.requirement_id);
  return requirementMatches.length === 1 && changeFocusMatches(requirementMatches[0], focus) ? focus : null;
}

function requirementRows(snapshot: Record<string, any>): RequirementRow[] {
  const items = snapshot.kind === "independent" ? [snapshot.card] : snapshot.cards;
  if (!Array.isArray(items)) return [];
  return items.flatMap((item: unknown) => {
    if (!object(item) || !Array.isArray(item.requirements)) return [];
    return item.requirements.filter(object).map((requirement: Record<string, any>, order: number) => ({
      itemId: typeof item.item_id === "string" ? item.item_id : "",
      itemTitle: String(item.title ?? ""), requirement, snapshot, order,
    }));
  });
}

function requirementPriority(row: RequirementRow): number {
  // Only rank facts present in the persisted snapshot. This schema has no
  // dedicated confirmation/change marker; never infer either from text.
  switch (row.requirement.status) {
    case "blocked": return 0;
    case "in_progress": return 1;
    case "pending": return 2;
    default: return 3;
  }
}

function renderSources(requirement: Record<string, any>): string {
  const sources: unknown[] = [];
  if (object(requirement.source_ref)) sources.push(requirement.source_ref);
  if (object(requirement.latest_result) && Array.isArray(requirement.latest_result.sources)) sources.push(...requirement.latest_result.sources);
  const unique = new Map<string, Record<string, any>>();
  for (const source of sources) {
    if (object(source) && typeof source.kind === "string" && typeof source.id === "string") unique.set(`${source.kind}\0${source.id}`, source);
  }
  if (!unique.size) return '<span class="work-card-source">来源未记录</span>';
  return `<span class="work-card-source-label">来源：</span>${[...unique.values()].map(source => `<span class="work-card-source">${escapeWorkCardHtml(source.kind)} · ${escapeWorkCardHtml(source.id)}</span>`).join(" ")}`;
}

const LIFECYCLE_KIND = "project_worker_host_lifecycle";
const LIFECYCLE_KEYS = ["kind", "result_status", "project_id", "task_id", "worker_id", "worker_run_id", "reservation_id", "parent_run_id", "parent_epoch", "host_status", "acceptance_status", "write_receipts_available", "write_receipts"];
const LIFECYCLE_READ_ATTEMPT_KEYS = ["read_attempts_available", "read_attempts"] as const;
const READ_ATTEMPT_ROW_KEYS = ["read_id", "path", "attempt", "result", "error_code"] as const;
const READ_ATTEMPT_RESULTS = new Set(["success", "retrying", "failed", "cancelled"]);
const READ_ATTEMPT_ERRORS = new Set([
  "", "sharing_violation", "file_lock_conflict", "lock_not_granted",
  "lock_violation", "read_retry_exhausted", "retry_cancel_event_unavailable",
  "non_retryable",
]);
const WINDOWS_RESERVED = /^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$/i;
const PRESTART_BLOCKED_LIFECYCLE_KEYS = ["kind", "result_status", "session_id", "item_id", "requirement_id", "project_id", "task_id", "worker_id", "worker_run_id", "reservation_id", "parent_run_id", "parent_epoch", "host_status", "host_error_code", "worker_execution_started", "worker_result_claimed", "acceptance_status"];
const PRESTART_BLOCKED_REASON_LABELS: Record<string, string> = {
  project_or_target_scope_invalid: "项目或目标范围未通过安全预检",
  project_root_identity_unavailable: "项目目录身份无法核验",
  worker_batch_limit_or_shape_invalid: "任务批次范围或格式不符合限制",
  parent_run_identity_mismatch: "当前回合身份无法核验",
  live_binding_required: "当前执行授权无法核验",
  live_binding_changed: "当前回合身份或授权状态已变化",
  worker_reservation_unavailable: "执行身份无法安全预留",
  worker_reservation_mismatch: "执行身份与任务不匹配",
  worker_reservation_unrecognized: "执行身份未能核验",
  worker_reservation_replayed: "执行身份已被使用，未重新执行",
  parent_session_or_project_binding_invalid: "当前会话或项目绑定无法核验",
  project_binding_unavailable: "项目绑定无法核验",
  project_binding_changed: "确认后项目绑定已变化",
  project_not_found: "所选项目未能核验",
  invalid_target_scope: "目标范围未通过安全检查",
  parent_write_scope_excludes_project: "当前授权范围不涵盖所选项目操作",
  current_turn_not_write_authorized: "当前回合未获该项目执行授权",
  scope_confirmation_failed: "项目范围确认未完成",
  scope_denied: "项目范围未获确认",
  separate_scope_outbound_and_write_confirmations_required: "所需的独立确认流程不可用",
  worker_run_start_record_failed: "执行开始状态未能安全记录",
  worker_dispatch_submit_failed: "执行器未能安全接收任务",
};
const RECEIPT_KEYS = ["path", "status", "verification", "characters_read_back", "error_code", "readback_succeeded"];
const HOST_STATUSES = new Set(["model_turn_finished", "blocked", "failed", "cancelled", "limit_reached"]);
const PRESTART_PARENT_RUN_ID = /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})$/i;

function exactKeys(value: Record<string, any>, keys: readonly string[] | string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function lifecycleHasExactKeys(payload: Record<string, any>): boolean {
  return exactKeys(payload, LIFECYCLE_KEYS)
    || exactKeys(payload, [...LIFECYCLE_KEYS, ...LIFECYCLE_READ_ATTEMPT_KEYS]);
}

function lifecycleProjectIdMatches(
  payload: Record<string, any>, result: Record<string, any>, snapshot: Record<string, any>,
): boolean {
  if (typeof payload.project_id !== "string" || !payload.project_id.trim()) return false;
  if (typeof snapshot.project_id === "string") {
    return payload.project_id === snapshot.project_id
      && (typeof result.project_id !== "string" || result.project_id === snapshot.project_id);
  }
  return typeof result.project_id !== "string" || payload.project_id === result.project_id;
}

function validParentRunId(value: unknown): boolean {
  if (typeof value !== "string" || !PRESTART_PARENT_RUN_ID.test(value)) return false;
  const hex = value.includes("-")
    ? value.replaceAll("-", "").toLowerCase()
    : value.toLowerCase();
  if (!/^[0-9a-f]{32}$/.test(hex)) return false;
  const canonical = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(canonical);
}

function workerReadPathSafe(path: string): boolean {
  if (!path || path.startsWith("/") || /^[A-Za-z]:/.test(path)) return false;
  const parts = path.split(/[\\/]/);
  if (parts.includes("..")) return false;
  return !parts.some(part => !part || part === "." || part === "host" || part.endsWith(" ")
    || /[\\:<">|?*~]/.test(part) || WINDOWS_RESERVED.test(part));
}

function validateWorkerReadAttempts(available: unknown, attempts: unknown): boolean {
  if (typeof available !== "boolean" || !Array.isArray(attempts) || attempts.length > 256) return false;
  if (!available && attempts.length > 0) return false;
  const lastByRead = new Map<number, [string, number, string]>();
  for (const row of attempts) {
    if (!object(row) || !exactKeys(row, READ_ATTEMPT_ROW_KEYS)) return false;
    const { read_id: readId, path, attempt, result, error_code: errorCode } = row;
    if (!Number.isSafeInteger(readId) || readId < 1 || readId > 64
      || typeof path !== "string" || !workerReadPathSafe(path)
      || !Number.isSafeInteger(attempt) || attempt < 1 || attempt > 6
      || typeof result !== "string" || !READ_ATTEMPT_RESULTS.has(result)
      || typeof errorCode !== "string" || !READ_ATTEMPT_ERRORS.has(errorCode)) {
      return false;
    }
    const previous = lastByRead.get(readId);
    if (previous === undefined) {
      if (attempt !== 1) return false;
    } else if (previous[0] !== path || !(
      attempt === previous[1] + 1 && previous[2] === "retrying"
      || attempt === previous[1] && previous[2] === "retrying" && result === "cancelled"
    )) {
      return false;
    }
    lastByRead.set(readId, [path, attempt, result]);
  }
  return true;
}

const READ_ATTEMPT_RESULT_LABELS: Record<string, string> = {
  success: "读取成功",
  retrying: "读取重试中",
  failed: "读取失败",
  cancelled: "读取已取消",
};

function lifecycleSummary(summary: unknown, result: Record<string, any>, snapshot: Record<string, any>): { kind: "not-lifecycle" | "invalid" | "valid"; html?: string } {
  if (typeof summary !== "string") return { kind: "not-lifecycle" };
  let payload: unknown;
  try { payload = JSON.parse(summary); }
  catch { return summary.includes(LIFECYCLE_KIND) ? { kind: "invalid" } : { kind: "not-lifecycle" }; }
  if (!object(payload) || payload.kind !== LIFECYCLE_KIND) return { kind: "not-lifecycle" };
  if (exactKeys(payload, PRESTART_BLOCKED_LIFECYCLE_KEYS)) {
    const valid = payload.result_status === "blocked" && result.status === "blocked"
      && result.verification_status === "not_verified"
      && payload.session_id === snapshot.session_id && payload.session_id === result.session_id
      && payload.item_id === result.item_id && payload.requirement_id === result.requirement_id
      && lifecycleProjectIdMatches(payload, result, snapshot)
      && typeof payload.task_id === "string" && /^task-[0-9a-f]{64}$/.test(payload.task_id)
      && typeof payload.worker_id === "string" && !!payload.worker_id
      && new TextEncoder().encode(payload.worker_id).length <= 128
      && payload.worker_run_id === result.run_id
      && typeof payload.reservation_id === "string" && !!payload.reservation_id
      && new TextEncoder().encode(payload.reservation_id).length <= 128
      && typeof payload.parent_run_id === "string" && PRESTART_PARENT_RUN_ID.test(payload.parent_run_id)
      && Number.isSafeInteger(payload.parent_epoch) && payload.parent_epoch >= 1
      && payload.host_status === "blocked"
      && typeof payload.host_error_code === "string"
      && new TextEncoder().encode(payload.host_error_code).length <= 128
      && payload.worker_execution_started === false
      && payload.worker_result_claimed === false
      && payload.acceptance_status === "not_run";
    if (!valid) return { kind: "invalid" };
    const reason = Object.hasOwn(PRESTART_BLOCKED_REASON_LABELS, payload.host_error_code)
      ? PRESTART_BLOCKED_REASON_LABELS[payload.host_error_code]
      : "具体受阻原因无法安全识别";
    return { kind: "valid", html: `<p class="work-card-result"><strong>工作器尚未启动</strong> · 执行前检查受阻：${escapeWorkCardHtml(reason)}。验收未运行；不代表工作已开始、完成或通过验收。</p>` };
  }
  const receipts = payload.write_receipts;
  const readEvidence = lifecycleHasExactKeys(payload) && exactKeys(payload, [...LIFECYCLE_KEYS, ...LIFECYCLE_READ_ATTEMPT_KEYS]);
  const valid = lifecycleHasExactKeys(payload)
    && payload.result_status === "unknown" && result.status === "unknown"
    && result.verification_status === "not_verified"
    && lifecycleProjectIdMatches(payload, result, snapshot)
    && typeof payload.task_id === "string" && /^task-[0-9a-f]{64}$/.test(payload.task_id)
    && typeof payload.worker_id === "string" && !!payload.worker_id
    && payload.worker_run_id === result.run_id
    && typeof payload.reservation_id === "string" && !!payload.reservation_id
    && validParentRunId(payload.parent_run_id)
    && Number.isSafeInteger(payload.parent_epoch) && payload.parent_epoch >= 1
    && HOST_STATUSES.has(payload.host_status)
    && payload.acceptance_status === "not_run"
    && typeof payload.write_receipts_available === "boolean"
    && Array.isArray(receipts) && receipts.length <= 32
    && (receipts.length === 0 || payload.write_receipts_available)
    && (!readEvidence || validateWorkerReadAttempts(payload.read_attempts_available, payload.read_attempts))
    && receipts.every((receipt: unknown) => object(receipt) && exactKeys(receipt, RECEIPT_KEYS)
      && typeof receipt.path === "string" && !!receipt.path && receipt.path.length <= 512
      && !receipt.path.startsWith("/") && !/^[A-Za-z]:/.test(receipt.path)
      && !receipt.path.split(/[\\/]/).includes("..")
      && typeof receipt.status === "string" && !!receipt.status
      && typeof receipt.verification === "string" && !!receipt.verification
      && Number.isSafeInteger(receipt.characters_read_back) && receipt.characters_read_back >= 0
      && typeof receipt.error_code === "string" && receipt.error_code.length <= 128
      && typeof receipt.readback_succeeded === "boolean");
  if (!valid) return { kind: "invalid" };

  const hostLabel: Record<string, string> = {
    model_turn_finished: "工作器主机回合已结束",
    blocked: "工作器主机报告受阻",
    failed: "工作器主机报告执行失败",
    cancelled: "工作器主机回合已取消",
    limit_reached: "工作器主机报告达到运行限制",
  };
  const receiptHtml = receipts.length
    ? `<ul class="work-card-host-receipts">${receipts.map((receipt: Record<string, any>) => `<li><code>${escapeWorkCardHtml(receipt.path)}</code> · 回执状态：${escapeWorkCardHtml(receipt.status)} · 验证记录：${escapeWorkCardHtml(receipt.verification)} · ${receipt.readback_succeeded ? `已读回 ${receipt.characters_read_back} 字符` : "未成功读回"}${receipt.error_code ? ` · 错误码：${escapeWorkCardHtml(receipt.error_code)}` : ""}</li>`).join("")}</ul>`
    : '<p>没有可展示的写入回执。</p>';
  const readHtml = readEvidence && payload.read_attempts_available && Array.isArray(payload.read_attempts) && payload.read_attempts.length
    ? `<ul class="work-card-host-read-attempts">${payload.read_attempts.map((attempt: Record<string, any>) => `<li><code>${escapeWorkCardHtml(attempt.path)}</code> · ${escapeWorkCardHtml(READ_ATTEMPT_RESULT_LABELS[attempt.result] ?? "读取状态未记录")}${attempt.error_code ? ` · 错误码：${escapeWorkCardHtml(attempt.error_code)}` : ""}</li>`).join("")}</ul>`
    : "";
  return { kind: "valid", html: `<p class="work-card-result"><strong>${hostLabel[payload.host_status]}</strong> · 验收未运行；不代表工作已完成或通过验收。</p>${receiptHtml}${readHtml}` };
}

function renderProjectTestSelfReport(result: Record<string, any>, isCurrent: boolean): string {
  if (!Object.hasOwn(result, "project_test") || result.project_test === null) return "";
  const report = result.project_test;
  if (!isCurrent) return "";
  const valid = object(report) && exactKeys(report, ["status", "source", "pytest_target", "exit_code"])
    && report.status === "passed" && report.source === "user_authorized_project_self_report"
    && typeof report.pytest_target === "string" && report.pytest_target.length <= 512
    && /^[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_]+$/.test(report.pytest_target)
    && !report.pytest_target.split(/[\\/]/).includes("..")
    && report.exit_code === 0
    && result.status === "unknown" && result.verification_status === "not_verified";
  if (!valid) return '<p class="work-card-result">项目自报告测试记录无法核对，未显示为通过；要求仍未验证。</p>';
  return '<p class="work-card-result work-card-project-self-report"><strong>项目自报通过，非整体验收</strong> · 测试目标：<code>'
    + escapeWorkCardHtml(report.pytest_target) + '</code></p>';
}

function renderLatestResult(result: Record<string, any>, snapshot: Record<string, any>, isCurrent: boolean, includeSummary = true): string {
  const lifecycle = lifecycleSummary(result.summary, result, snapshot);
  if (lifecycle.kind === "invalid") return '<p class="work-card-result">机器回执未通过校验，已隐藏内部字段。</p>';
  const selfReport = renderProjectTestSelfReport(result, isCurrent);
  if (lifecycle.kind === "valid") return lifecycle.html! + selfReport;
  if (includeSummary) return `<p class="work-card-result"><strong>最近结果：</strong>${escapeWorkCardHtml(result.summary || "摘要未提供")} · ${escapeWorkCardHtml(statusLabel(result.status))}${result.verification_status ? ` · 验证：${escapeWorkCardHtml(result.verification_status)}` : " · 验证信息未提供"}</p>` + selfReport;
  const qualifiers = [];
  if (result.status && result.status !== "unknown") qualifiers.push(`记录状态：${statusLabel(result.status)}`);
  if (result.verification_status === "passed") qualifiers.push("验证已通过");
  else if (result.verification_status === "failed") qualifiers.push("验证未通过");
  else if (result.verification_status === "not_verified") qualifiers.push("尚未验证");
  return `<p class="work-card-result">${qualifiers.join(" · ") || "没有其他验证信息记录。"}</p>` + selfReport;
}

function renderCardViewedState(card: Record<string, any>): string {
  if (!Object.hasOwn(card, "viewed_at")) return "";
  const viewed = card.viewed_at;
  if (viewed === null) return "";
  if (typeof viewed === "string" && viewed.trim()) {
    const at = escapeWorkCardHtml(viewed.trim());
    return `<p class="work-card-viewed-state is-viewed">已查看详情 · <time datetime="${at}">${at}</time> · 不代表认可或验收</p>`;
  }
  return "";
}

function renderRequirement(row: RequirementRow, full = false, changeFocus: ChangeFocus | null = null): string {
  const requirement = row.requirement;
  const title = row.itemTitle ? `<span class="work-card-item-title">${escapeWorkCardHtml(row.itemTitle)}</span>` : "";
  const focusLabel = changeFocusMatches(row, changeFocus) ? '<span class="work-card-change-focus">本次更新</span>' : "";
  const summary = object(requirement.latest_result) && typeof requirement.latest_result.summary === "string" ? requirement.latest_result.summary.trim() : "";
  const result = object(requirement.latest_result) ? renderLatestResult(requirement.latest_result, row.snapshot, requirement.latest_result_is_current === true, !summary) : '<p class="work-card-result">暂未附结果记录。</p>';
  const freshness = requirement.latest_result_is_current === false || requirement.current_result_missing === true ? '<p class="work-card-stale-note">这份历史记录无法确认是否为最新结果。</p>' : "";
  const latest = object(requirement.latest_result) ? requirement.latest_result : null;
  const resultAttrs = latest && typeof latest.result_id === "string" && latest.result_id && typeof requirement.requirement_id === "string" ? ` data-work-card-result-detail data-result-id="${escapeWorkCardHtml(latest.result_id)}" data-requirement-id="${escapeWorkCardHtml(requirement.requirement_id)}"` : "";
  const detail = latest ? `<details class="work-card-requirement-detail"${resultAttrs}><summary>查看结果与验证说明</summary>${result}${freshness}</details>` : freshness;
  const resultSummary = summary && !summary.includes(LIFECYCLE_KIND) ? `<p class="work-card-result-summary">${escapeWorkCardHtml(summary)}</p>` : "";
  return `<li class="work-card-requirement${full ? " work-card-requirement-all" : ""}"><div class="work-card-requirement-heading">${title}${focusLabel}</div><p class="work-card-requirement-text">${escapeWorkCardHtml(requirement.text)}</p>${resultSummary}${detail}</li>`;
}

function renderRequirementTechnicalDetails(rows: RequirementRow[]): string {
  const content = rows.map(row => {
    const requirement = row.requirement;
    const ids = [row.itemId ? `事项 ID：${escapeWorkCardHtml(row.itemId)}` : "", typeof requirement.requirement_id === "string" ? `要求 ID：${escapeWorkCardHtml(requirement.requirement_id)}` : ""].filter(Boolean).join(" · ");
    const status = `<span class="work-card-status">捕获时：${escapeWorkCardHtml(statusLabel(requirement.status))}</span>`;
    const latest = object(requirement.latest_result) ? requirement.latest_result : null;
    const resultIdentity = latest ? `<div>结果 ID：${escapeWorkCardHtml(latest.result_id ?? "未提供")} · 回合 ID：${escapeWorkCardHtml(latest.run_id ?? "未提供")}</div><div>结果状态：${escapeWorkCardHtml(statusLabel(latest.status))} · 验证：${escapeWorkCardHtml(latest.verification_status ?? "未提供")}</div>` : "";
    return `<li>${row.itemTitle ? `${escapeWorkCardHtml(row.itemTitle)} · ` : ""}${ids}<div>${status}</div>${resultIdentity}<div class="work-card-sources">${renderSources(requirement)}</div></li>`;
  }).join("");
  return `<details class="work-card-technical-detail"><summary>技术与来源信息</summary><ol>${content}</ol></details>`;
}

export function renderWorkCardSnapshot(card: unknown, presentationIdentity?: { sessionId: string; presentationId: string; cardId: string }): string {
  if (!object(card) || !object(card.snapshot)) return '<section class="work-card-snapshot"><p>工作卡片详情不可用</p></section>';
  const snapshot = card.snapshot;
  if (snapshot.kind === "linked_project_session_copy") {
    const expectedSession = presentationIdentity?.sessionId ?? snapshot.session_id;
    if (!validLinkedProjectSessionCopySnapshot(snapshot, expectedSession)
      || (presentationIdentity && card.card_id !== presentationIdentity.cardId)) {
      return '<section class="work-card-snapshot"><p>工作卡片详情不可用</p></section>';
    }
    return renderLinkedProjectSessionCopySnapshot(snapshot, presentationIdentity);
  }
  if (snapshot.kind === "linked_item_copy") {
    const expectedSession = presentationIdentity?.sessionId ?? snapshot.session_id;
    if (!validLinkedItemCopySnapshot(snapshot, expectedSession)
      || (presentationIdentity && card.card_id !== presentationIdentity.cardId)) {
      return '<section class="work-card-snapshot"><p>工作卡片详情不可用</p></section>';
    }
    return renderLinkedItemCopySnapshot(snapshot, presentationIdentity);
  }
  const isIndependent = snapshot.kind === "independent";
  const items = isIndependent ? [snapshot.card] : snapshot.cards;
  if (!Array.isArray(items)) return '<section class="work-card-snapshot"><p>工作卡片详情不可用</p></section>';
  const rows = requirementRows(snapshot);
  const changeFocus = resolveChangeFocus(snapshot);
  const ranked = rows.map((row, index) => ({
    ...row, isChangeFocus: changeFocusMatches(row, changeFocus), originalIndex: index,
  })).sort((a, b) => Number(b.isChangeFocus) - Number(a.isChangeFocus)
    || requirementPriority(a) - requirementPriority(b) || a.originalIndex - b.originalIndex);
  const projectScoped = snapshot.kind === "project_scoped";
  const defaultRows = ranked.slice(0, WORK_CARD_DEFAULT_VISIBLE_REQUIREMENTS);
  const title = isIndependent
    ? String(items[0]?.title ?? "独立事项")
    : String(items[0]?.title ?? snapshot.project_id ?? "项目快照");
  const identity = isIndependent ? "" : `<p class="work-card-project-id">项目：${escapeWorkCardHtml(snapshot.project_id ?? "标识未提供")}</p>`;
  const itemSources = [...new Map(items.flatMap((item: any) => Array.isArray(item?.source_refs) ? item.source_refs : []).filter(object).filter((source: any) => typeof source.kind === "string" && typeof source.id === "string").map((source: any) => [`${source.kind}\\0${source.id}`, source])).values()];
  const sourceSummary = itemSources.length ? `<p class="work-card-project-sources">项目来源：${itemSources.map((source: any) => `${escapeWorkCardHtml(source.kind)} · ${escapeWorkCardHtml(source.id)}`).join("；")}</p>` : "";
  const capturedAt = escapeWorkCardHtml(snapshot.captured_at ?? "时间未提供");
  const allRows = rows.map(row => renderRequirement(row, true, changeFocus)).join("");
  const requirements = defaultRows.map(row => renderRequirement(row, false, changeFocus)).join("");
  const identityAttrs = presentationIdentity
    ? ` data-session-id="${escapeWorkCardHtml(presentationIdentity.sessionId)}" data-presentation-id="${escapeWorkCardHtml(presentationIdentity.presentationId)}" data-card-id="${escapeWorkCardHtml(presentationIdentity.cardId)}"`
    : "";
  const all = rows.length > WORK_CARD_DEFAULT_VISIBLE_REQUIREMENTS
    ? `<details class="work-card-all"><summary>查看全部 ${rows.length} 项要求</summary><ol class="work-card-requirements work-card-all-list">${allRows}</ol></details>` : "";
  const kindAttr = ` data-snapshot-kind="${escapeWorkCardHtml(snapshot.kind ?? (isIndependent ? "independent" : "project"))}"`;
  const viewedState = renderCardViewedState(card);
  const temporalGuard = '<p class="work-card-temporal-guard" role="note">以下为保存时的历史记录，不会自动刷新当前状态。</p>';
  const requirementsLabel = rows.length ? `查看 ${rows.length} 项要求与结果` : "查看工作记录";
  const preview = renderOrdinarySnapshotCollapsedPreview({
    rows, rankedRows: ranked, items: items.filter(object), isIndependent, capturedAt,
    changeFocus, viewedAt: card.viewed_at,
  });
  const itemStates = items.filter(object).map((item: Record<string, any>) => `<li class="work-card-item-state"><strong>${escapeWorkCardHtml(item.title ?? "事项标题未提供")}</strong><span>${escapeWorkCardHtml(statusLabel(item.status))}</span></li>`).join("");
  const itemTechnical = `<details class="work-card-technical-detail"><summary>事项状态与内部字段</summary><ul class="work-card-item-states">${itemStates}</ul><p>项目：${escapeWorkCardHtml(snapshot.project_id ?? "未提供")}</p>${identity}${sourceSummary}</details>`;
  const completenessNote = projectScoped && snapshot.history_complete === false ? '<p class="work-card-stale-note">部分历史结果未纳入；此卡仅展示已保存内容。</p>' : "";
  const detailBody = `<div class="work-card-snapshot-detail-content">${temporalGuard}${completenessNote}<section class="work-card-items"><ol class="work-card-requirements">${requirements || '<li class="work-card-empty">这张卡没有保存要求。</li>'}</ol>${all}${renderRequirementTechnicalDetails(rows)}${itemTechnical}<details class="work-card-technical-detail"><summary>保存时间与已读记录</summary><p class="work-card-captured">快照时间：<time datetime="${capturedAt}">${capturedAt}</time></p>${viewedState}</details></section></div>`;
  return `<article class="work-card-snapshot ${isIndependent ? "is-independent" : "is-project"}" data-work-card-snapshot${kindAttr}${identityAttrs}><header class="work-card-header"><p class="work-card-snapshot-label">历史快照 · 非实时</p><h3>${escapeWorkCardHtml(title)}</h3>${preview}</header><details class="work-card-snapshot-detail"><summary>${requirementsLabel}</summary>${detailBody}</details></article>`;
}

function renderLinkedProjectSessionCopySnapshot(
  snapshot: Record<string, any>,
  presentationIdentity?: { sessionId: string; presentationId: string; cardId: string },
): string {
  const identityAttrs = presentationIdentity ? ` data-session-id="${escapeWorkCardHtml(presentationIdentity.sessionId)}" data-presentation-id="${escapeWorkCardHtml(presentationIdentity.presentationId)}" data-card-id="${escapeWorkCardHtml(presentationIdentity.cardId)}"` : "";
  const source = snapshot.source;
  const requirements = (source.items as Record<string, any>[]).flatMap(item => (item.requirements as Record<string, any>[]).map(requirement => ({ item, requirement })));
  const factsByRequirement = new Map<string, Record<string, any>[]>();
  for (const fact of source.worker_facts as Record<string, any>[]) {
    const key = `${fact.item_id}\0${fact.requirement_id}`;
    factsByRequirement.set(key, [...(factsByRequirement.get(key) ?? []), fact]);
  }
  const rows = requirements.map(({item, requirement}) => {
    const resultFacts = factsByRequirement.get(`${item.item_id}\0${requirement.requirement_id}`) ?? [];
    return `<li class="work-card-requirement"><div class="work-card-requirement-heading"><span class="work-card-item-title">${escapeWorkCardHtml(item.title)}</span></div><p class="work-card-requirement-text">${escapeWorkCardHtml(requirement.text)}</p></li>`;
  }).join("");
  const technicalRows = requirements.map(({item, requirement}) => `<li>${escapeWorkCardHtml(item.title)} · 事项 ID：${escapeWorkCardHtml(item.item_id)} · 要求 ID：${escapeWorkCardHtml(requirement.requirement_id)}<div>捕获时：${escapeWorkCardHtml(statusLabel(requirement.status))}</div></li>`).join("");
  const workerTechnical = (source.worker_facts as Record<string, any>[]).map(fact => `<li>工作器 ID：${escapeWorkCardHtml(fact.worker_id)} · 回合 ID：${escapeWorkCardHtml(fact.worker_run_id)}<div>宿主状态：${escapeWorkCardHtml(fact.host_lifecycle.status)} · 结束原因：${escapeWorkCardHtml(fact.host_lifecycle.finish_reason)} · 操作结果：${escapeWorkCardHtml(fact.operation_result)} · 验证：${escapeWorkCardHtml(fact.verification)}</div><div>绑定 ID：${escapeWorkCardHtml(fact.binding_id)} · 保留 ID：${escapeWorkCardHtml(fact.host_start.reservation_id)} · 序号：${escapeWorkCardHtml(fact.host_lifecycle.sequence)}</div></li>`).join("");
  const hasBlockedReceipt = (source.worker_facts as Record<string, any>[]).some(fact => fact.host_lifecycle.status === "blocked");
  const resultNote = source.worker_facts.length
    ? hasBlockedReceipt ? "部分工作在执行前受阻；其余回合结束也不能确认操作结果或验证结论。" : "工作器回合结束，但副本没有可确认的操作结果或验证结论。"
    : "此副本没有附带操作结果记录。";
  const viewLabel = requirements.length ? `查看 ${requirements.length} 项要求与结果` : "查看来源记录";
  const capturedAt = escapeWorkCardHtml(snapshot.captured_at);
  const sourceRevision = escapeWorkCardHtml(source.source_revision);
  const requirementRollup = requirements.length
    ? `${requirements.length} 项要求 · ${statusSummary(requirements.map(entry => entry.requirement), requirement => requirement.status)}`
    : "未保存要求";
  const preview = `<p class="work-card-preview-meta">${requirementRollup}</p><p class="work-card-preview-state"><strong>结果：</strong>${escapeWorkCardHtml(resultNote)}</p><p class="work-card-preview-meta">${escapeWorkCardHtml(linkedWorkerHostSummary(source.worker_facts as Record<string, any>[]))}</p><p class="work-card-preview-meta">确认时间：<time datetime="${capturedAt}">${capturedAt}</time></p>`;
  const detailBody = `<section class="work-card-items"><p class="work-card-temporal-guard" role="note">以下内容来自指定来源会话的历史副本，不代表项目全貌或当前状态。</p><ol class="work-card-requirements">${rows || '<li class="work-card-empty">此副本没有保存要求。</li>'}</ol><p class="work-card-result-summary">${resultNote}</p><details class="work-card-technical-detail"><summary>技术与来源信息</summary><p>来源项目：${escapeWorkCardHtml(source.project_id)} · 来源 Session：${escapeWorkCardHtml(source.source_session_id)} · 来源修订：${sourceRevision}</p><p class="work-card-captured">确认时间：<time datetime="${capturedAt}">${capturedAt}</time></p><p>来源范围：${escapeWorkCardHtml(source.scope)} · 映射：${escapeWorkCardHtml(source.mapping_claim)}</p><ol>${technicalRows}</ol><h4>工作器回执（技术字段）</h4><ol>${workerTechnical}</ol><p>工作器回执仅用于说明执行边界；不构成操作结果或验收证明。查看来源当前状态需重新授权。</p></details></section>`;
  return `<article class="work-card-snapshot is-linked-project-session-copy" data-work-card-snapshot data-snapshot-kind="linked_project_session_copy"${identityAttrs}><header class="work-card-header"><p class="work-card-snapshot-label">历史副本 · 非实时</p><p class="work-card-scope-label">来自指定会话 · 不代表项目全貌</p><h3>项目工作记录</h3>${preview}</header><details data-work-card-snapshot-detail><summary>${viewLabel}</summary>${detailBody}</details></article>`;
}

function renderLinkedItemCopySnapshot(
  snapshot: Record<string, any>,
  presentationIdentity?: { sessionId: string; presentationId: string; cardId: string },
): string {
  const identityAttrs = presentationIdentity ? ` data-session-id="${escapeWorkCardHtml(presentationIdentity.sessionId)}" data-presentation-id="${escapeWorkCardHtml(presentationIdentity.presentationId)}" data-card-id="${escapeWorkCardHtml(presentationIdentity.cardId)}"` : "";
  const capturedAt = escapeWorkCardHtml(snapshot.captured_at);
  const collapsedResult = snapshot.host_lifecycle.status === "blocked"
    ? "执行前遇到阻碍；没有确认操作结果。"
    : "此副本没有可确认的操作结果或验证结论。";
  const preview = `<p class="work-card-preview-meta">${escapeWorkCardHtml(previewTextSnippet(snapshot.requirement_text))}</p><p class="work-card-preview-state"><strong>结果：</strong>${escapeWorkCardHtml(collapsedResult)}</p><p class="work-card-preview-meta">捕获时 · 事项 ${escapeWorkCardHtml(statusLabel(snapshot.item_status_at_capture))} · 要求 ${escapeWorkCardHtml(statusLabel(snapshot.requirement_status_at_capture))}</p><p class="work-card-preview-meta">副本时间：<time datetime="${capturedAt}">${capturedAt}</time></p>`;
  const detailBody = `<section class="work-card-items"><p class="work-card-temporal-guard" role="note">以下是单项要求的历史副本，不代表项目全貌或当前状态。</p><ol class="work-card-requirements"><li class="work-card-requirement"><div class="work-card-requirement-heading"><span class="work-card-item-title">${escapeWorkCardHtml(snapshot.item_title)}</span></div><p class="work-card-requirement-text">${escapeWorkCardHtml(snapshot.requirement_text)}</p><p class="work-card-result-summary">${collapsedResult}</p></li></ol><details class="work-card-technical-detail"><summary>技术与来源信息</summary><p>来源项目：${escapeWorkCardHtml(snapshot.project_id)} · 来源 Session：${escapeWorkCardHtml(snapshot.source_session_id)}</p><p>事项：${escapeWorkCardHtml(snapshot.item_id)} · 要求：${escapeWorkCardHtml(snapshot.requirement_id)}</p><p class="work-card-captured">副本确认时间：<time datetime="${capturedAt}">${capturedAt}</time></p><p>捕获时事项状态：${escapeWorkCardHtml(statusLabel(snapshot.item_status_at_capture))}</p><p>捕获时要求状态：${escapeWorkCardHtml(statusLabel(snapshot.requirement_status_at_capture))}</p><p>工作器主机回合：${escapeWorkCardHtml(snapshot.host_lifecycle.status)} · 结束原因：${escapeWorkCardHtml(snapshot.host_lifecycle.finish_reason)}</p><p>仅保留获准复制的字段；查询来源当前状态需重新授权。</p></details></section>`;
  return `<article class="work-card-snapshot is-linked-item-copy" data-work-card-snapshot data-snapshot-kind="linked_item_copy"${identityAttrs}><header class="work-card-header"><p class="work-card-snapshot-label">历史副本 · 非实时</p><p class="work-card-scope-label">一项要求 · 不是项目全貌</p><h3>${escapeWorkCardHtml(snapshot.item_title)}</h3>${preview}</header><details data-work-card-snapshot-detail><summary>查看要求与结果</summary>${detailBody}</details></article>`;
}

export function renderWorkCardEntry(entry: WorkCardPresentationEntry): string {
  if (entry.status === "loading") return `<section class="work-card-unavailable" data-presentation-id="${escapeWorkCardHtml(entry.presentationId)}">正在读取历史工作卡片…</section>`;
  if (entry.status === "missing" || !entry.card) {
    const message = entry.status === "missing" ? "历史工作卡片未找到，原消息引用仍保留。" : `历史工作卡片读取失败：${entry.text || "暂不可用"}`;
    return `<section class="work-card-unavailable" data-presentation-id="${escapeWorkCardHtml(entry.presentationId)}">${escapeWorkCardHtml(message)}</section>`;
  }
  return renderWorkCardSnapshot(entry.card, { sessionId: entry.sessionId, presentationId: entry.presentationId, cardId: entry.cardId });
}

export function buildWorkCardResultViewRequest(
  entry: WorkCardPresentationEntry, resultId: string, requirementId: string, currentSessionId: string,
): Record<string, string> | null {
  if (!entry || entry.sessionId !== currentSessionId || entry.status !== "resolved" || !entry.card
      || typeof resultId !== "string" || !resultId || typeof requirementId !== "string" || !requirementId) return null;
  const snapshot = entry.card.snapshot;
  if (!object(snapshot) || snapshot.kind === "linked_item_copy"
    || snapshot.kind === "linked_project_session_copy") return null;
  const items = snapshot.kind === "independent" ? [snapshot.card] : snapshot.cards;
  if (!Array.isArray(items)) return null;
  const matches = items.flatMap((item: unknown) => object(item) && Array.isArray(item.requirements) ? item.requirements : [])
    .filter(object).filter((requirement: Record<string, any>) => requirement.requirement_id === requirementId
      && object(requirement.latest_result) && requirement.latest_result.result_id === resultId);
  if (matches.length !== 1) return null;
  return { type: "work-card.result.view", session_id: currentSessionId, presentation_id: entry.presentationId,
    card_id: entry.cardId, result_id: resultId, requirement_id: requirementId };
}









export function isExplicitResultDetailExpansion(clickTrusted: boolean, clickDefaultPrevented: boolean, wasOpen: boolean, isOpen: boolean): boolean {
  return clickTrusted && !clickDefaultPrevented && wasOpen === false && isOpen === true;
}


/** A response belongs only to the original explicit expansion, never a later reopening. */
export class WorkCardResultExcerptView {
  private sessionId = "";
  private shown = new Set<HTMLDetailsElement>();
  private pending = new Map<string, { detail: HTMLDetailsElement; entry: WorkCardPresentationEntry; request: Record<string, string> }>();
  opened(detail: HTMLDetailsElement, entry: WorkCardPresentationEntry, request: Record<string, string>): void {
    if (!request.request_id) return;
    this.pending.set(request.request_id, { detail, entry, request: { ...request } });
  }
  closed(detail: HTMLDetailsElement): string[] {
    const cancelled: string[] = [];
    for (const [key, value] of this.pending) if (value.detail === detail) { this.pending.delete(key); cancelled.push(key); }
    detail.querySelector('[data-local-reply-excerpt]')?.remove();
    this.shown.delete(detail);
    return cancelled;
  }
  setSession(sessionId: string): void { if (this.sessionId !== sessionId) { this.clear(); this.sessionId = sessionId; } }
  clear(): void { for (const detail of this.shown) this.closed(detail); for (const { detail } of this.pending.values()) this.closed(detail); this.pending.clear(); }
  accept(event: import("../../api/ws").WorkCardResultViewEvent, sessionId: string, entry: WorkCardPresentationEntry | undefined): boolean {
    if (!event.request_id) return false;
    const pending = this.pending.get(event.request_id);
    if (!pending || ["session_id", "presentation_id", "card_id", "requirement_id", "result_id"].some(key => pending.request[key] !== (event as unknown as Record<string, unknown>)[key])) return false;
    this.pending.delete(event.request_id);
    const detail = event.local_assistant_reply_excerpt_detail, node = pending.detail;
    const article = node.closest<HTMLElement>("article[data-work-card-snapshot]");
    if (!article || article.dataset.sessionId !== sessionId || article.dataset.presentationId !== event.presentation_id || article.dataset.cardId !== event.card_id) return false;
    if (!detail || (event.status !== "acknowledged" && event.status !== "already_acknowledged")
      || event.session_id !== sessionId || !entry || entry !== pending.entry || entry.sessionId !== sessionId
      || entry.presentationId !== event.presentation_id || entry.cardId !== event.card_id
      || !node.isConnected || !node.open || node.dataset.resultId !== event.result_id
      || node.dataset.requirementId !== event.requirement_id || !entry.card
      || detail.session_id !== sessionId || detail.requirement_id !== event.requirement_id || detail.result_id !== event.result_id
      || detail.result_status !== "unknown" || detail.verification_status !== "not_verified"
      || (detail.source_availability !== "available" && detail.source_availability !== "unavailable")
      || typeof detail.label !== "string" || !detail.label || typeof detail.escaped_excerpt_text !== "string"
      || !detail.escaped_excerpt_text || /[<>"']|&(?!amp;|lt;|gt;|quot;|#x27;)/.test(detail.escaped_excerpt_text)) return false;
    const snapshot = entry.card.snapshot;
    const items = snapshot?.kind === "independent" ? [snapshot.card] : snapshot?.cards;
    if (!Array.isArray(items) || !buildWorkCardResultViewRequest(entry, event.result_id, event.requirement_id, sessionId)) return false;
    const matches = items.filter(item => object(item) && item.item_id === detail.item_id && Array.isArray(item.requirements)
      && item.requirements.some((r: unknown) => object(r) && r.requirement_id === event.requirement_id
        && object(r.latest_result) && r.latest_result.result_id === event.result_id));
    if (matches.length !== 1) return false;
    const decoded = detail.escaped_excerpt_text.replace(/&(amp|lt|gt|quot|#x27);/g, (_, entity: string) =>
      ({amp: "&", lt: "<", gt: ">", quot: '"', "#x27": "'"} as Record<string, string>)[entity]);
    const section = node.ownerDocument.createElement("section"); section.setAttribute("data-local-reply-excerpt", "");
    const label = node.ownerDocument.createElement("p"); label.textContent = detail.label + " · 操作结果未知；未核验";
    const text = node.ownerDocument.createElement("pre"); text.textContent = decoded;
    text.style.whiteSpace = "pre-wrap"; text.style.overflowWrap = "anywhere";
    section.append(label, text); node.append(section); this.shown.add(node); return true;
  }
}

export function bindWorkCardResultViewEvents(
  container: HTMLElement,
  options: {
    currentSessionId: () => string;
    getEntry: (presentationId: string) => WorkCardPresentationEntry | undefined;
    send: (request: Record<string, string>) => void;
    onError?: (error: unknown) => void;
    onExpanded?: (detail: HTMLDetailsElement, entry: WorkCardPresentationEntry, request: Record<string, string>) => void;
    onClosed?: (detail: HTMLDetailsElement) => void;
  },
): () => void {
  const clickState = new WeakMap<HTMLDetailsElement, boolean>();
  const pendingToggle = new WeakMap<HTMLDetailsElement, boolean>();
  const detailsFor = (event: Event): HTMLDetailsElement | null => {
    if (!(event.target instanceof Element)) return null;
    const summary = event.target.closest("summary");
    return summary?.closest<HTMLDetailsElement>("details[data-work-card-result-detail]") ?? null;
  };
  const captureClick = (event: Event): void => {
    if (!event.isTrusted) return;
    const details = detailsFor(event);
    if (details) {
      if (details.open) options.onClosed?.(details);
      clickState.set(details, details.open);
    }
  };
  const completeClick = (event: Event): void => {
    if (!event.isTrusted) return;
    const details = detailsFor(event);
    if (!details) return;
    const wasOpen = clickState.get(details);
    clickState.delete(details);
    if (wasOpen !== undefined && !event.defaultPrevented) pendingToggle.set(details, wasOpen);
  };
  const toggle = (event: Event): void => {
    const details = event.target;
    if (!(details instanceof HTMLDetailsElement) || !details.hasAttribute("data-work-card-result-detail")) return;
    if (!details.open) options.onClosed?.(details);
    const wasOpen = pendingToggle.get(details);
    pendingToggle.delete(details);
    if (wasOpen === undefined || !isExplicitResultDetailExpansion(true, false, wasOpen, details.open)) return;
    const article = details.closest<HTMLElement>("article[data-work-card-snapshot]");
    if (!article) return;
    const entry = options.getEntry(article.dataset.presentationId ?? "");
    if (!entry || entry.sessionId !== options.currentSessionId() || entry.cardId !== article.dataset.cardId) return;
    const request = buildWorkCardResultViewRequest(
      entry, details.dataset.resultId ?? "", details.dataset.requirementId ?? "", options.currentSessionId(),
    );
    if (!request) return;
    try { options.send(request); options.onExpanded?.(details, entry, request); } catch (error) { options.onError?.(error); }
  };
  container.addEventListener("click", captureClick, true);
  container.addEventListener("click", completeClick);
  container.addEventListener("toggle", toggle, true);
  return () => {
    container.removeEventListener("click", captureClick, true);
    container.removeEventListener("click", completeClick);
    container.removeEventListener("toggle", toggle, true);
  };
}

export function buildWorkCardSnapshotViewRequest(entry: WorkCardPresentationEntry, currentSessionId: string): Record<string, string> | null {
  if (!entry || entry.sessionId !== currentSessionId || entry.status !== "resolved" || !entry.card
    || entry.card.card_id !== entry.cardId || entry.card.snapshot.session_id !== currentSessionId
    || (entry.card.snapshot.kind !== "linked_item_copy" && entry.card.snapshot.kind !== "linked_project_session_copy")) return null;
  return {type: "work-card.snapshot.view", session_id: currentSessionId, presentation_id: entry.presentationId, card_id: entry.cardId};
}
export function sendWorkCardSnapshotView(entry: WorkCardPresentationEntry, session: string, send: (request: Record<string, string>) => void): "sent" | "failed" | "ineligible" {
  const request = buildWorkCardSnapshotViewRequest(entry, session);
  if (!request) return "ineligible";
  try { send(request); return "sent"; } catch { return "failed"; }
}

/** Only a trusted, non-cancelled expansion of the snapshot detail is a view intent. */
export function bindWorkCardSnapshotViewEvents(container: HTMLElement, options: {
  currentSessionId: () => string; getEntry: (id: string) => WorkCardPresentationEntry | undefined;
  send: (request: Record<string, string>) => void; onError: () => void;
}): () => void {
  const clicked = new WeakMap<HTMLDetailsElement, boolean>();
  const pending = new WeakMap<HTMLDetailsElement, boolean>();
  const find = (event: Event): HTMLDetailsElement | null => event.target instanceof Element
    ? event.target.closest("summary")?.closest<HTMLDetailsElement>("details[data-work-card-snapshot-detail]") ?? null : null;
  const capture = (event: Event): void => { if (!event.isTrusted) return; const detail = find(event); if (detail) clicked.set(detail, detail.open); };
  const click = (event: Event): void => { if (!event.isTrusted) return; const detail = find(event); if (!detail) return;
    const wasOpen = clicked.get(detail); clicked.delete(detail); if (wasOpen !== undefined && !event.defaultPrevented) pending.set(detail, wasOpen); };
  const toggle = (event: Event): void => {
    const detail = event.target; if (!(detail instanceof HTMLDetailsElement) || !detail.hasAttribute("data-work-card-snapshot-detail")) return;
    const wasOpen = pending.get(detail); pending.delete(detail);
    if (wasOpen === undefined || !isExplicitResultDetailExpansion(true, false, wasOpen, detail.open)) return;
    const article = detail.closest<HTMLElement>("article[data-work-card-snapshot]");
    const entry = article && options.getEntry(article.dataset.presentationId ?? "");
    if (!entry || entry.cardId !== article?.dataset.cardId || entry.sessionId !== article.dataset.sessionId) return;
    if (sendWorkCardSnapshotView(entry, options.currentSessionId(), options.send) === "failed") options.onError();
  };
  container.addEventListener("click", capture, true); container.addEventListener("click", click); container.addEventListener("toggle", toggle, true);
  return () => { container.removeEventListener("click", capture, true); container.removeEventListener("click", click); container.removeEventListener("toggle", toggle, true); };
}
