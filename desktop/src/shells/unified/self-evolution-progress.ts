export type SelfEvolutionPhase =
  | "preparing" | "observing" | "awaiting_design_approval" | "editing" | "checking"
  | "awaiting_review" | "blocked" | "cancelling" | "cancelled" | "failed" | "unknown" | "merged";
export type SelfEvolutionStatus = "active" | "blocked" | "cancelling" | "cancelled" | "failed" | "unknown" | "completed" | "merged";
export type SelfEvolutionFact =
  | "task_accepted" | "observation_recorded" | "edit_started" | "check_started"
  | "check_passed" | "check_failed" | "blocked" | "cancelled" | "failed" | "unknown"
  | "awaiting_review" | "merged";
export type SelfEvolutionKind = "task_started" | "phase_changed" | "verification_completed" | "task_blocked" | "task_terminal";

export interface SelfEvolutionIdentity {
  session_id: string;
  task_id: string;
  attempt_id: string;
  run_id: string;
  turn_epoch: number;
}
export interface SelfEvolutionEvent extends SelfEvolutionIdentity {
  type: "self_evolution.update";
  sequence: number;
  phase: SelfEvolutionPhase;
  kind: SelfEvolutionKind;
  fact_codes: SelfEvolutionFact[];
  snapshot_revision: number;
  evidence_refs: string[];
}
export interface SelfEvolutionVerification {
  label: string;
  result: "passed" | "failed" | "not_run" | "unknown";
  evidence_ref: string | null;
}
export interface SelfEvolutionSnapshot extends SelfEvolutionIdentity {
  phase: SelfEvolutionPhase;
  status: SelfEvolutionStatus;
  snapshot_revision: number;
  sequence: number;
  findings: string[];
  verification: SelfEvolutionVerification[];
  /** Opaque host-issued handle only; renderer never treats it as a path or URL. */
  preview_handle: string | null;
}
type RecoveryHint = { session_id: string; task_id: string };
/** Lookup hints are untrusted and never authorize a host read. */
export class SelfEvolutionRecovery {
  private memory: RecoveryHint | null = null;
  private pending: (RecoveryHint & { request_id: string }) | null = null;
  remember(sessionId: string, taskId: string): void { this.memory = { session_id: sessionId, task_id: taskId }; }
  hint(sessionId: string, readStorage: () => string | null): RecoveryHint | null {
    if (this.memory?.session_id === sessionId) return { ...this.memory };
    try {
      const value = JSON.parse(readStorage() || "null");
      if (value?.session_id === sessionId && typeof value.task_id === "string"
        && /^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$/.test(value.task_id))
        return { session_id: sessionId, task_id: value.task_id };
    } catch { /* Optional storage may be unavailable or corrupt. */ }
    return null;
  }
  requested(sessionId: string, taskId: string, requestId: string): void {
    this.pending = { session_id: sessionId, task_id: taskId, request_id: requestId };
  }
  accepts(sessionId: string, taskId: string, requestId: unknown): boolean {
    return !!this.pending && this.pending.session_id === sessionId && this.pending.task_id === taskId
      && this.pending.request_id === requestId;
  }
  disconnected(): void { this.pending = null; }
}
export interface SelfEvolutionTrustedContext { session_id: string }
export interface SelfEvolutionMilestone { key: string; text: string }
export interface SelfEvolutionView extends SelfEvolutionSnapshot { milestones: SelfEvolutionMilestone[] }

const phases = new Set<string>([
  "preparing", "observing", "awaiting_design_approval", "editing", "checking", "awaiting_review",
  "blocked", "cancelling", "cancelled", "failed", "unknown", "merged",
]);
const statuses = new Set<string>(["active", "blocked", "cancelling", "cancelled", "failed", "unknown", "completed", "merged"]);
const kinds = new Set<string>(["task_started", "phase_changed", "verification_completed", "task_blocked", "task_terminal"]);
const facts = new Set<string>([
  "task_accepted", "observation_recorded", "edit_started", "check_started", "check_passed", "check_failed",
  "blocked", "cancelled", "failed", "unknown", "awaiting_review", "merged",
]);
const EXACT_EVENT_KEYS = new Set([
  "type", "session_id", "task_id", "attempt_id", "run_id", "turn_epoch", "sequence", "phase", "kind",
  "fact_codes", "snapshot_revision", "evidence_refs",
]);
const EXACT_SNAPSHOT_KEYS = new Set([
  "session_id", "task_id", "attempt_id", "run_id", "turn_epoch", "phase", "status", "snapshot_revision",
  "sequence", "findings", "verification", "preview_handle",
]);

function safeId(value: unknown): value is string { return typeof value === "string" && value.length > 0 && value.length <= 200; }
function safeCounter(value: unknown, min = 0): value is number { return Number.isSafeInteger(value) && (value as number) >= min; }
function stringArray(value: unknown): value is string[] { return Array.isArray(value) && value.length <= 100 && value.every(item => typeof item === "string" && item.length <= 2000); }
function validIdentity(value: Record<string, unknown>): boolean {
  return safeId(value.session_id) && safeId(value.task_id) && safeId(value.attempt_id) && safeId(value.run_id)
    && safeCounter(value.turn_epoch, 0);
}
function validSnapshot(value: unknown): value is SelfEvolutionSnapshot {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const v = value as Record<string, unknown>;
  if (Object.keys(v).some(key => !EXACT_SNAPSHOT_KEYS.has(key)) || !validIdentity(v)
    || !phases.has(String(v.phase)) || !statuses.has(String(v.status))
    || v.status !== statusFor(v.phase as SelfEvolutionPhase)
    || !safeCounter(v.snapshot_revision, 1) || !safeCounter(v.sequence, 0)
    || !stringArray(v.findings) || !Array.isArray(v.verification) || v.verification.length > 100
    || !(v.preview_handle === null || safeId(v.preview_handle))) return false;
  return v.verification.every(item => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return false;
    const row = item as Record<string, unknown>;
    return Object.keys(row).length === 3 && typeof row.label === "string" && row.label.length <= 200
      && ["passed", "failed", "not_run", "unknown"].includes(String(row.result))
      && (row.evidence_ref === null || safeId(row.evidence_ref))
      && (!(row.result === "passed" || row.result === "failed") || safeId(row.evidence_ref));
  });
}
function validateEvent(value: unknown): SelfEvolutionEvent | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const v = value as Record<string, unknown>;
  if (Object.keys(v).some(key => !EXACT_EVENT_KEYS.has(key)) || Object.keys(v).length !== EXACT_EVENT_KEYS.size
    || v.type !== "self_evolution.update" || !validIdentity(v)
    || !safeCounter(v.sequence, 1) || !safeCounter(v.snapshot_revision, 1)
    || !Array.isArray(v.fact_codes) || v.fact_codes.length > 20 || !v.fact_codes.every(x => typeof x === "string")
    || !stringArray(v.evidence_refs)) return null;
  // Unknown enum values are admitted only as an opaque uncertainty state; never use their text in UI.
  return v as unknown as SelfEvolutionEvent;
}

const PHASE_LABEL: Record<SelfEvolutionPhase, string> = {
  preparing: "准备中", observing: "观察中", awaiting_design_approval: "等待方案确认", editing: "改造中",
  checking: "检查中", awaiting_review: "等待你查看", blocked: "遇到阻塞", cancelling: "正在请求停止",
  cancelled: "已取消", failed: "执行失败", unknown: "状态待确认", merged: "已合入",
};
const FACT_TEXT: Partial<Record<SelfEvolutionFact, string>> = {
  task_accepted: "我接下这项改造，先从确认现状开始。",
  observation_recorded: "我找到现状依据了，正在整理改法。",
  edit_started: "我准备好了，先在候选区开始改造。",
  check_started: "改动先告一段落，我现在开始做针对性检查。",
  check_passed: "检查有结果了，我把实际验证范围一并交给你看。",
  check_failed: "检查没有通过，我会先看清问题再决定下一步。",
  blocked: "我遇到一个阻塞，先停在这里；已有改动会留在候选区。",
  cancelled: "宿主已确认任务停止，我先把当前结果留给你。",
  failed: "这次执行结束了，但我还不能把它说成完成。",
  unknown: "暂时无法确认任务状态，我先不重派也不做判断。",
  awaiting_review: "候选改造已经整理好，等你查看；我没有把它合入稳定版。",
  merged: "宿主已确认合入结果，我把这次改动交还给你。",
};
const FACT_NEEDS_EVIDENCE = new Set<SelfEvolutionFact>(["observation_recorded", "edit_started", "check_started", "check_passed", "check_failed", "merged"]);
function statusFor(phase: SelfEvolutionPhase): SelfEvolutionStatus {
  if (phase === "blocked") return "blocked";
  if (phase === "cancelling") return "cancelling";
  if (phase === "cancelled") return "cancelled";
  if (phase === "failed") return "failed";
  if (phase === "unknown") return "unknown";
  if (phase === "awaiting_review") return "completed";
  if (phase === "merged") return "merged";
  return "active";
}

/** Pure projection. Authentication belongs to the host transport; this reducer only binds its trusted session/task snapshot. */
export class SelfEvolutionProgress {
  private view: SelfEvolutionView | null = null;
  private readonly spoken = new Set<string>();

  restore(snapshot: unknown, trusted: SelfEvolutionTrustedContext, options?: { clearMilestones?: boolean }): boolean {
    if (!validSnapshot(snapshot) || !safeId(trusted?.session_id) || snapshot.session_id !== trusted.session_id) return false;
    const previous = this.view;
    if (previous && previous.session_id === snapshot.session_id && previous.task_id === snapshot.task_id) {
      const sameAttempt = snapshot.attempt_id === previous.attempt_id;
      if (snapshot.snapshot_revision < previous.snapshot_revision
        || (sameAttempt && (snapshot.run_id !== previous.run_id || snapshot.turn_epoch !== previous.turn_epoch))
        || (!sameAttempt && snapshot.snapshot_revision <= previous.snapshot_revision)) return false;
      if (snapshot.snapshot_revision === previous.snapshot_revision
        && (snapshot.sequence !== previous.sequence || snapshot.phase !== previous.phase || snapshot.status !== previous.status
          || JSON.stringify(snapshot.findings) !== JSON.stringify(previous.findings)
          || JSON.stringify(snapshot.verification) !== JSON.stringify(previous.verification)
          || snapshot.preview_handle !== previous.preview_handle)) return false;
    }
    const sameSessionTask = previous?.session_id === snapshot.session_id && previous.task_id === snapshot.task_id;
    this.view = { ...snapshot, findings: [...snapshot.findings], verification: snapshot.verification.map(row => ({ ...row })), milestones: sameSessionTask && !options?.clearMilestones ? [...previous.milestones] : [] };
    return true;
  }

  accept(input: unknown): SelfEvolutionView | null {
    if (!this.view) return null;
    const event = validateEvent(input);
    if (!event || event.session_id !== this.view.session_id || event.task_id !== this.view.task_id
      || event.attempt_id !== this.view.attempt_id || event.run_id !== this.view.run_id
      || event.turn_epoch !== this.view.turn_epoch || event.sequence <= this.view.sequence
      || event.snapshot_revision <= this.view.snapshot_revision) return null;
    const unknown = !phases.has(String(event.phase)) || !kinds.has(String(event.kind))
      || event.fact_codes.some(code => !facts.has(code));
    if (!unknown && event.fact_codes.some(code => FACT_NEEDS_EVIDENCE.has(code as SelfEvolutionFact) && event.evidence_refs.length === 0)) return null;

    const phase = unknown ? "unknown" : event.phase;
    const next: SelfEvolutionView = { ...this.view, phase, status: unknown ? "unknown" : statusFor(phase),
      snapshot_revision: event.snapshot_revision, sequence: event.sequence, milestones: [...this.view.milestones] };
    if (unknown) {
      this.addMilestone(next, `${event.session_id}:${event.task_id}:${event.attempt_id}:unknown:${event.sequence}`, "当前状态待确认，我先不做判断。");
    } else {
      const fresh = event.fact_codes.filter(code => FACT_TEXT[code] && !this.spoken.has(`${event.session_id}:${event.task_id}:${event.attempt_id}:${code}`));
      if (fresh.length) {
        const text = fresh.includes("observation_recorded") && fresh.includes("edit_started")
          ? "我找到现状依据了，也整理好了改法；现在开始改造候选区。"
          : fresh.map(code => FACT_TEXT[code]!).join(" ");
        this.addMilestone(next, `${event.session_id}:${event.task_id}:${event.attempt_id}:${event.kind}:${event.sequence}`, text);
        for (const code of fresh) this.spoken.add(`${event.session_id}:${event.task_id}:${event.attempt_id}:${code}`);
      }
    }
    this.view = next;
    return this.current();
  }

  current(): SelfEvolutionView | null {
    return this.view ? { ...this.view, findings: [...this.view.findings], verification: this.view.verification.map(row => ({ ...row })), milestones: [...this.view.milestones] } : null;
  }

  private addMilestone(view: SelfEvolutionView, key: string, text: string): void {
    if (view.milestones.some(item => item.key === key)) return;
    view.milestones.push({ key, text });
  }
}

function escapeHtml(value: unknown): string {
  return String(value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]!);
}
function verificationLabel(result: SelfEvolutionVerification["result"]): string {
  return result === "passed" ? "有证据：已通过" : result === "failed" ? "有证据：未通过" : result === "not_run" ? "尚未运行" : "结果待确认";
}

/** Returns only evidence-bounded review content; actions stay absent until trusted host capabilities exist. */
export function renderSelfEvolutionReview(input: SelfEvolutionSnapshot | SelfEvolutionView, diff: DiffResult | null = null): string {
  const { milestones: _milestones, ...snapshot } = input as SelfEvolutionView;
  if (!validSnapshot(snapshot)) return "";
  const findings = snapshot.findings.length
    ? `<ul class="self-evolution-progress__findings">${snapshot.findings.map(text => `<li>${escapeHtml(text)}</li>`).join("")}</ul>` : `<p class="self-evolution-progress__empty">宿主尚未提供可展示的观察摘要。</p>`;
  const checks = snapshot.verification.length
    ? `<ul class="self-evolution-progress__checks">${snapshot.verification.map(row => `<li><span>${escapeHtml(row.label)}</span>：${verificationLabel(row.result)}${row.evidence_ref ? ` <span class="self-evolution-progress__evidence">证据编号 ${escapeHtml(row.evidence_ref)}</span>` : ""}</li>`).join("")}</ul>` : `<p class="self-evolution-progress__empty">目前没有宿主登记的验证证据。</p>`;
  const blocked = snapshot.phase === "blocked" || snapshot.status === "blocked"
    ? `<p class="self-evolution-progress__warning" role="note">当前任务处于阻塞状态；查看文件差异不表示可以合入。</p>` : "";
  const reviewTitle = snapshot.phase === "awaiting_review" ? "待审阅交付" : "改动与验证概览";
  return `<section class="self-evolution-progress__review" aria-label="${reviewTitle}">
    <h3>${reviewTitle}</h3>
    <h4>宿主观察摘要</h4>${findings}
    <h4>验证证据</h4>${checks}<p class="self-evolution-progress__empty">目前只显示宿主登记的证据编号；读取日志或证据正文的安全入口尚未接入。</p>
    <h4>候选文件变更</h4>${diff ? renderSelfEvolutionDiffReview(diff) : `<p class="self-evolution-progress__empty">文件级变更清单尚未由宿主提供，当前不能确认具体改动文件。</p>`}
    <h4>预览与合入</h4><p class="self-evolution-progress__unavailable">真实可用预览尚未接入${snapshot.preview_handle ? "；收到的不透明句柄未绑定可信预览解析器，不能当作预览入口。" : "，不能声称已查看候选效果。"}</p>
    <p class="self-evolution-progress__unavailable">原生确认与受控合入当前不可用；合入预检仍为 blocked，不会调用 Git。</p>
    ${blocked}
  </section>`;
}

/** Returns inline, accessible status markup only; no overlay or fabricated action URL. */
export function renderSelfEvolutionProgress(input: SelfEvolutionSnapshot | SelfEvolutionView, extraMilestones: readonly SelfEvolutionMilestone[] = []): string {
  const { milestones: embeddedMilestones = [], ...snapshot } = input as SelfEvolutionView;
  const milestones = extraMilestones.length ? extraMilestones : embeddedMilestones;
  if (!validSnapshot(snapshot)) return "";
  const phase = PHASE_LABEL[snapshot.phase] ?? PHASE_LABEL.unknown;
  const speech = milestones.length
    ? `<ul class="self-evolution-progress__milestones" aria-label="关键进展">${milestones.slice(-3).map(item => `<li>${escapeHtml(item.text)}</li>`).join("")}</ul>` : "";
  const greeting = snapshot.phase === "awaiting_review" ? "✦ 我改造自己的进度" : "✦ 我在改造自己";
  return `<section class="self-evolution-progress" aria-label="自我改造任务进度"><div class="self-evolution-progress__heading"><strong>${greeting}</strong><span>${phase}</span></div><div class="self-evolution-progress__body" role="status" aria-live="polite" aria-atomic="true">${speech}</div>${renderSelfEvolutionReview(snapshot)}</section>`;
}

export interface SelfEvolutionDiffIdentity extends SelfEvolutionIdentity {
  snapshot_revision: number;
  request_id: string;
}
type DiffFile = { path: string; change: string; before_content_status: string; after_content_status: string; diff?: string };
type DiffResult = SelfEvolutionDiffIdentity & { candidate_digest: string; truncated: boolean; content_complete: boolean; files: DiffFile[]; apply_available: boolean; apply_eligible_paths: string[] };
const DIFF_STATUSES = new Set(["absent", "text", "binary_or_unsupported", "over_text_limit"]);
const DIFF_CHANGES = new Set(["added", "deleted", "modified", "replaced"]);
function sameDiffIdentity(a: SelfEvolutionDiffIdentity, b: SelfEvolutionDiffIdentity): boolean {
  return a.session_id === b.session_id && a.task_id === b.task_id && a.attempt_id === b.attempt_id
    && a.run_id === b.run_id && a.turn_epoch === b.turn_epoch && a.snapshot_revision === b.snapshot_revision
    && a.request_id === b.request_id;
}
/** In-memory only. A WebSocket response is never an authorization or a merge grant. */
export class SelfEvolutionDiffReview {
  private pending: SelfEvolutionDiffIdentity | null = null;
  private result: DiffResult | null = null;
  requested(identity: SelfEvolutionDiffIdentity): void { this.pending = { ...identity }; this.result = null; }
  clear(): void { this.pending = null; this.result = null; }
  accept(input: unknown, current: SelfEvolutionDiffIdentity): boolean {
    if (!this.pending || !sameDiffIdentity(this.pending, current) || !input || typeof input !== "object" || Array.isArray(input)) return false;
    const row = input as Record<string, unknown>;
    const keys = ["type", "session_id", "task_id", "attempt_id", "run_id", "turn_epoch", "snapshot_revision", "request_id", "candidate_digest", "truncated", "content_complete", "files"];
    const capabilityKeysPresent = Object.hasOwn(row, "apply_available") || Object.hasOwn(row, "apply_eligible_paths");
    if (capabilityKeysPresent) {
      keys.push("apply_available", "apply_eligible_paths");
      if (typeof row.apply_available !== "boolean" || !Array.isArray(row.apply_eligible_paths)) return false;
    }
    if (Object.keys(row).length !== keys.length || Object.keys(row).some(key => !keys.includes(key))
      || row.type !== "self_evolution.review.result" || !sameDiffIdentity(row as unknown as SelfEvolutionDiffIdentity, current)
      || typeof row.candidate_digest !== "string" || !/^[a-f0-9]{64}$/.test(row.candidate_digest)
      || typeof row.truncated !== "boolean" || typeof row.content_complete !== "boolean"
      || !Array.isArray(row.files) || row.files.length > 256) return false;
    let total = 0;
    for (const raw of row.files) {
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) return false;
      const file = raw as Record<string, unknown>;
      if (Object.keys(file).some(key => !["path", "change", "before_content_status", "after_content_status", "diff"].includes(key))
        || typeof file.path !== "string" || !file.path || file.path.length > 1024
        || !DIFF_CHANGES.has(String(file.change)) || !DIFF_STATUSES.has(String(file.before_content_status))
        || !DIFF_STATUSES.has(String(file.after_content_status))
        || (file.diff !== undefined && typeof file.diff !== "string")) return false;
      total += typeof file.diff === "string" ? file.diff.length : 0;
      if (total > 262144) return false;
    }
    const eligiblePaths = capabilityKeysPresent ? row.apply_eligible_paths as unknown[] : [];
    if (eligiblePaths.length > 256 || eligiblePaths.some(path => typeof path !== "string" || !path || path.length > 1024)
      || new Set(eligiblePaths).size !== eligiblePaths.length
      || (row.apply_available === true && (!row.content_complete || row.truncated || eligiblePaths.length === 0
        || eligiblePaths.some(path => !(row.files as DiffFile[]).some(file => file.path === path))))
      || (row.apply_available === false && eligiblePaths.length > 0)) return false;
    this.result = { ...current, candidate_digest: row.candidate_digest, truncated: row.truncated,
      content_complete: row.content_complete, files: (row.files as DiffFile[]).map(file => ({ ...file })),
      apply_available: row.apply_available === true, apply_eligible_paths: eligiblePaths as string[] };
    this.pending = null;
    return true;
  }
  current(identity: SelfEvolutionDiffIdentity): DiffResult | null {
    return this.result && sameDiffIdentity(this.result, identity)
      ? { ...this.result, files: this.result.files.map(row => ({ ...row })) } : null;
  }
}
export function renderSelfEvolutionDiffReview(result: DiffResult | null): string {
  if (!result) return `<p class="self-evolution-progress__empty">尚未取得经宿主批准的文件差异。</p>`;
  const warnings = `${result.truncated ? "差异已截断；不能视为完整内容。" : ""}${!result.content_complete ? "部分内容未提供或无法显示。" : ""}`;
  const files = result.files.map(file => `<li><details><summary>${escapeHtml(file.path)} · ${escapeHtml(file.change)}</summary>`
    + (file.diff !== undefined ? `<pre class="self-evolution-progress__diff">${escapeHtml(file.diff)}</pre>`
      : `<p>内容未提供或无法显示（原：${escapeHtml(file.before_content_status)}；现：${escapeHtml(file.after_content_status)}）。</p>`) + `</details></li>`).join("");
  return `${warnings ? `<p class="self-evolution-progress__warning" role="note">${warnings}</p>` : ""}`
    + `${files ? `<ul>${files}</ul>` : `<p>本次扫描没有发现文件变化。</p>`}`
    + `<p class="self-evolution-progress__empty">这是非原子时间点审阅，不授予合入权限。</p>`;
}

export type SelfEvolutionApplyOutcome = "applied" | "partial" | "declined" | "cancelled" | "stale" | "failed" | "unavailable";
export interface SelfEvolutionApplyResponseIdentity extends SelfEvolutionIdentity {
  snapshot_revision: number;
  request_id: string;
}
export interface SelfEvolutionApplyRequest {
  type: "self_evolution.apply.request";
  task_id: string;
  attempt_id: string;
  request_id: string;
  selected_paths: string[];
}
function sameApplyContext(a: SelfEvolutionApplyResponseIdentity, b: SelfEvolutionApplyResponseIdentity): boolean {
  return a.session_id === b.session_id && a.task_id === b.task_id && a.attempt_id === b.attempt_id
    && a.run_id === b.run_id && a.turn_epoch === b.turn_epoch && a.request_id === b.request_id;
}
/** In-memory opt-in request lifecycle. It tracks correlation only; it grants no authority. */
export class SelfEvolutionApply {
  private pending: SelfEvolutionApplyResponseIdentity | null = null;
  private outcome: SelfEvolutionApplyOutcome | null = null;
  begin(identity: SelfEvolutionApplyResponseIdentity): void {
    this.pending = { ...identity };
    this.outcome = null;
  }
  requested(review: DiffResult | null, selectedPaths: readonly string[], requestId: string): SelfEvolutionApplyRequest | null {
    if (!review?.apply_available || !selectedPaths.length || !safeId(requestId)
      || new Set(selectedPaths).size !== selectedPaths.length
      || selectedPaths.some(path => !review.apply_eligible_paths.includes(path))) return null;
    this.begin({ ...review, request_id: requestId });
    return { type: "self_evolution.apply.request", task_id: review.task_id, attempt_id: review.attempt_id,
      request_id: requestId, selected_paths: [...selectedPaths] };
  }
  accept(input: unknown, current: SelfEvolutionApplyResponseIdentity): boolean {
    if (!this.pending || !sameApplyContext(this.pending, current)
      || current.snapshot_revision < this.pending.snapshot_revision
      || !input || typeof input !== "object" || Array.isArray(input)) return false;
    const row = input as Record<string, unknown>;
    // The result must echo the original review revision, not the newer snapshot
    // that may have arrived while the host was applying files.
    const identityKeys = ["session_id", "task_id", "attempt_id", "run_id", "turn_epoch", "snapshot_revision", "request_id"];
    const common = ["type", ...identityKeys];
    if (identityKeys.some(key => row[key] !== (this.pending as unknown as Record<string, unknown>)[key])) return false;
    if (row.type === "self_evolution.apply.result") {
      if (Object.keys(row).length !== common.length + 1 || Object.keys(row).some(key => ![...common, "status"].includes(key))
        || !["applied", "partial", "declined", "cancelled", "stale", "failed"].includes(String(row.status))) return false;
      this.outcome = row.status as SelfEvolutionApplyOutcome;
    } else if (row.type === "self_evolution.apply.unavailable") {
      if (Object.keys(row).length !== common.length + 1 || Object.keys(row).some(key => ![...common, "reason"].includes(key))
        || !["unsupported", "stale", "foreign", "cancelled", "busy", "invalid"].includes(String(row.reason))) return false;
      this.outcome = row.reason === "cancelled" ? "cancelled" : row.reason === "stale" ? "stale" : "unavailable";
    } else return false;
    this.pending = null;
    return true;
  }
  current(): { outcome: SelfEvolutionApplyOutcome; message: string } | null {
    if (!this.outcome) return null;
    const messages: Record<SelfEvolutionApplyOutcome, string> = {
      applied: "宿主报告所选改动已应用到实验区；这不代表合入稳定版。",
      partial: "警告：仅部分文件已写入实验区。请先检查实际改动并完成验证，再决定是否重试；不要直接重试，以免重复或覆盖。",
      declined: "你在原生确认中拒绝了本次实验区应用。",
      cancelled: "本次实验区应用已取消。",
      stale: "审阅状态已变化，本次请求未继续；请重新查看最新差异。",
      failed: "宿主未能完成本次实验区应用。",
      unavailable: "宿主当前无法执行实验区应用请求。",
    };
    return { outcome: this.outcome, message: messages[this.outcome] };
  }
  clear(): void { this.pending = null; this.outcome = null; }
}

/** Opt-in checkboxes are rendered only from an accepted host capability and start unchecked. */
export function renderSelfEvolutionApplyControls(review: DiffResult | null, outcome: ReturnType<SelfEvolutionApply["current"]>, selected: readonly string[] = [], pending = false): string {
  const status = outcome ? `<p class="self-evolution-progress__apply-status${outcome.outcome === "partial" ? " self-evolution-progress__warning" : ""}" role="${outcome.outcome === "partial" ? "alert" : "status"}">${escapeHtml(outcome.message)}</p>` : "";
  const pendingStatus = pending ? `<p class="self-evolution-progress__apply-status" role="status">等待宿主返回本次实验区应用结果；结果仍绑定到原审阅版本。</p>` : "";
  if (!review?.apply_available) return pendingStatus + status;
  const choices = review.apply_eligible_paths.map(path => `<label><input type="checkbox" data-self-evolution-apply-path="${escapeHtml(path)}"${selected.includes(path) ? " checked" : ""}${pending ? " disabled" : ""}> ${escapeHtml(path)}</label>`).join("");
  return `<section class="self-evolution-progress__apply" aria-label="实验区选择应用"><h4>选择应用到实验区</h4><p>仅提交你勾选的已审阅文件。目标是实验区，不是稳定版；继续前仍会出现原生确认。</p><div>${choices}</div><button type="button" class="unified-btn unified-btn-ghost" data-self-evolution-apply${pending || !selected.length ? " disabled" : ""}>将所选文件应用到实验区</button>${pendingStatus}${status}</section>`;
}
