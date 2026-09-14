import type { AgentWsClient, ChangeLedgerItem, PlanChangeItem, PlanSuggestion, ProjectArtifactSummary, ProjectDocItem, ServerEvent, ServiceListItem, TerminalSessionItem } from "../../api/ws";
import { RUNAWAY_BLOCKER, RUNAWAY_STAGE, RUNAWAY_STATUS } from "../../copy/user-messages";
import { escapeHtml } from "../chat-state";
import { renderDocsCatalog } from "./doc-reading";
import type { MainFocus } from "./plan-review";
import { acceptLabel, truncateSummary, diffStats } from "./plan-review";

export type { PlanSuggestion, ServiceListItem, TerminalSessionItem };

// ---- new task-flow types ----

export interface TaskItem {
  line: number;
  text: string;
  done: boolean;
  status: "done" | "current" | "pending" | "new" | "skipped" | "removed";
  subtasks?: { text: string; done: boolean }[];
}

export interface TaskPhase {
  title: string;
  tasks: TaskItem[];
}

/** Snapshot of task list for diffing across updates. */
export interface TaskSnapshot {
  lines: Set<number>;
  lineTexts: Map<number, string>;
}

export type OverlayPanel = "plan" | "docs" | "projects" | "threads" | null;

export type FlowStage = "requirements" | "documentation" | "design" | "implementation" | "verification" | "release";

export interface CodeFollowup {
  mode: "agent_cleanup" | "git_guide";
  prefill?: string;
  paths?: string[];
  droppedBody?: string;
  droppedId?: string;
  guide?: { workspace_rel?: string; commands?: string[]; note?: string };
}

// ---- existing types (compat) ----

export interface PlanOverlayData {
  requestId: string;
  title: string;
  summary: string;
  tasksPreview: string;
  planStatus: string;
}

export interface SwitchOverlayData {
  requestId: string;
  projectId: string;
  message: string;
  action: string;
}

export interface ProjectListItem {
  id: string;
  tasksDone: number;
  tasksTotal: number;
  sessionId: string | null;
  isCurrent: boolean;
}

export interface ProjectThreadItem {
  sessionId: string;
  title: string;
  preview: string;
  updatedAt: string;
  archived: boolean;
}

export interface ProjectPanelState {
  projectId: string;
  projectSummary: string;
  planStatus: string;
  tasksMarkdown: string;
  mapMarkdown: string;
  tasksDone: number;
  tasksTotal: number;
  tasksAllDone: boolean;
  planOverlay: PlanOverlayData | null;
  projects: ProjectListItem[];
  switchOverlay: SwitchOverlayData | null;
  switchInProgress: boolean;
  pendingPickerId: string;
  // new fields
  overlayPanel: OverlayPanel;
  taskPhases: TaskPhase[];
  taskSnapshot: TaskSnapshot;
  planBannerCollapsed: boolean;
  changeTimelineExpanded: boolean;
  switchConfirmTarget: ProjectListItem | null;
  projectSearchQuery: string;
  planChangeLog: PlanChangeItem[];
  changeTimeline: ChangeLedgerItem[];
  executionStage: FlowStage | null;
  workflowStage: FlowStage | null;
  needsDesignConfirm: boolean;
  executionStageStatus: "in_progress" | "blocked" | "ready" | string;
  executionStageReason: string;
  executionStageBlockers: string[];
  executionStageMissing: string[];
  executionStageWarnings: string[];
  executionStageAffected: string[];
  executionStageDeferred: string[];
  executionStageArtifacts: ProjectArtifactSummary[];
  highlightChanges: boolean;
  highlightedLines: Set<number>;
  // doc panel
  projectDocs: ProjectDocItem[];
  currentDocPath: string;
  currentDocContent: string;
  newDocName: string;
  renamingDocPath: string;
  renameDraft: string;
  deleteConfirmPath: string;
  docView: "preview" | "edit";
  docEditDraft: string;
  docDirty: boolean;
  docSaving: boolean;
  // task add
  quickAddText: string;
  // auto-detect
  detectedProject: { id: string; reason: string } | null;
  // Plan Agent warnings (legacy / non-structured)
  planWarnings: string[];
  planWarningsPending: string[];
  dismissedWarningFingerprint: string;
  planWarningStage: FlowStage | null;
  // Undo toast
  undoDescription: string;
  undoTimerId: number | null;
  // Degradation indicator
  degradationLevel: string;
  degradationLabel: string;
  // Change confirmation
  changesLevel: string | null;
  dismissedTaskChangeFingerprint: string;
  autoConfirmTimerId: number | null;
  externalChanges: boolean;
  suggestions: PlanSuggestion[];
  autoFixNotices: string[];
  operationalNotices: string[];
  dismissedOperationalNoticeFingerprint: string;
  partnerNotices: string[];
  partnerBusy: boolean;
  nextTask: string | null;
  nextTaskLine: number | null;
  // Phase 27 — managed services panel
  services: ServiceListItem[];
  servicesLoading: boolean;
  servicesError: string;
  servicesLogName: string;
  servicesLogText: string;
  // Persistent interactive terminal sessions (M0 visibility surface).
  terminalSessions: TerminalSessionItem[];
  terminalsLoading: boolean;
  terminalsError: string;
  terminalsCollapsed: boolean;
  terminalDetails: TerminalSessionItem | null;
  terminalOutput: string;
  terminalOutputCursor: number;
  terminalOutputLoading: boolean;
  terminalOutputError: string;
  terminalOutputCursorReset: boolean;
  terminalOutputTruncated: boolean;
  // Phase 27 M1 — turn evidence strip
  turnArmedId: string;
  turnArmedText: string;
  turnEvidence: Array<{ tool: string; ok: boolean }>;
  turnGateNotice: string;
  // G14 M2 — exec reliability strip
  turnPostcondition: string;
  turnCircuitOpen: string[];
  turnPlaybookId: string;
  turnFailureClass: string;
  // Phase 36 — project threads (live + archive)
  activeSessionId: string;
  threads: ProjectThreadItem[];
  threadsLoading: boolean;
  currentSessionId: string;
  mainFocus: MainFocus;
  reviewFocusId: string | null;
  // UX-026 — sidebar body / adopt feedback
  servicesCollapsed: boolean;
  suggestionAdoptFlash: string | null;
  adoptedFooterMessage: string | null;
  adoptPendingId: string | null;
  turnInProgress: boolean;
  deliveryProfile: string;
  reviewVerdict: string | null;
  reviewBlockersCount: number;
  reviewProgressBlocked: boolean;
  scopeConfirmedAt: string;
  runawayEnabled: boolean;
  runawayStatus: string;
  runawayCheckpoint: string;
  runawayRepairCount: number;
  runawayLastVerification: string | null;
  runawayPausedReason: string | null;
  runawayAcceptancePassed: boolean;
  runawayVerificationEvidence: Record<string, unknown> | null;
  runawayVerificationEvidencePath: string | null;
  runawayVersion: number;
  runawayPhase: string;
  runawayUserLine: string;
  runawayMode: string;
  runawayBlocked: boolean;
  runawayChecklist: {
    passed: number;
    total: number;
    currentId: string | null;
    currentTitle: string | null;
    failedId: string | null;
  } | null;
  runawayCancelAvailable: boolean;
  runawayBlock: {
    itemId: string | null;
    reason: string;
    command: string;
    tail: string;
  } | null;
  scopeNeedsReconfirm: boolean;
  flowPreviewStage: FlowStage | null;
  milestoneAccepted: boolean;
  milestoneAcceptedAt: string | null;
  codeFollowup: CodeFollowup | null;
  nextTurnChangeSummary: string | null;
  dropTaskPendingId: string | null;
  dropTaskPendingWorking: boolean;
}

export type ProjectGoalStatus = "empty" | "processing" | "decision" | "blocked" | "failed" | "completed";

export interface ProjectGoalViewModel {
  status: ProjectGoalStatus;
  statusLabel: string;
  title: string;
  summary: string;
  nextStep: string;
  action: string | null;
  actionLabel: string | null;
  secondaryAction?: string | null;
  secondaryActionLabel?: string | null;
}

export interface HeaderNextStepAction {
  action: string;
  label: string;
  accent?: boolean;
  taskId?: string;
}

export interface HeaderNextStepView {
  kind: "dual-draft" | "single" | "none";
  actions: HeaderNextStepAction[];
}

export interface ProjectPanelCallbacks {
  onProjectSwitch: (projectId: string) => void;
  onProjectSwitchConfirm: () => void;
  onProjectSwitchCancel: () => void;
  onPlanConfirm: () => void;
  onPlanEdit: () => void;
  onRefreshProjects: () => void;
  onNewThread: () => void;
  onOpenThread: (sessionId: string) => void;
  onReturnActiveThread: () => void;
  onOpenProjects: () => void;
  onNewProject: () => void;
  onScopeConfirm: () => void;
  onDesignConfirm: () => void;
  onStopTurn: () => void;
  onMilestoneAccept: () => void;
}

// ---- helpers ----

function isRunawayV2(state: ProjectPanelState): boolean {
  return state.runawayEnabled && state.runawayVersion >= 2;
}

function applyRunawayPayload(
  state: ProjectPanelState,
  event: {
    runaway_version?: number;
    runaway_phase?: string;
    runaway_user_line?: string;
    runaway_mode?: string;
    runaway_blocked?: boolean;
    runaway_checklist?: {
      passed?: number;
      total?: number;
      current_id?: string | null;
      current_title?: string | null;
      failed_id?: string | null;
    } | null;
    runaway_block?: {
      item_id?: string | null;
      reason?: string;
      command?: string;
      tail?: string;
    } | null;
    runaway_status?: string;
    runaway_checkpoint?: string | null;
    runaway_paused_reason?: string | null;
  },
): void {
  if (event.runaway_version !== undefined) {
    state.runawayVersion = Number(event.runaway_version) || 0;
  }
  if (event.runaway_phase !== undefined) {
    state.runawayPhase = event.runaway_phase ?? "";
  }
  if (event.runaway_user_line !== undefined) {
    state.runawayUserLine = event.runaway_user_line ?? "";
  } else if (event.runaway_status) {
    state.runawayUserLine = event.runaway_status;
  }
  if (event.runaway_mode !== undefined) {
    state.runawayMode = event.runaway_mode ?? "auto";
  }
  if (event.runaway_blocked !== undefined) {
    state.runawayBlocked = Boolean(event.runaway_blocked);
  }
  const checklist = event.runaway_checklist;
  if (checklist && typeof checklist === "object") {
    state.runawayChecklist = {
      passed: Number(checklist.passed ?? 0),
      total: Number(checklist.total ?? 0),
      currentId: checklist.current_id ?? null,
      currentTitle: checklist.current_title ?? null,
      failedId: checklist.failed_id ?? null,
    };
  }
  const block = event.runaway_block;
  if (block && typeof block === "object") {
    state.runawayBlock = {
      itemId: block.item_id ?? null,
      reason: block.reason ?? "",
      command: block.command ?? "",
      tail: block.tail ?? "",
    };
  } else if (event.runaway_blocked === false) {
    state.runawayBlock = null;
    state.runawayPausedReason = null;
    state.runawayCheckpoint = event.runaway_checkpoint ?? state.runawayCheckpoint;
  }
}

function normalizeSuggestions(raw: unknown): PlanSuggestion[] {
  if (!Array.isArray(raw)) return [];
  const out: PlanSuggestion[] = [];
  raw.forEach((item, i) => {
    if (typeof item === "string") {
      const body = item.trim();
      if (!body) return;
      out.push({
        id: `legacy-${i}`,
        kind: "info",
        title: "计划建议",
        body,
        risk: "suggest",
      });
      return;
    }
    if (!item || typeof item !== "object") return;
    const o = item as Record<string, unknown>;
    const id = typeof o.id === "string" && o.id ? o.id : `sug-${i}`;
    const body = typeof o.body === "string" ? o.body : String(o.title ?? "");
    if (!body && !o.title) return;
    if (o.kind === "stale" || (o.payload && typeof o.payload === "object" && (o.payload as Record<string, unknown>).operational === true)) return;
    out.push({
      id,
      kind: typeof o.kind === "string" ? o.kind : "info",
      title: typeof o.title === "string" && o.title ? o.title : "计划建议",
      body: body || String(o.title),
      risk: typeof o.risk === "string" ? o.risk : "suggest",
      action: typeof o.action === "string" ? o.action : null,
      payload: o.payload && typeof o.payload === "object"
        ? (o.payload as Record<string, unknown>)
        : {},
    });
  });
  return out;
}

function suggestionWhatChanged(s: PlanSuggestion): string {
  const value = s.payload?.what_changed;
  return typeof value === "string" && value.trim() ? value.trim() : "见 diff";
}

const FLOW_STAGES: Array<{ id: FlowStage; label: string; short: string }> = [
  { id: "requirements", label: "需求", short: "范围" },
  { id: "documentation", label: "文档整理", short: "文档" },
  { id: "design", label: "设计", short: "计划" },
  { id: "implementation", label: "实现", short: "写码" },
  { id: "verification", label: "验证", short: "证据" },
  { id: "release", label: "发布", short: "里程碑" },
];

function isFlowStage(value: unknown): value is FlowStage {
  return FLOW_STAGES.some((stage) => stage.id === value);
}

function isDocumentationBatch(state: ProjectPanelState): boolean {
  return state.executionStage === "documentation" && state.executionStageStatus === "in_progress";
}

function operationalNoticeFingerprint(notices: string[]): string {
  return Array.from(new Set(notices.map((notice) => notice.trim()).filter(Boolean))).join("\u001f");
}

function renderOperationalNotices(notices: string[]): string {
  const lines = notices
    .map((notice) => `<div class="sidebar-change-banner-changes">${escapeHtml(notice)}</div>`)
    .join("");
  return `<div class="sidebar-change-banner" style="border-color:#8b7cf6;background:color-mix(in srgb, #8b7cf6 7%, var(--ma-surface));">
    <div class="sidebar-change-banner-title">运行提醒</div>
    ${lines}
    <button type="button" class="unified-btn" data-action="dismiss-operational-notices" style="margin-top:0.3rem;font-size:0.72rem;">关闭</button>
  </div>`;
}

function uniquePlanWarnings(warnings: string[]): string[] {
  return Array.from(new Set(warnings.map((warning) => warning.trim()).filter(Boolean)));
}

function planWarningFingerprint(warnings: string[]): string {
  return uniquePlanWarnings(warnings).join("\u001f");
}

function resetPlanWarningState(state: ProjectPanelState): void {
  state.planWarnings = [];
  state.planWarningsPending = [];
  state.dismissedWarningFingerprint = "";
  state.planWarningStage = null;
  state.dismissedTaskChangeFingerprint = "";
}

export function resetProjectScopedState(state: ProjectPanelState): void {
  if (typeof window !== "undefined") {
    if (state.autoConfirmTimerId !== null) window.clearInterval(state.autoConfirmTimerId);
    if (state.undoTimerId !== null) window.clearTimeout(state.undoTimerId);
  }
  state.projectSummary = "";
  state.planStatus = "";
  state.tasksMarkdown = "";
  state.mapMarkdown = "";
  state.tasksDone = 0;
  state.tasksTotal = 0;
  state.tasksAllDone = false;
  state.planOverlay = null;
  state.switchConfirmTarget = null;
  state.taskPhases = [];
  state.taskSnapshot = { lines: new Set(), lineTexts: new Map() };
  state.planChangeLog = [];
  state.changeTimeline = [];
  state.executionStage = null;
  state.workflowStage = null;
  state.needsDesignConfirm = false;
  state.executionStageStatus = "ready";
  state.executionStageReason = "";
  state.executionStageBlockers = [];
  state.executionStageMissing = [];
  state.executionStageWarnings = [];
  state.executionStageAffected = [];
  state.executionStageDeferred = [];
  state.executionStageArtifacts = [];
  state.highlightChanges = false;
  state.highlightedLines = new Set();
  state.currentDocPath = "";
  state.currentDocContent = "";
  state.newDocName = "";
  state.renamingDocPath = "";
  state.renameDraft = "";
  state.deleteConfirmPath = "";
  state.docView = "preview";
  state.docEditDraft = "";
  state.docDirty = false;
  state.docSaving = false;
  state.quickAddText = "";
  resetPlanWarningState(state);
  state.undoDescription = "";
  state.undoTimerId = null;
  state.degradationLevel = "L1";
  state.degradationLabel = "全功能";
  state.changesLevel = null;
  state.autoConfirmTimerId = null;
  state.externalChanges = false;
  state.suggestions = [];
  state.autoFixNotices = [];
  state.operationalNotices = [];
  state.dismissedOperationalNoticeFingerprint = "";
  state.partnerNotices = [];
  state.partnerBusy = false;
  state.nextTask = null;
  state.nextTaskLine = null;
  state.servicesLoading = false;
  state.servicesError = "";
  state.servicesLogName = "";
  state.servicesLogText = "";
  state.turnArmedId = "";
  state.turnArmedText = "";
  state.turnEvidence = [];
  state.turnGateNotice = "";
  state.turnPostcondition = "none";
  state.turnCircuitOpen = [];
  state.turnPlaybookId = "";
  state.turnFailureClass = "";
  state.activeSessionId = "";
  state.threads = [];
  state.threadsLoading = false;
  state.currentSessionId = "";
  state.mainFocus = "chat";
  state.reviewFocusId = null;
  state.suggestionAdoptFlash = null;
  state.adoptedFooterMessage = null;
  state.adoptPendingId = null;
  state.turnInProgress = false;
  state.deliveryProfile = "solo";
  state.reviewVerdict = null;
  state.reviewBlockersCount = 0;
  state.reviewProgressBlocked = false;
  state.scopeConfirmedAt = "";
  state.runawayEnabled = false;
  state.runawayStatus = "";
  state.runawayCheckpoint = "";
  state.runawayRepairCount = 0;
  state.runawayLastVerification = null;
  state.runawayPausedReason = null;
  state.runawayAcceptancePassed = false;
  state.runawayVerificationEvidence = null;
  state.runawayVerificationEvidencePath = null;
  state.runawayVersion = 0;
  state.runawayPhase = "";
  state.runawayUserLine = "";
  state.runawayMode = "auto";
  state.runawayBlocked = false;
  state.runawayChecklist = null;
  state.runawayCancelAvailable = false;
  state.runawayBlock = null;
  state.scopeNeedsReconfirm = false;
  state.flowPreviewStage = null;
  state.milestoneAccepted = false;
  state.milestoneAcceptedAt = null;
  state.codeFollowup = null;
  state.nextTurnChangeSummary = null;
  state.dropTaskPendingId = null;
  state.dropTaskPendingWorking = false;
}

export function shouldApplyProjectEvent(
  state: ProjectPanelState,
  eventProjectId: string | null | undefined,
): boolean {
  const incomingProjectId = (eventProjectId || "").trim();
  const pendingProjectId = state.switchInProgress
    ? (state.pendingPickerId || state.switchOverlay?.projectId || "").trim()
    : "";
  if (pendingProjectId && incomingProjectId !== pendingProjectId) return false;
  if (state.projectId && incomingProjectId !== state.projectId) return false;
  return true;
}

export function getTaskChangeFingerprint(state: ProjectPanelState): string {
  return JSON.stringify({
    projectId: state.projectId,
    changesLevel: state.changesLevel,
    externalChanges: state.externalChanges,
    changes: state.planChangeLog.map((change) => ({
      id: change.id,
      kind: change.kind,
      task_text: change.task_text,
      reason: change.reason,
      line: change.line ?? null,
    })),
  });
}

function updatePlanWarningState(
  state: ProjectPanelState,
  stage: FlowStage,
  incomingWarnings?: string[],
): void {
  const previousStage = state.planWarningStage;
  const leavingDocumentation = previousStage === "documentation" && stage !== "documentation";
  if (stage === "documentation") {
    if (incomingWarnings !== undefined) {
      state.planWarningsPending = uniquePlanWarnings([
        ...state.planWarningsPending,
        ...incomingWarnings,
      ]);
    }
    state.planWarnings = [];
  } else if (incomingWarnings !== undefined || leavingDocumentation) {
    const warnings = uniquePlanWarnings([
      ...(leavingDocumentation ? state.planWarningsPending : []),
      ...(incomingWarnings ?? []),
    ]);
    state.planWarningsPending = [];
    state.planWarnings = planWarningFingerprint(warnings) === state.dismissedWarningFingerprint ? [] : warnings;
  }
  state.planWarningStage = stage;
}

function hasOpenTasks(state: ProjectPanelState): boolean {
  return state.tasksTotal > state.tasksDone || state.taskPhases.some((phase) => phase.tasks.some((task) => !task.done));
}

function hasQualityEvidence(state: ProjectPanelState): boolean {
  return state.turnEvidence.some((item) => {
    if (!item.ok) return false;
    const tool = item.tool.toLowerCase();
    return /test|quality|verify|build|compile|lint/.test(tool);
  });
}

export function getExecutionStage(state: ProjectPanelState): FlowStage {
  if (isFlowStage(state.executionStage)) return state.executionStage;
  if (!state.projectId || !hasOpenTasks(state)) return state.projectId ? "release" : "requirements";
  if (state.planStatus === "draft" || state.planStatus === "plan_dirty") return "requirements";
  if (state.turnEvidence.length > 0 && !state.turnInProgress) return "verification";
  if (state.turnInProgress || state.turnArmedId) return "implementation";
  return "design";
}

function getWorkflowStage(state: ProjectPanelState): FlowStage {
  return state.workflowStage || getExecutionStage(state);
}

const USER_STAGE_LABELS: Record<FlowStage, string> = {
  requirements: "了解需求",
  documentation: "整理项目文档",
  design: "准备项目方案",
  implementation: "实现当前任务",
  verification: "验证交付结果",
  release: "准备发布检查",
};

const USER_BLOCKER_LABELS: Record<string, string> = {
  "SCOPE.md": "项目范围",
  "PROJECT.md": "项目说明",
  "DESIGN.md": "项目方案",
  "TECH-DESIGN.md": "技术方案",
  "TASKS.md": "任务清单",
  "VERIFY.md": "验证计划",
  "RELEASE.md": "发布检查",
  REQ: "需求目标",
  AC: "验收标准",
  plan_status: "方案确认",
  review: "交付检查",
  review_blockers: "交付检查中的问题",
};

const USER_BLOCKER_REASON_LABELS: Record<string, string> = {
  documentation_in_progress: "项目文档正在整理中，当前还没有进入实现阶段。",
  design_not_confirmed: "项目设计尚未完成，当前还不能进入实现阶段。",
  plan_not_confirmed: "项目方案尚未确认，当前还不能开始实现。",
  scope_incomplete: "项目范围或验收标准还不完整。",
  no_executable_tasks: "当前没有满足依赖条件的可执行任务。",
  manifest_unavailable: "项目状态清单暂时不可用，无法安全继续。",
  l2_stale: "项目文档与当前变更不一致，需要先更新受影响内容。",
  review_blocked: "交付检查没有通过，仍有问题需要处理。",
};

export interface ProjectBlockerDetails {
  reason: string;
  impact: string;
  attempted: string;
  nextStep: string;
}

function userFacingBlockerReason(reason: string): string {
  const normalized = reason.trim();
  if (!normalized) return "当前阶段出口尚未满足，系统已暂停继续推进。";
  return USER_BLOCKER_REASON_LABELS[normalized]
    || (normalized.includes("documentation_in_progress")
      ? USER_BLOCKER_REASON_LABELS.documentation_in_progress
      : normalized.includes("design_not_confirmed")
        ? USER_BLOCKER_REASON_LABELS.design_not_confirmed
        : normalized);
}

function userFacingBlockerSummary(state: ProjectPanelState): string {
  if (state.reviewProgressBlocked && state.executionStageBlockers.length === 0) {
    return USER_BLOCKER_REASON_LABELS.review_blocked;
  }
  if (state.executionStageReason.trim()) {
    return userFacingBlockerReason(state.executionStageReason);
  }
  const labels = Array.from(new Set(
    state.executionStageBlockers.map((item) => USER_BLOCKER_LABELS[item] || item)
      .filter(Boolean),
  ));
  return labels.length > 0
    ? "还需要处理：" + labels.join("、")
    : "当前还不能继续，请查看原因。";
}

function hasActualProjectBlocker(state: ProjectPanelState): boolean {
  if (state.reviewProgressBlocked) return true;
  if (state.runawayEnabled) {
    if (isRunawayV2(state)) return state.runawayBlocked;
    return state.runawayCheckpoint === "paused" || Boolean(state.runawayPausedReason?.trim());
  }
  return state.executionStageStatus === "blocked";
}

export function projectBlockerDetails(state: ProjectPanelState): ProjectBlockerDetails {
  const reviewBlocked = state.reviewProgressBlocked;
  const v2Block = isRunawayV2(state) ? state.runawayBlock : null;
  const reason = reviewBlocked && !state.executionStageReason.trim()
    ? USER_BLOCKER_REASON_LABELS.review_blocked
    : v2Block?.reason?.trim()
      || userFacingBlockerReason(
        state.runawayPausedReason?.trim() || state.executionStageReason.trim(),
      );
  const stageLabel = USER_STAGE_LABELS[getExecutionStage(state)] || "当前阶段";
  const blockerLabels = Array.from(new Set(
    [...state.executionStageBlockers, ...state.executionStageMissing]
      .map((item) => USER_BLOCKER_LABELS[item] || item)
      .filter(Boolean),
  ));
  const impact = reviewBlocked
    ? "项目不会进入下一阶段，也不会被标记为已完成。"
    : `当前停在「${stageLabel}」阶段${blockerLabels.length ? `，待处理项：${blockerLabels.join("、")}` : ""}。`;
  const attempted = state.runawayEnabled
    ? RUNAWAY_BLOCKER.pausedImpact
    : "已停在当前阶段出口，没有绕过前置条件继续写入。";
  const nextStep = state.runawayEnabled
    ? (
      isRunawayV2(state)
        ? RUNAWAY_BLOCKER.pausedNextV2
        : state.runawayCheckpoint === "paused"
          ? RUNAWAY_BLOCKER.pausedNextLegacy
          : "处理上述问题后，再重新开启狂奔；如需人工判断，请直接在聊天中说明处理方式。"
    )
    : RUNAWAY_BLOCKER.pausedNextOff;
  return { reason, impact, attempted, nextStep };
}

export function hasProjectBlocker(state: ProjectPanelState): boolean {
  return hasActualProjectBlocker(state);
}

export function renderProjectBlockerDetails(state: ProjectPanelState): string {
  const details = projectBlockerDetails(state);
  const pausedReason = state.runawayPausedReason?.trim() || "";
  const canResumeRunaway = state.runawayEnabled && (
    isRunawayV2(state) ? state.runawayBlocked : state.runawayCheckpoint === "paused"
  );
  const canDirectedRetry = isRunawayV2(state) && state.runawayBlocked;
  const canResumeDocumentation = !isRunawayV2(state) && canResumeRunaway
    && (
      pausedReason === "文档尚未达到设计确认标准"
      || state.executionStageReason.trim() === "documentation_in_progress"
    );
  const resumeLabel = canResumeDocumentation
    ? RUNAWAY_BLOCKER.resumeDocumentation
    : RUNAWAY_BLOCKER.resumeDefault;
  const v2Block = state.runawayBlock;
  const tailBlock = v2Block?.tail?.trim()
    ? `<pre class="unified-blocker-tail">${escapeHtml(v2Block.tail.trim())}</pre>`
    : "";
  const commandLine = v2Block?.command?.trim()
    ? `<p><strong>命令</strong>：<code>${escapeHtml(v2Block.command.trim())}</code></p>`
    : "";
  const itemLine = v2Block?.itemId
    ? `<p><strong>验收项</strong>：${escapeHtml(v2Block.itemId)}</p>`
    : "";
  return `<section class="unified-surface unified-blocker-details" aria-label="阻塞原因">
    <div class="unified-expand-title">为什么停在这里</div>
    ${itemLine}
    <p><strong>原因</strong>：${escapeHtml(details.reason)}</p>
    ${commandLine}
    ${tailBlock}
    <p><strong>影响</strong>：${escapeHtml(details.impact)}</p>
    <p><strong>已处理</strong>：${escapeHtml(details.attempted)}</p>
    <p><strong>下一步</strong>：${escapeHtml(details.nextStep)}</p>
    <div class="unified-expand-actions">
      ${canResumeRunaway ? `<button type="button" class="unified-btn unified-btn-accent" data-action="resume-runaway">${escapeHtml(resumeLabel)}</button>` : ""}
      ${canDirectedRetry ? '<button type="button" class="unified-btn" data-action="directed-runaway-retry">定向再试</button>' : ""}
      <button type="button" class="unified-btn" data-action="blocker-details-close">关闭详情</button>
    </div>
  </section>`;
}

function isRunawayActive(state: ProjectPanelState): boolean {
  if (!state.runawayEnabled) return false;
  if (hasActualProjectBlocker(state) || state.milestoneAccepted) return false;
  if (isRunawayV2(state)) {
    return !state.runawayBlocked && state.runawayPhase !== "release_wait" && state.runawayPhase !== "human";
  }
  return ["idle", "preparing", "implementing", "verifying", "repairing", "release_wait"].includes(
    state.runawayCheckpoint,
  ) && state.runawayCheckpoint !== "release_wait";
}

function userFacingTaskGroupTitle(title: string): string {
  const cleaned = title.replace(/^Phase\s+\d+[a-z]?\s*[·—:-]?\s*/i, "").trim();
  return cleaned || "任务";
}

function firstProjectGoalTask(state: ProjectPanelState): string {
  return state.turnArmedText.trim()
    || state.nextTask?.trim()
    || state.taskPhases
      .flatMap((phase) => phase.tasks)
      .find((task) => !task.done)?.text
    || "当前项目";
}

function compactGoalText(text: string, max = 180): string {
  const plain = text
    .replace(/[`*_#>\-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return truncateSummary(plain, max);
}

function projectTaskProgressLabel(state: ProjectPanelState): string {
  if (state.runawayEnabled) {
    if (hasActualProjectBlocker(state)) return "等待处理";
    if (isRunawayV2(state)) {
      if (state.runawayPhase === "release_wait") return "交付检查";
      if (state.runawayPhase === "implement") return "正在实现";
      if (state.runawayPhase === "verify") return "正在验证";
      if (state.runawayPhase === "prepare") return "正在准备";
      if (state.runawayUserLine) return state.runawayUserLine;
    }
    if (state.tasksTotal > 0 && state.tasksAllDone) return "交付检查";
    if (state.runawayCheckpoint === "verifying") return "正在验证";
    if (state.runawayCheckpoint === "repairing") return "正在修复";
    if (state.runawayCheckpoint === "implementing") return "正在实现";
    if (state.runawayCheckpoint === "preparing" || state.runawayCheckpoint === "idle") return "正在准备";
  }
  if (state.tasksTotal > 0 && state.tasksAllDone) return "交付检查";
  if (getExecutionStage(state) === "documentation") return "正在整理资料";
  if (state.tasksTotal > 0) return "项目进行中";
  return "等待开始";
}

function decisionCopy(view: ProjectGoalViewModel): { detail: string; impact: string } {
  if (view.action === "confirm-scope") {
    return {
      detail: "确认内容：第一轮交付范围",
      impact: "影响：确认后进入设计阶段",
    };
  }
  if (view.action === "confirm-plan") {
    return {
      detail: "确认内容：当前项目方案",
      impact: view.secondaryAction === "direct-implement"
        ? "影响：规划后开工进入实现；直接实现跳过计划搭档立刻写代码"
        : "影响：确认后进入当前任务的实现",
    };
  }
  if (view.action === "confirm-design") {
    return {
      detail: "确认内容：四个核心项目文档与设计基线",
      impact: "影响：确认后开放任务授权，进入设计阶段",
    };
  }
  if (view.action === "open-plan-review") {
    return {
      detail: `确认内容：${compactGoalText(view.summary, 120)}`,
      impact: "影响：采纳后写入项目计划并继续当前流程",
    };
  }
  if (view.action === "open-full-plan" && view.title === "选择下一条任务") {
    return {
      detail: "操作：从开放任务队列中选择一项",
      impact: "影响：开始实现后进入当前任务阶段",
    };
  }
  if (view.status === "blocked") {
    return {
      detail: `原因：${compactGoalText(view.summary, 140)}`,
      impact: `影响：${view.nextStep}`,
    };
  }
  if (view.status === "failed") {
    return {
      detail: `原因：${compactGoalText(view.summary, 140)}`,
      impact: `影响：${view.nextStep}`,
    };
  }
  return {
    detail: compactGoalText(view.summary, 140),
    impact: view.nextStep,
  };
}

export function deriveProjectGoalViewModel(state: ProjectPanelState): ProjectGoalViewModel {
  if (!state.projectId) {
    return {
      status: "empty",
      statusLabel: "未开始",
      title: "选择或新建项目",
      summary: "绑定项目后，系统会把目标、文档、任务和验证串成一条工作链路。",
      nextStep: "先选择一个项目，或创建新项目。",
      action: "open-projects",
      actionLabel: "打开项目",
    };
  }

  if (state.switchInProgress) {
    return {
      status: "processing",
      statusLabel: "切换中",
      title: "正在加载项目",
      summary: `正在加载项目 ${state.projectId} 的目标和任务。`,
      nextStep: "项目状态加载完成后即可继续。",
      action: null,
      actionLabel: null,
    };
  }

  const goal = compactGoalText(state.projectSummary) || compactGoalText(firstProjectGoalTask(state));
  const executionStage = getExecutionStage(state);
  const stageLabel = USER_STAGE_LABELS[executionStage];
  const blocker = userFacingBlockerSummary(state);
  if (hasActualProjectBlocker(state)) {
    const title = isRunawayV2(state) && state.runawayUserLine
      ? state.runawayUserLine
      : (state.runawayEnabled ? "需要你的处理" : "当前无法继续");
    return {
      status: "blocked",
      statusLabel: "需要处理",
      title,
      summary: blocker,
      nextStep: "查看原因，处理后再继续。",
      action: state.reviewProgressBlocked ? "jump-review-summary" : "jump-turn-process",
      actionLabel: "查看原因",
    };
  }

  if (state.runawayCancelAvailable) {
    return {
      status: "processing",
      statusLabel: "等待停止",
      title: "狂奔续接等待中",
      summary: "上一回合还占用执行通道，当前没有新的模型回合在运行。",
      nextStep: "可以立即停止这次续接等待，释放狂奔通道。",
      action: "stop-turn",
      actionLabel: "停止续接",
    };
  }

  const hasFailedEvidence = !state.runawayEnabled && state.turnEvidence.some((item) => !item.ok);
  if (
    !state.runawayEnabled && (
      state.executionStageStatus === "failed"
      || state.turnPostcondition === "fail"
      || Boolean(state.turnFailureClass)
    )
    || (hasFailedEvidence && !state.turnInProgress)
  ) {
    return {
      status: "failed",
      statusLabel: "验证未通过",
      title: "上一步没有完成",
      summary: state.turnFailureClass || "最近一次执行或验证没有通过。",
      nextStep: "查看失败原因，再决定修复、重试或调整方案。",
      action: "jump-turn-process",
      actionLabel: "查看失败原因",
    };
  }

  if (
    isRunawayV2(state)
    && state.runawayEnabled
    && !state.runawayBlocked
    && !state.turnInProgress
    && state.runawayPhase !== "release_wait"
    && state.runawayPhase !== "human"
  ) {
    const checklist = state.runawayChecklist;
    const summary = checklist && checklist.total > 0
      ? `验收 ${checklist.passed}/${checklist.total}`
      : goal;
    return {
      status: "processing",
      statusLabel: "续接中",
      title: state.runawayUserLine || "狂奔已开启",
      summary,
      nextStep: "系统会自动续接；若停在这里，点「手动续接」或发送「继续」。",
      action: "jump-turn-process",
      actionLabel: "查看过程",
      secondaryAction: "resume-runaway",
      secondaryActionLabel: "手动续接",
    };
  }

  if (state.turnInProgress || state.executionStageStatus === "in_progress") {
    const title = isRunawayV2(state) && state.runawayUserLine
      ? state.runawayUserLine
      : stageLabel;
    const summary = isRunawayV2(state) && state.runawayChecklist && state.runawayChecklist.total > 0
      ? `验收进度 ${state.runawayChecklist.passed}/${state.runawayChecklist.total}`
      : goal;
    return {
      status: "processing",
      statusLabel: "进行中",
      title,
      summary,
      nextStep: state.runawayEnabled
        ? "系统会自动继续实现、验证和修复；你可以查看过程或停止。"
        : "系统正在处理当前内容；你可以查看过程或停止。",
      action: "jump-turn-process",
      actionLabel: "查看过程",
    };
  }

  if (
    state.tasksTotal > 0
    && state.tasksAllDone
    && executionStage === "release"
    && state.milestoneAccepted
  ) {
    return {
      status: "completed",
      statusLabel: "已完成",
      title: "当前批次已完成",
      summary: goal,
      nextStep: state.milestoneAccepted ? "可以开始下一批工作。" : "查看发布检查并进行人工验收。",
      action: state.milestoneAccepted ? null : "open-full-plan",
      actionLabel: state.milestoneAccepted ? null : "查看发布检查",
    };
  }

  if (state.tasksTotal > 0 && state.tasksAllDone && executionStage === "release") {
    return {
      status: "decision",
      statusLabel: "待发布验收",
      title: "发布检查已准备好",
      summary: goal,
      nextStep: "验证和审查已经通过，请检查发布清单并确认发布；在此之前项目不会标记为完成。",
      action: "accept-milestone",
      actionLabel: "确认发布",
    };
  }

  const actionableSuggestions = state.runawayEnabled
    ? []
    : state.suggestions.filter((suggestion) => Boolean(suggestion.action));
  if (actionableSuggestions.length > 0) {
    return {
      status: "decision",
      statusLabel: "等你决定",
      title: "有一个方案需要你确认",
      summary: actionableSuggestions[0].title || goal,
      nextStep: "查看变更内容，确认或忽略这项方案。",
      action: "open-plan-review",
      actionLabel: "查看方案",
    };
  }

  if (state.tasksTotal > 0 && state.tasksAllDone) {
    return {
      status: "decision",
      statusLabel: "待验证",
      title: "交付结果待检查",
      summary: goal,
      nextStep: "先跑验收并查看验证证据；任务清空本身不代表项目完成。",
      action: "run-verify",
      actionLabel: "跑验收",
    };
  }

  if (!state.runawayEnabled && (state.planOverlay || state.planStatus === "plan_dirty" || (state.planStatus === "draft" && state.tasksTotal > 0))) {
    return {
      status: "decision",
      statusLabel: "等你决定",
      title: state.planStatus === "plan_dirty" ? "方案有变更，等你确认" : "方案已准备好",
      summary: compactGoalText(state.planOverlay?.summary || "") || goal,
      nextStep: "规划后开工走计划确认；直接实现跳过计划搭档、立刻写代码。确认前可随时切换。",
      action: "confirm-plan",
      actionLabel: "规划后开工",
      secondaryAction: "direct-implement",
      secondaryActionLabel: "直接实现",
    };
  }

  if (state.scopeNeedsReconfirm || (!state.scopeConfirmedAt && state.tasksTotal > 0)) {
    return {
      status: "decision",
      statusLabel: "等你决定",
      title: "范围需要确认",
      summary: goal,
      nextStep: "确认第一轮范围后，再继续准备方案。",
      action: "confirm-scope",
      actionLabel: "确认范围",
    };
  }

  if (state.needsDesignConfirm || state.workflowStage === "documentation") {
    return {
      status: "decision",
      statusLabel: "等你决定",
      title: "设计需要确认",
      summary: "",
      nextStep: "确认设计后，才能选择任务并开始实现。",
      action: "confirm-design",
      actionLabel: "确认设计",
    };
  }

  return {
    status: "decision",
    statusLabel: "等你决定",
    title: executionStage === "verification"
      ? "验证结果已准备好"
      : executionStage === "design" ? "选择下一条任务" : "准备开始下一步",
    summary: goal,
    nextStep: executionStage === "verification"
      ? "跑验收并查看本回合证据，确认结果是否满足验收。"
      : "选择一个开放任务，或继续补充当前目标。",
    action: executionStage === "verification" ? "run-verify" : "open-full-plan",
    actionLabel: executionStage === "verification" ? "跑验收" : "选择任务",
  };
}

function extractHeaderTaskId(state: ProjectPanelState): string {
  const raw = `${state.turnArmedId || ""} ${state.nextTask || ""}`;
  return raw.match(/\bT-\d+(?:-\d+)*\b/i)?.[0]?.toUpperCase() || "";
}

export function deriveHeaderNextStepView(state: ProjectPanelState): HeaderNextStepView {
  if (!state.projectId || state.switchInProgress || state.runawayEnabled) {
    return { kind: "none", actions: [] };
  }
  if (state.planStatus === "draft" || state.planStatus === "plan_dirty") {
    return {
      kind: "dual-draft",
      actions: [
        { action: "confirm-plan", label: "规划后开工", accent: true },
        { action: "direct-implement", label: "直接实现" },
      ],
    };
  }
  const stage = getExecutionStage(state);
  if (state.tasksAllDone || stage === "verification") {
    return { kind: "single", actions: [{ action: "run-verify", label: "跑验收", accent: true }] };
  }
  const taskId = extractHeaderTaskId(state);
  if (stage === "implementation" && taskId) {
    return {
      kind: "single",
      actions: [{ action: "start-task", label: "开始任务", accent: true, taskId }],
    };
  }
  if (stage === "release" && !state.milestoneAccepted) {
    return { kind: "single", actions: [{ action: "accept-milestone", label: "确认发布", accent: true }] };
  }
  const view = deriveProjectGoalViewModel(state);
  if (view.action && view.actionLabel) {
    return {
      kind: "single",
      actions: [{ action: view.action, label: view.actionLabel, accent: true }],
    };
  }
  return { kind: "none", actions: [] };
}

export function renderHeaderNextStepCtas(actions: HeaderNextStepAction[]): string {
  if (!actions.length) return "";
  return `<div class="unified-header-ctas">${actions.map((item) => {
    const accent = item.accent ? " unified-btn-accent" : "";
    const task = item.taskId ? ` data-task-id="${escapeHtml(item.taskId)}"` : "";
    return `<button type="button" class="unified-btn${accent} unified-header-cta" data-action="${escapeHtml(item.action)}"${task}>${escapeHtml(item.label)}</button>`;
  }).join("")}</div>`;
}

export function renderProjectGoalCard(state: ProjectPanelState): string {
  const view = deriveProjectGoalViewModel(state);
  const header = deriveHeaderNextStepView(state);
  const contextGoal = compactGoalText(state.projectSummary)
    || compactGoalText(firstProjectGoalTask(state))
    || view.title;
  const isDecision = view.status === "decision" || view.status === "blocked" || view.status === "failed";
  const headerCtas = renderHeaderNextStepCtas(header.actions);
  const contextAction = headerCtas
    || ((!state.runawayEnabled || state.runawayCancelAvailable)
      && !isDecision && view.action && view.actionLabel
      ? `<button type="button" class="unified-btn unified-context-action" data-action="${escapeHtml(view.action)}">${escapeHtml(view.actionLabel)}</button>`
      : "");
  const decision = isDecision
      ? (() => {
        const copy = decisionCopy(view);
        const detailAction = view.action === "confirm-scope"
          ? `<button type="button" class="unified-btn" data-action="open-scope-doc">查看范围</button>`
          : "";
        const stripCtas = header.kind === "dual-draft"
          ? renderHeaderNextStepCtas(header.actions)
          : view.action && view.actionLabel
            ? `<button type="button" class="unified-btn unified-btn-accent" data-action="${escapeHtml(view.action)}">${escapeHtml(view.actionLabel)}</button>`
              + (view.secondaryAction && view.secondaryActionLabel
                ? `<button type="button" class="unified-btn" data-action="${escapeHtml(view.secondaryAction)}">${escapeHtml(view.secondaryActionLabel)}</button>`
                : "")
            : "";
        return `<section class="unified-decision-strip is-${escapeHtml(view.status)}" aria-label="需要处理的事项">
          <div class="unified-decision-copy">
            <strong class="unified-decision-title">${escapeHtml(view.title)}</strong>
            <span>${escapeHtml(copy.detail)}</span>
            <span>${escapeHtml(copy.impact)}</span>
          </div>
          <div class="unified-decision-actions">${detailAction}${stripCtas}</div>
        </section>`;
      })()
    : "";
  return `<div class="unified-context-bar" data-goal-status="${escapeHtml(view.status)}">
    <div class="unified-context-main">
      <strong class="unified-context-project" title="${escapeHtml(state.projectId)}">${escapeHtml(state.projectId)}</strong>
      <span class="unified-context-separator" aria-hidden="true">·</span>
      <span class="unified-context-goal" title="${escapeHtml(contextGoal)}">${escapeHtml(contextGoal)}</span>
      <span class="unified-context-progress">${escapeHtml(projectTaskProgressLabel(state))}</span>
      <span class="unified-context-status is-${escapeHtml(view.status)}">${escapeHtml(view.statusLabel)}</span>
      ${contextAction}
    </div>
  </div>${decision}`;
}

function renderDetailedFlowRail(state: ProjectPanelState): string {
  const execution = getExecutionStage(state);
  const preview = state.flowPreviewStage;
  const current = preview || execution;
  const executionIndex = FLOW_STAGES.findIndex((stage) => stage.id === execution);
  return `<div class="textbook-flow" aria-label="教科书流程">
    <div class="textbook-flow-header">
      <span class="textbook-flow-title">项目流程</span>
      ${preview ? `<button type="button" class="unified-btn textbook-flow-return" data-action="flow-return">回到当前阶段</button>` : `<span class="textbook-flow-current">当前：${escapeHtml(FLOW_STAGES[executionIndex]?.label || "需求")}</span>`}
    </div>
    <div class="textbook-flow-rail">
      ${FLOW_STAGES.map((stage, index) => {
        const active = stage.id === execution ? " is-execution" : "";
        const selected = stage.id === current ? " is-selected" : "";
        const past = index < executionIndex ? " is-past" : "";
        return `<button type="button" class="textbook-flow-step${active}${selected}${past}" data-flow-stage="${stage.id}" aria-current="${stage.id === current ? "step" : "false"}">
          <span class="textbook-flow-dot"></span><span>${escapeHtml(stage.label)}</span>
        </button>`;
      }).join('<span class="textbook-flow-connector" aria-hidden="true"></span>')}
    </div>
  </div>`;
}

const STAGE_ARTIFACTS: Record<FlowStage, string[]> = {
  requirements: ["PROJECT.md", "SCOPE.md"],
  documentation: ["PROJECT.md", "DESIGN.md", "TASKS.md", "VERIFY.md"],
  design: ["SCOPE.md", "DESIGN.md", "TECH-DESIGN.md"],
  implementation: ["SCOPE.md", "DESIGN.md", "TECH-DESIGN.md", "TASKS.md"],
  verification: ["TASKS.md", "VERIFY.md"],
  release: ["VERIFY.md", "RELEASE.md"],
};

function artifactByPath(state: ProjectPanelState): Map<string, ProjectArtifactSummary> {
  return new Map(state.executionStageArtifacts.map((artifact) => [artifact.path, artifact]));
}

function taskAssociationBasis(state: ProjectPanelState): {
  id: string;
  req: string[];
  ac: string[];
  design: string[];
  verify: string[];
  evidence: string[];
} {
  const lines = state.tasksMarkdown.split(/\r?\n/);
  const targetId = (state.turnArmedId || state.nextTask || "").match(/\bT-\d+(?:-\d+)*\b/i)?.[0]?.toUpperCase() || "";
  let start = -1;
  let end = lines.length;
  for (let index = 0; index < lines.length; index += 1) {
    if (!/^\s*-\s*\[[ xX]\]\s+/.test(lines[index])) continue;
    const id = lines[index].match(/\bT-\d+(?:-\d+)*\b/i)?.[0]?.toUpperCase() || "";
    if (start < 0 && (!targetId || id === targetId)) start = index;
    else if (start >= 0) {
      end = index;
      break;
    }
  }
  if (start < 0) return { id: targetId, req: [], ac: [], design: [], verify: [], evidence: [] };
  const block = lines.slice(start, end).join("\n");
  const read = (key: string): string[] => {
    const match = block.match(new RegExp(`(?:^|\\s)${key}\\s*:\\s*([^\\n;|]+)`, "i"));
    if (!match) return [];
    return match[1].split(",").map((item) => item.trim()).filter(Boolean);
  };
  return {
    id: block.match(/\bT-\d+(?:-\d+)*\b/i)?.[0]?.toUpperCase() || targetId,
    req: read("req"),
    ac: read("ac"),
    design: read("design"),
    verify: read("verify"),
    evidence: read("evidence"),
  };
}

function taskMappingCompleteness(state: ProjectPanelState): string {
  const blocks = state.tasksMarkdown
    .split(/\r?\n(?=\s*-\s*\[[ xX]\]\s+)/)
    .filter((block) => /^\s*-\s*\[[ xX]\]\s+/.test(block));
  const mapped = blocks.filter((block) => /(?:^|\s)ac\s*:/i.test(block) && /(?:^|\s)design\s*:/i.test(block)).length;
  return `${mapped}/${blocks.length}`;
}

function renderStageArtifactSummary(state: ProjectPanelState, stage: FlowStage): string {
  const artifacts = artifactByPath(state);
  const rows = STAGE_ARTIFACTS[stage].map((path) => {
    const artifact = artifacts.get(path);
    if (!artifact) {
      return `<span class="textbook-artifact-row is-missing"><code>${escapeHtml(path)}</code><span>未接入</span></span>`;
    }
    const status = artifact.status || "unknown";
    const completeness = artifact.completeness || "unknown";
    return `<button type="button" class="textbook-artifact-row" data-action="open-artifact-doc" data-artifact-path="${escapeHtml(artifact.path)}"><code>${escapeHtml(artifact.path)}</code><span>${escapeHtml(artifact.role)} · ${escapeHtml(artifact.revision)} · <b class="textbook-artifact-status is-${escapeHtml(status)}">${escapeHtml(status)}</b> · <b class="textbook-artifact-completeness is-${escapeHtml(completeness)}">${escapeHtml(completeness)}</b></span></button>`;
  }).join("");
  return `<div class="textbook-artifacts"><div class="textbook-section-label">阶段制品</div><div class="textbook-artifact-list">${rows}</div></div>`;
}

function renderStageBasis(state: ProjectPanelState, stage: FlowStage): string {
  const artifacts = artifactByPath(state);
  const revision = (path: string): string => artifacts.get(path)?.revision || "—";
  const task = taskAssociationBasis(state);
  let lines: string[];
  switch (stage) {
    case "requirements": {
      const scopeIds = artifacts.get("SCOPE.md")?.ids?.filter((id) => /^AC-/i.test(id)) || [];
      lines = [`AC 覆盖：${scopeIds.join(", ") || task.ac.join(", ") || "待补充"}`];
      break;
    }
    case "design":
      lines = [`基线：DESIGN@${revision("DESIGN.md")} · TECH-DESIGN@${revision("TECH-DESIGN.md")}`, `映射完整度：${taskMappingCompleteness(state)} · 当前：${task.design.join(", ") || "待从 TASKS 关联"}`];
      break;
    case "implementation":
      lines = [`编码依据：DESIGN@${revision("DESIGN.md")} · SCOPE@${revision("SCOPE.md")}`, `AC：${task.ac.join(", ") || "当前任务未声明"}${task.id ? ` · ${task.id}` : ""}`];
      break;
    case "verification":
      lines = [`矩阵：VERIFY@${revision("VERIFY.md")} · V：${task.verify.join(", ") || "待补充"}`, `证据新鲜度：${state.turnEvidence.length ? `${state.turnEvidence.filter((item) => item.ok).length}/${state.turnEvidence.length} 本回合成功` : "本回合暂无"}`];
      break;
    case "release":
      lines = [`清单：RELEASE@${revision("RELEASE.md")}`, `人工验收：${state.milestoneAccepted ? "已记录" : "待人工验收"}`];
      break;
  }
  return `<div class="textbook-stage-basis"><div class="textbook-section-label">阶段依据</div>${lines.map((line) => `<div>${escapeHtml(line)}</div>`).join("")}</div>`;
}

function renderDetailedStagePlanCard(state: ProjectPanelState, callbacks?: ProjectPanelCallbacks): string {
  const execution = getExecutionStage(state);
  const stage = state.flowPreviewStage || execution;
  const openCount = Math.max(0, state.tasksTotal - state.tasksDone);
  const currentTask = state.turnArmedText || state.nextTask || "暂无已武装任务";
  const evidence = state.turnEvidence.length
    ? `${state.turnEvidence.filter((item) => item.ok).length}/${state.turnEvidence.length} 项本回合工具证据成功`
    : "本回合尚无工具证据";
  let body = "";
  let action = "";
  switch (stage) {
    case "requirements":
      if (!state.projectId) {
        body = `<div class="textbook-plan-goal">请先打开或新建项目</div><div class="textbook-plan-stat">绑定项目后才能确认范围并进入文档整理。</div>`;
        action = `<button type="button" class="unified-btn" data-action="open-projects">打开项目</button><button type="button" class="unified-btn unified-btn-accent" data-action="new-project">新建项目</button>`;
      } else {
        body = `<div class="textbook-plan-goal">项目 ${escapeHtml(state.projectId)}：先让第一轮有可执行交付物。</div><div class="textbook-plan-stat">开放任务 ${openCount} 条${state.scopeNeedsReconfirm ? " · 范围已变" : ""}</div>`;
        action = state.runawayEnabled
          ? `<span class="textbook-soft-signal">${RUNAWAY_STAGE.preparingProject}</span>`
          : state.scopeConfirmedAt
          ? `<span class="textbook-soft-signal">范围已确认</span>`
          : `<button type="button" class="unified-btn unified-btn-accent" data-action="confirm-scope" ${openCount === 0 ? "disabled" : ""}>确认范围</button>`;
      }
      break;
    case "design":
      body = state.runawayEnabled
        ? `<div class="textbook-plan-goal">${RUNAWAY_STAGE.preparingTasks}</div><div class="textbook-plan-stat">计划提案由狂奔授权自动采纳</div>`
        : `<div class="textbook-plan-goal">方案已确定后，可以开始下一项工作。</div><div class="textbook-plan-stat">还有待处理的方案变更</div>`;
      action = state.runawayEnabled
        ? `<span class="textbook-soft-signal">${RUNAWAY_STAGE.enteringImplementation}</span>`
        : state.suggestions.some((suggestion) => Boolean(suggestion.action))
        ? `<button type="button" class="unified-btn unified-btn-accent" data-action="open-plan-review">查看变更</button>`
        : `<span class="textbook-soft-signal">可进入实现</span>`;
      break;
    case "implementation":
      body = `<div class="textbook-plan-goal">当前武装任务</div><div class="textbook-plan-task">${escapeHtml(currentTask)}</div><div class="textbook-plan-stat">Gate 证据：${escapeHtml(evidence)}</div>`;
      action = state.planStatus === "plan_dirty"
        ? `<button type="button" class="unified-btn unified-btn-accent" data-action="stop-turn">一键停止并再确认计划</button>`
        : `<span class="textbook-soft-signal">完成依靠 report_progress + Gate</span>`;
      break;
    case "verification":
      body = `<div class="textbook-plan-goal">检查本项或本 Phase 的源-L1 证据。</div><div class="textbook-plan-stat">${escapeHtml(evidence)}${hasQualityEvidence(state) ? " · 已检测到验证类工具" : " · 等待验证类工具"}</div>`;
      action = `<span class="textbook-soft-signal ${hasQualityEvidence(state) ? "is-good" : "is-warn"}">${hasQualityEvidence(state) ? "验证证据已到" : "验证尚未闭合"}</span>`;
      break;
    case "release":
      body = `<div class="textbook-plan-goal">milestone 发布清单</div><div class="textbook-plan-stat">${state.milestoneAccepted ? "已由人验收" : "需人点验收；不自动关闭"}</div><div class="textbook-release-list"><span>☑ 开放任务清空</span><span>${hasQualityEvidence(state) ? "☑" : "☐"} 源-L1 近期绿</span><span>${state.partnerNotices.length === 0 && state.reviewBlockersCount === 0 ? "☑" : "☐"} blocker 已处理</span><span>☐ 建议 git 快照（不自动提交）</span><span>${state.milestoneAccepted ? "☑" : "☐"} 人点本 milestone 验收</span></div>`;
      action = state.milestoneAccepted
        ? `<span class="textbook-soft-signal is-good">milestone 已验收</span>`
        : `<button type="button" class="unified-btn unified-btn-accent" data-action="accept-milestone">本 milestone 验收</button>`;
      break;
  }
  const renderDocumentGroup = (label: string, items: string[], tone: string): string => items.length
    ? `<div class="textbook-stage-group is-${tone}"><div class="textbook-section-label">${label}</div><div>${items.map((item) => `<span class="textbook-missing-chip">${escapeHtml(item)}</span>`).join("")}</div></div>`
    : "";
  const actualBlocker = hasActualProjectBlocker(state);
  const blockerSummary = actualBlocker
    ? userFacingBlockerSummary(state)
    : state.executionStageStatus === "in_progress"
      ? "文档整理进行中；未生成文档属于进度，不是阻塞"
      : "当前没有需要你处理的问题";
  const documentGroups = [
    actualBlocker ? renderDocumentGroup("需要处理", state.executionStageBlockers, "blocker") : "",
    renderDocumentGroup("本次变更受影响", state.executionStageAffected, "affected"),
    renderDocumentGroup("后续阶段待完善", state.executionStageDeferred, "deferred"),
    renderDocumentGroup("待完善建议", state.executionStageWarnings, "deferred"),
  ].filter(Boolean);
  const displayStageStatus = actualBlocker ? "blocked" : state.executionStageStatus === "in_progress" ? "in_progress" : "ready";
  const missing = `<div class="textbook-stage-findings"><div class="textbook-stage-status is-${escapeHtml(displayStageStatus)}">${escapeHtml(blockerSummary)}</div>${documentGroups.join("")}${documentGroups.length ? `<div class="textbook-soft-signal">只处理当前阶段与本次变更包的直接影响，不递归补齐全项目文档。</div>` : ""}</div>`;
  body += missing + renderStageArtifactSummary(state, stage) + renderStageBasis(state, stage);
  return `<section class="textbook-plan-card" aria-label="阶段计划卡"><div class="textbook-plan-card-header"><span>阶段计划 · ${escapeHtml(FLOW_STAGES.find((item) => item.id === stage)?.label || "需求")}</span>${stage !== execution ? `<span class="textbook-preview-badge">预览中</span>` : ""}</div><div class="textbook-plan-card-body">${body}</div><div class="textbook-plan-card-actions">${action}</div></section>`;
}

function renderStagePlanCard(state: ProjectPanelState, callbacks?: ProjectPanelCallbacks): string {
  void callbacks;
  const stage = getExecutionStage(state);
  const currentTask = state.turnArmedText || state.nextTask || "当前没有正在处理的任务";
  const reviewPassed = state.reviewVerdict === "pass" && state.reviewBlockersCount === 0;
  const runawayStatus = state.runawayEnabled && state.runawayStatus
    ? `<div class="textbook-runaway-status">狂奔：${escapeHtml(state.runawayStatus)}</div>`
    : "";
  let goal = "系统会在这里告诉你当前进展。";
  let stat = "";
  let action = "";
  switch (stage) {
    case "requirements":
      goal = state.projectId ? "先确认这一轮要做什么。" : "先打开或新建一个项目。";
      stat = state.projectId ? "先确认这一轮的范围。" : "绑定项目后才能继续。";
      action = !state.projectId
        ? '<button type="button" class="unified-btn unified-btn-accent" data-action="open-projects">打开项目</button>'
        : state.scopeConfirmedAt
          ? '<span class="textbook-soft-signal">范围已确认</span>'
          : '<button type="button" class="unified-btn unified-btn-accent" data-action="confirm-scope">确认范围</button>';
      break;
    case "documentation":
      goal = "系统正在整理项目资料。";
      stat = "资料准备好后，会请你确认方案。";
      break;
    case "design":
      goal = !state.runawayEnabled && state.suggestions.some((suggestion) => Boolean(suggestion.action))
        ? "方案有一项变更需要你查看。"
        : state.runawayEnabled ? RUNAWAY_STAGE.preparingTasks : "方案已经准备好，可以选择一项任务开始。";
      stat = "方案准备好后会自动进入实现。";
      action = !state.runawayEnabled && state.suggestions.some((suggestion) => Boolean(suggestion.action))
        ? '<button type="button" class="unified-btn unified-btn-accent" data-action="open-plan-review">查看方案</button>'
        : state.runawayEnabled
          ? `<span class="textbook-soft-signal">${RUNAWAY_STAGE.autoEnterImplementation}</span>`
          : '<button type="button" class="unified-btn unified-btn-accent" data-action="open-full-plan">选择任务</button>';
      break;
    case "implementation":
      goal = "系统正在处理当前任务。";
      stat = currentTask;
      action = state.planStatus === "plan_dirty"
        ? '<button type="button" class="unified-btn unified-btn-accent" data-action="stop-turn">暂停并查看变更</button>'
        : '<span class="textbook-soft-signal">完成后会展示结果和验证入口</span>';
      break;
    case "verification":
      goal = "任务已经收口，系统正在检查交付结果。";
      stat = state.turnEvidence.length > 0 ? "验证结果已经产生，等待你查看。" : "正在准备验证结果。";
      action = '<button type="button" class="unified-btn unified-btn-accent" data-action="jump-turn-process">查看验证结果</button>';
      break;
    case "release":
      goal = state.milestoneAccepted ? "发布确认已经记录。" : "交付结果已经准备好，请完成发布前检查。";
      stat = state.milestoneAccepted ? "可以开始下一轮工作。" : "项目在确认发布前不会标记为完成。";
      action = state.milestoneAccepted
        ? '<span class="textbook-soft-signal is-good">发布已确认</span>'
        : '<button type="button" class="unified-btn unified-btn-accent" data-action="accept-milestone">确认发布</button>';
      break;
  }
  const checklist = stage === "release"
    ? '<div class="textbook-release-list">'
      + '<span>☑ 任务已完成</span>'
      + '<span>' + (state.turnEvidence.length > 0 ? "☑" : "☐") + ' 验证结果</span>'
      + '<span>' + (reviewPassed ? "☑" : "☐") + ' 交付检查</span>'
      + '<span>' + (state.milestoneAccepted ? "☑" : "☐") + ' 人工确认发布</span>'
      + "</div>"
    : "";
  const blocker = hasActualProjectBlocker(state)
    ? '<div class="textbook-stage-findings"><div class="textbook-stage-status is-blocked">'
      + escapeHtml(userFacingBlockerSummary(state))
      + "</div></div>"
    : "";
  return '<section class="textbook-plan-card" aria-label="当前进展">'
    + '<div class="textbook-plan-card-header"><span>当前进展</span></div>'
    + '<div class="textbook-plan-card-body">'
    + '<div class="textbook-plan-goal">' + escapeHtml(goal) + "</div>"
    + '<div class="textbook-plan-stat">' + escapeHtml(stat) + "</div>"
    + runawayStatus
    + checklist + blocker
    + '<div class="textbook-plan-card-actions">' + action + "</div></section>";
}

function renderTopSuggestionCard(
  s: PlanSuggestion,
  reviewFocusId: string | null,
  adoptPendingId: string | null,
): string {
  const isPending = adoptPendingId === s.id;
  const acceptLbl = isPending ? "采纳中…" : acceptLabel(s);
  const diffRaw =
    s.payload && typeof s.payload.diff === "string" ? s.payload.diff.trim() : "";
  const stats = diffRaw ? diffStats(diffRaw) : "";
  const statsLine = stats
    ? `<div class="sidebar-suggestion-stats">${escapeHtml(stats)}</div>`
    : "";
  const summary = truncateSummary(s.body, 80);
  const whatChanged = s.action && s.action !== "toggle_task"
    ? `<div class="sidebar-suggestion-change"><span>相对上一版：</span>${escapeHtml(truncateSummary(suggestionWhatChanged(s), 120))}</div>`
    : "";
  const focused = reviewFocusId === s.id ? " is-review-focus" : "";
  return `<div class="sidebar-suggestion-card is-top${focused}" data-suggestion-id="${escapeHtml(s.id)}">
    <div class="sidebar-suggestion-title">${escapeHtml(s.title)}</div>
    <div class="sidebar-suggestion-body">${escapeHtml(summary)}</div>
    ${whatChanged}
    ${statsLine}
    <div class="sidebar-suggestion-actions">
      <button type="button" class="unified-btn" data-action="open-suggestion-review" data-suggestion-id="${escapeHtml(s.id)}" aria-controls="unified-plan-review">查看</button>
      <button type="button" class="unified-btn unified-btn-accent" data-action="open-suggestion-review-new" data-suggestion-id="${escapeHtml(s.id)}" aria-controls="unified-plan-review">审阅</button>
      <button type="button" class="unified-btn unified-btn-accent" data-action="accept-suggestion" data-suggestion-id="${escapeHtml(s.id)}"${isPending ? " disabled" : ""}>${escapeHtml(acceptLbl)}</button>
      <button type="button" class="unified-btn" data-action="ignore-suggestion" data-suggestion-id="${escapeHtml(s.id)}">忽略</button>
    </div>
  </div>`;
}

function renderDropTaskChoice(state: ProjectPanelState): string {
  if (!state.dropTaskPendingId) return "";
  const suggestion = state.suggestions.find((item) => item.id === state.dropTaskPendingId);
  if (!suggestion) return "";
  const workingNotice = state.dropTaskPendingWorking
    ? `<div class="textbook-drop-warning">助手仍在执行；这会改变计划，但不会自动回滚已写代码。仍要采纳吗？</div><button type="button" class="unified-btn unified-btn-accent" data-action="confirm-drop-while-working" data-suggestion-id="${escapeHtml(suggestion.id)}">仍要改计划</button>`
    : `<div class="textbook-drop-warning">计划删除后，仓库代码不会自动删除。请选择清理出口：</div>
      <div class="textbook-drop-actions"><button type="button" class="unified-btn unified-btn-accent" data-action="drop-policy" data-policy="plan_only" data-suggestion-id="${escapeHtml(suggestion.id)}">只删计划</button><button type="button" class="unified-btn" data-action="drop-policy" data-policy="agent_cleanup" data-suggestion-id="${escapeHtml(suggestion.id)}">删计划并让 agent 清理</button><button type="button" class="unified-btn" data-action="drop-policy" data-policy="git_guide" data-suggestion-id="${escapeHtml(suggestion.id)}">我用 git / IDE</button></div>`;
  return `<div class="textbook-drop-choice"><div class="textbook-drop-choice-title">删除任务：${escapeHtml(suggestion.title)}</div>${workingNotice}</div>`;
}

/** UX-026 SP-9 — stacked proposals in sidebar body (top card + peek layers). */
function renderSuggestionStack(state: ProjectPanelState): string {
  if (state.runawayEnabled) return "";
  if (isDocumentationBatch(state)) return "";
  if (state.suggestionAdoptFlash) {
    return `<div class="sidebar-suggestion-stack is-adopt-flash">
      <div class="sidebar-suggestion-stack-flash">
        <div class="sidebar-suggestion-stack-flash-title">已采纳写入</div>
        <div class="sidebar-suggestion-stack-flash-body">${escapeHtml(state.suggestionAdoptFlash)}</div>
      </div>
    </div>`;
  }

  const actionable = state.suggestions.filter((s) => Boolean(s.action));
  if (actionable.length === 0) return "";

  const peek2 = actionable.length > 2
    ? `<div class="sidebar-suggestion-peek is-2" aria-hidden="true"></div>`
    : "";
  const peek1 = actionable.length > 1
    ? `<div class="sidebar-suggestion-peek is-1" aria-hidden="true"></div>`
    : "";

  return `<div class="sidebar-suggestion-stack">
    <div class="sidebar-suggestion-stack-title">待采纳 · ${actionable.length}</div>
    <div class="sidebar-suggestion-stack-deck">
      ${peek2}
      ${peek1}
      ${renderTopSuggestionCard(actionable[0], state.reviewFocusId, state.adoptPendingId)}
    </div>
  </div>`;
}

function renderDetailedTurnSummary(state: ProjectPanelState): string {
  const total = state.turnEvidence.length;
  const fails = state.turnEvidence.filter((e) => !e.ok).length;
  const ok = total - fails;
  let detail: string;
  if (state.turnInProgress && total === 0) {
    detail = "进行中";
  } else if (total === 0) {
    detail = "尚无工具";
  } else if (state.turnInProgress) {
    detail = `进行中 · 已 ${total} 工具`;
  } else if (fails > 0) {
    detail = `${total} 工具 · ${ok} 成功 · ${fails} 失败`;
  } else {
    detail = `${total} 工具 · 全部成功`;
  }
  const gateHint = (state.turnGateNotice || "").trim()
    ? " · 有门禁提示"
    : "";
  return `<button type="button" class="sidebar-turn-summary" data-action="jump-turn-process">
    <span class="sidebar-turn-summary-chevron">${state.turnInProgress ? "▾" : "▸"}</span>
    <span class="sidebar-turn-summary-text">本回合 · ${escapeHtml(detail)}${escapeHtml(gateHint)}</span>
    <span class="sidebar-turn-summary-jump" aria-hidden="true">›</span>
  </button>`;
}

function renderDetailedReliabilityStrip(state: ProjectPanelState): string {
  const postcondition = (state.turnPostcondition || "none").trim();
  const circuitOpen = state.turnCircuitOpen || [];
  const playbook = (state.turnPlaybookId || "").trim();
  const failureClass = (state.turnFailureClass || "").trim();
  if (postcondition === "none" && circuitOpen.length === 0 && !playbook && !failureClass) {
    return "";
  }
  const postconditionClass = postcondition === "ok" ? "is-ok" : postcondition === "fail" ? "is-fail" : "is-warn";
  const circuit = circuitOpen.length > 0
    ? `<div class="sidebar-reliability-row is-fail">熔断：${escapeHtml(circuitOpen.join("、"))}</div>`
    : "";
  const playbookText = playbook ? ` · playbook=${playbook}` : "";
  const failureText = failureClass ? ` · failure=${failureClass}` : "";
  return `<div class="sidebar-reliability" aria-label="执行可靠性">
    <div class="sidebar-reliability-row ${postconditionClass}">后置条件：${escapeHtml(postcondition)}${escapeHtml(playbookText)}${escapeHtml(failureText)}</div>
    ${circuit}
  </div>`;
}

function renderAdoptedFooterBanner(message: string): string {
  const short = message.length > 120 ? `${message.slice(0, 117)}…` : message;
  return `<div class="sidebar-change-banner sidebar-adopted-banner">
    <div class="sidebar-change-banner-title">已采纳写入</div>
    <div class="sidebar-adopted-banner-body">${escapeHtml(short)}</div>
    <button type="button" class="unified-btn" data-action="dismiss-partner-notice">关闭</button>
  </div>`;
}

function renderPartnerNotices(notices: string[], busy: boolean, actionableCount = 0): string {
  if (!busy && isAdoptedPartnerNotice(notices)) {
    return renderAdoptedNotice(notices);
  }
  // C3 / S-191: sidebar keeps short operational lines only — not Plan long-chat host.
  const lines = notices
    .map((n) => normalizePartnerNoticeLine(n))
    .filter(Boolean)
    .slice(0, 3)
    .map((n) => {
      const userText = userFacingNotice(n);
      const short = userText.length > 140 ? `${userText.slice(0, 137)}…` : userText;
      return `<div style="font-size:0.78rem;opacity:0.92;margin-top:0.2rem">${escapeHtml(short)}</div>`;
    })
    .join("");
  const title = busy ? "方案搭档 · 思考中…" : "方案搭档";
  const pendingAction = !busy && actionableCount > 0
    ? `<button type="button" class="unified-btn unified-btn-accent" data-action="open-plan-review">查看方案</button>`
    : "";
  return `<div class="sidebar-change-banner" style="border-color:var(--ma-accent);background:color-mix(in srgb, var(--ma-accent) 7%, var(--ma-surface));">
    <div class="sidebar-change-banner-title">${title}</div>
    ${lines || (busy ? `<div style="font-size:0.78rem;opacity:0.7;margin-top:0.2rem">正在理解你的话…</div>` : "")}
    ${pendingAction}
  </div>`;
}

function normalizePartnerNoticeLine(text: string): string {
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  for (const ln of lines) {
    if (ln.startsWith("@@")) continue;
    if (ln.length >= 2 && ln[0] === "-" && ln[1] === " ") continue;
    if (ln.length >= 2 && ln[0] === "+" && ln[1] === " ") continue;
    return ln;
  }
  return lines[0] ?? "";
}

function userFacingNotice(text: string): string {
  return text
    .replace(/\bPhase\s+\d+[a-z]?\b/gi, "当前批次")
    .replace(/\bM[12]\b/gi, "")
    .replace(/\bmilestone\b/gi, "发布检查")
    .replace(/\bdeliverable_review\b/gi, "交付检查")
    .replace(/\breport_progress\b/gi, "任务完成记录")
    .replace(/\bGate\b/gi, "验证检查")
    .replace(/\bblockers?\b/gi, "待处理问题")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function isAdoptedPartnerNotice(notices: string[]): boolean {
  const head = normalizePartnerNoticeLine(notices[0] ?? "");
  if (!head) return false;
  if (/待审阅|待采纳|待侧栏/.test(head)) return false;
  return /^(已采纳写入|已写入|已从归档恢复)/.test(head);
}

function renderAdoptedNotice(notices: string[]): string {
  const line = userFacingNotice(normalizePartnerNoticeLine(notices[0] ?? ""));
  return renderAdoptedFooterBanner(line);
}

function renderAutoFixNotice(notices: string[]): string {
  const lines = notices
    .map((n) => `<div style="padding:0.15rem 0;font-size:0.76rem;color:var(--ma-text);">${escapeHtml(n)}</div>`)
    .join("");
  return `<div class="sidebar-change-banner" style="border-color:#3d8b5a;background:color-mix(in srgb, #3d8b5a 8%, var(--ma-surface));">
    <div class="sidebar-change-banner-title">已自动清理</div>
    ${lines}
    <button type="button" class="unified-btn" data-action="dismiss-auto-fix" style="margin-top:0.3rem;font-size:0.72rem;">关闭</button>
  </div>`;
}

function renderNextStepChip(state: ProjectPanelState): string {
  // A7: current task lives in decision surface; chip redundant.
  void state;
  return "";
}

function renderDetailedDecisionSurface(state: ProjectPanelState, callbacks: ProjectPanelCallbacks): string {
  const currentTask =
    (state.turnArmedText || "").trim() ||
    (state.nextTask || "").trim() ||
    state.taskPhases
      .flatMap((p) => p.tasks)
      .find((t) => t.status === "current" && !t.done)?.text ||
    "";
  let html = `${renderFlowRail(state)}${renderStagePlanCard(state, callbacks)}`;
  if (state.nextTurnChangeSummary) {
    html += `<div class="textbook-next-turn-overlay"><strong>侧栏已采纳计划变更：</strong>${escapeHtml(state.nextTurnChangeSummary)}</div>`;
  }
  html += renderDropTaskChoice(state);
  html += `<div class="sidebar-decision">`;
  if (currentTask) {
    html += `<div class="sidebar-decision-current">
      <div class="sidebar-decision-label">当前</div>
      <div class="sidebar-decision-text">${escapeHtml(currentTask)}</div>
    </div>`;
  } else if (state.projectId) {
    html += `<p class="overlay-empty" style="padding:0.5rem 0;">暂无开放任务</p>`;
  } else {
    html += `<p class="overlay-empty" style="padding:0.5rem 0;">未绑定项目 · 使用「项目 新建」</p>`;
  }
  if (state.projectId && state.reviewVerdict) {
    const verdict = state.reviewVerdict.toUpperCase();
    const blockers =
      state.reviewBlockersCount > 0
        ? ` · ${state.reviewBlockersCount} blockers`
        : "";
    html += `<button type="button" class="sidebar-review-line" data-action="jump-review-summary">
      <span class="sidebar-decision-label">审查</span>
      <span class="sidebar-review-line-text">${escapeHtml(verdict)}${escapeHtml(blockers)}</span>
    </button>`;
  }
  if (state.projectId && state.deliveryProfile) {
    const profileLabel = state.deliveryProfile === "ritual" ? "ritual（严格）" : "solo（宽松）";
    html += `<div class="sidebar-profile-line">
      <span class="sidebar-decision-label">profile</span>
      <span>${escapeHtml(profileLabel)}</span>
    </div>`;
  }
  if (state.projectId) {
    html += `<button type="button" class="unified-btn" data-action="open-full-plan" style="width:100%;margin:0.35rem 0 0.15rem;font-size:0.78rem;">查看任务</button>`;
  }
  html += `</div>`;
  html += renderSuggestionStack(state);
  html += renderTurnSummary(state);
  html += renderReliabilityStrip(state);
  return html;
}

function renderReliabilityStrip(state: ProjectPanelState): string {
  const postcondition = (state.turnPostcondition || "none").trim();
  const hasFailure = postcondition === "fail"
    || state.turnCircuitOpen.length > 0
    || Boolean(state.turnFailureClass);
  if (!hasFailure) return "";
  return '<button type="button" class="sidebar-reliability is-fail" data-action="jump-turn-process">'
    + '<span class="sidebar-reliability-row is-fail">这次处理没有完成 · 查看原因</span>'
    + "</button>";
}

function renderTurnSummary(state: ProjectPanelState): string {
  const total = state.turnEvidence.length;
  const fails = state.turnEvidence.filter((item) => !item.ok).length;
  const label = state.turnInProgress
    ? "正在处理"
    : fails > 0
      ? "处理未完成"
      : total > 0
        ? "本次处理已完成"
        : "等待处理";
  return '<button type="button" class="sidebar-turn-summary" data-action="jump-turn-process">'
    + '<span class="sidebar-turn-summary-chevron">' + (state.turnInProgress ? "▾" : "▸") + "</span>"
    + '<span class="sidebar-turn-summary-text">当前处理 · ' + label + "</span>"
    + '<span class="sidebar-turn-summary-jump" aria-hidden="true">›</span></button>';
}

function sidebarOpenTasksLabel(state: ProjectPanelState): string {
  const open = Math.max(0, state.tasksTotal - state.tasksDone);
  if (state.tasksTotal === 0) return "等待开始";
  if (open === 0) return "任务已清空 · 待验证";
  return `还有 ${open} 条开放`;
}

function renderSidebarStatusCard(params: {
  ariaLabel: string;
  title: string;
  summary?: string;
  detail?: string;
  footnote?: string;
  action?: string | null;
  actionLabel?: string | null;
  secondaryAction?: string | null;
  secondaryActionLabel?: string | null;
  actionAccent?: boolean;
}): string {
  const summary = params.summary
    ? `<p class="sidebar-status-card-summary">${escapeHtml(params.summary)}</p>`
    : "";
  const detail = params.detail
    ? `<p class="sidebar-status-card-detail">${escapeHtml(params.detail)}</p>`
    : "";
  const footnote = params.footnote
    ? `<p class="sidebar-status-card-footnote">${escapeHtml(params.footnote)}</p>`
    : "";
  const primary = params.action && params.actionLabel
    ? `<button type="button" class="unified-btn${params.actionAccent ? " unified-btn-accent" : ""}" data-action="${escapeHtml(params.action)}">${escapeHtml(params.actionLabel)}</button>`
    : "";
  const secondary = params.secondaryAction && params.secondaryActionLabel
    ? `<button type="button" class="unified-btn" data-action="${escapeHtml(params.secondaryAction)}">${escapeHtml(params.secondaryActionLabel)}</button>`
    : "";
  const action = primary || secondary
    ? `<div class="sidebar-status-card-actions">${primary}${secondary}</div>`
    : "";
  return `<section class="sidebar-status-card" aria-label="${escapeHtml(params.ariaLabel)}">
    <h3 class="sidebar-status-card-title">${escapeHtml(params.title)}</h3>
    ${summary}
    ${detail}
    ${footnote}
    ${action}
  </section>`;
}

function renderDecisionSurface(state: ProjectPanelState, callbacks: ProjectPanelCallbacks): string {
  if (state.switchInProgress) {
    return renderSidebarStatusCard({
      ariaLabel: "项目切换中",
      title: `正在加载 ${state.projectId || "目标项目"}`,
      summary: "旧项目内容已暂时隐藏，等待新项目状态加载完成。",
    });
  }
  if (state.runawayEnabled) {
    const view = deriveProjectGoalViewModel(state);
    const currentTask = (state.turnArmedText || state.nextTask || "").trim();
    let summary = view.summary || "系统正在处理当前目标。";
    if (isRunawayV2(state) && state.runawayChecklist && state.runawayChecklist.total > 0) {
      const checklist = `验收 ${state.runawayChecklist.passed}/${state.runawayChecklist.total}`;
      if (!summary.includes("验收")) summary = `${summary} · ${checklist}`;
    }
    const showFootnote = view.status === "blocked" || view.status === "decision" || view.status === "failed";
    return renderSidebarStatusCard({
      ariaLabel: "自动执行进展",
      title: view.title,
      summary,
      detail: currentTask && view.status === "processing" ? currentTask : undefined,
      footnote: showFootnote ? view.nextStep : (view.status === "processing" ? view.nextStep : undefined),
      action: view.action,
      actionLabel: view.actionLabel,
      secondaryAction: view.secondaryAction,
      secondaryActionLabel: view.secondaryActionLabel,
      actionAccent: view.status !== "blocked",
    });
  }
  const currentTask =
    (state.turnArmedText || "").trim()
    || (state.nextTask || "").trim()
    || state.taskPhases
      .flatMap((phase) => phase.tasks)
      .find((task) => task.status === "current" && !task.done)?.text
    || "";
  let html = renderFlowRail(state) + renderStagePlanCard(state, callbacks);
  html += renderDropTaskChoice(state);
  if (currentTask) {
    html += renderSidebarStatusCard({
      ariaLabel: "当前任务",
      title: currentTask,
      action: state.projectId ? "open-full-plan" : null,
      actionLabel: state.projectId ? "查看任务" : null,
    });
  } else if (state.projectId) {
    html += renderSidebarStatusCard({
      ariaLabel: "当前任务",
      title: "当前没有待处理任务",
      summary: "可以从任务列表选择下一项，或继续补充当前目标。",
      action: "open-full-plan",
      actionLabel: "查看任务",
    });
  } else {
    html += renderSidebarStatusCard({
      ariaLabel: "项目",
      title: "先打开或新建一个项目",
      action: "open-projects",
      actionLabel: "打开项目",
    });
  }
  html += renderSuggestionStack(state);
  html += renderTurnSummary(state);
  html += renderReliabilityStrip(state);
  return html;
}

function renderDetailedReviewProgressBanner(state: ProjectPanelState): string {
  if (!state.reviewProgressBlocked) return "";
  const count = state.reviewBlockersCount > 0 ? ` · ${state.reviewBlockersCount} 项阻塞` : "";
  return `<div class="sidebar-change-banner" style="border-color:#d4a000;background:color-mix(in srgb, #d4a000 6%, var(--ma-surface));">
    <div class="sidebar-change-banner-title">交付检查需要处理</div>
    <div class="sidebar-change-banner-changes">交付检查未通过${escapeHtml(count)}，请先处理问题后再继续。</div>
    <button type="button" class="unified-btn" data-action="jump-review-summary" style="font-size:0.72rem;">查看原因</button>
  </div>`;
}

function planStatusLabel(state: ProjectPanelState): string {
  if (state.runawayEnabled) {
    if (isRunawayV2(state)) {
      if (state.turnInProgress) return RUNAWAY_STATUS.processing;
      if (state.runawayBlocked) return state.runawayUserLine || state.runawayStatus || "需要处理";
      if (state.runawayUserLine) return state.runawayUserLine;
      return RUNAWAY_STATUS.idle;
    }
    if (state.runawayStatus) return state.runawayStatus;
  }
  if (state.planStatus === "confirmed") {
    if (state.tasksAllDone && state.tasksTotal > 0) {
      if (state.executionStage === "release" && state.milestoneAccepted) return "项目已完成";
      return "交付检查";
    }
    return "项目进行中";
  }
  if (state.planStatus === "plan_dirty") return "方案有变更";
  return "等待方案确认";
}

function projectProgressLabel(item: ProjectListItem): string {
  if (item.tasksTotal === 0) return "等待开始";
  const open = Math.max(0, item.tasksTotal - item.tasksDone);
  if (open === 0) return "交付检查";
  return "项目进行中";
}

function projectSessionHint(item: ProjectListItem): string {
  if (item.isCurrent) return "当前";
  if (item.sessionId) return "可续接";
  return "新建会话";
}

// ---- TASKS.md parser ----

const TASK_RE = /^\s*-\s*\[([ xX])\]\s+(.*)$/;
const PHASE_RE = /^##\s+(.+)$/;

export function parseTasksMarkdown(text: string): TaskPhase[] {
  const lines = text.split("\n");
  const phases: TaskPhase[] = [];
  let currentPhase: TaskPhase = { title: "", tasks: [] };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const phaseMatch = line.match(PHASE_RE);
    if (phaseMatch) {
      if (currentPhase.title || currentPhase.tasks.length) {
        phases.push(currentPhase);
      }
      currentPhase = { title: userFacingTaskGroupTitle(phaseMatch[1].trim()), tasks: [] };
      continue;
    }

    const taskMatch = line.match(TASK_RE);
    if (taskMatch) {
      const done = taskMatch[1].toLowerCase() === "x";
      currentPhase.tasks.push({
        line: i,
        text: taskMatch[2].trim(),
        done,
        status: done ? "done" : "pending",
      });
    }
  }

  if (currentPhase.title || currentPhase.tasks.length) {
    phases.push(currentPhase);
  }

  // assign "current" to first undone
  let found = false;
  for (const phase of phases) {
    for (const task of phase.tasks) {
      if (!task.done && !found) {
        task.status = "current";
        found = true;
      }
    }
  }

  return phases;
}

// ---- change detection ----

export function captureTaskSnapshot(phases: TaskPhase[]): TaskSnapshot {
  const lines = new Set<number>();
  const lineTexts = new Map<number, string>();
  for (const phase of phases) {
    for (const task of phase.tasks) {
      lines.add(task.line);
      lineTexts.set(task.line, task.text);
    }
  }
  return { lines, lineTexts };
}

export function diffTaskPhases(old: TaskSnapshot, phases: TaskPhase[]): {
  addedLines: Set<number>;
  removedLines: Set<number>;
  changedLines: Set<number>;
} {
  const currentLines = new Set<number>();
  for (const phase of phases) {
    for (const task of phase.tasks) {
      currentLines.add(task.line);
    }
  }

  const addedLines = new Set<number>();
  const removedLines = new Set<number>();
  const changedLines = new Set<number>();

  // New lines not in old snapshot
  for (const line of currentLines) {
    if (!old.lines.has(line)) {
      addedLines.add(line);
    } else if (old.lines.has(line)) {
      // Text changed for same line?
      const oldText = old.lineTexts.get(line);
      const task = phases.flatMap((p) => p.tasks).find((t) => t.line === line);
      if (oldText && task && oldText !== task.text) {
        changedLines.add(line);
      }
    }
  }

  // Lines in old but not current
  for (const line of old.lines) {
    if (!currentLines.has(line)) {
      removedLines.add(line);
    }
  }

  return { addedLines, removedLines, changedLines };
}

// ---- task flow rendering ----

function taskIdFromText(text: string): string {
  return text.match(/\bT-\d+(?:-\d+)*\b/i)?.[0]?.toUpperCase() || "";
}

function renderReviewProgressBanner(state: ProjectPanelState): string {
  if (!state.reviewProgressBlocked) return "";
  const count = state.reviewBlockersCount > 0 ? " · " + state.reviewBlockersCount + " 项待处理" : "";
  return '<div class="sidebar-change-banner" style="border-color:#d4a000;background:color-mix(in srgb, #d4a000 6%, var(--ma-surface));">'
    + '<div class="sidebar-change-banner-title">交付检查需要处理</div>'
    + '<div class="sidebar-change-banner-changes">系统暂时不会继续推进，请先处理交付检查中的问题' + count + '。</div>'
    + '<button type="button" class="unified-btn" data-action="jump-review-summary" style="font-size:0.72rem;">查看检查结果</button>'
    + "</div>";
}

function renderFlowRail(state: ProjectPanelState): string {
  const stage = getExecutionStage(state);
  const progress = projectTaskProgressLabel(state);
  return '<div class="textbook-flow textbook-flow-summary" aria-label="当前进展">'
    + '<div class="textbook-flow-header"><span class="textbook-flow-title">当前进展</span>'
    + '<span class="textbook-flow-current">' + escapeHtml(USER_STAGE_LABELS[stage]) + '</span></div>'
    + '<div class="textbook-flow-summary-text">' + escapeHtml(progress) + '</div></div>';
}

function renderTaskFlow(state: ProjectPanelState, highlightLines: Set<number> | null = null): string {
  if (!state.taskPhases.length) {
    return `<p class="overlay-empty">TASKS.md 将显示在这里</p>`;
  }

  // A7 overlay: open items only (done items live in archive)
  let html = '<p class="overlay-empty" style="padding:0.25rem 0 0.5rem;font-size:0.72rem;">待处理任务 · 选择一项开始</p>';
  let anyOpen = false;
  for (const phase of state.taskPhases) {
    const openTasks = phase.tasks.filter((t) => !t.done);
    if (!openTasks.length) continue;
    anyOpen = true;
    html += `<div class="task-phase-header">${escapeHtml(phase.title)}</div>`;
    for (const task of openTasks) {
      const cls = ["task-item"];
      if (task.status === "current") cls.push("is-current");
      else if (task.status === "new") cls.push("is-new");
      else if (task.status === "skipped") cls.push("is-skipped");

      if (highlightLines && highlightLines.has(task.line)) {
        cls.push("is-highlighted");
      }

      const taskId = taskIdFromText(task.text);
      const startAction = getWorkflowStage(state) === "design" && taskId
        ? `<button type="button" class="unified-btn task-start-action" data-action="start-task" data-task-id="${escapeHtml(taskId)}">开始实现</button>`
        : "";
      html += `<div class="${cls.join(" ")}" data-line="${task.line}">
        <label class="task-item-select">
          <input type="checkbox" class="task-checkbox" data-line="${task.line}">
          <span class="task-text">${escapeHtml(task.text)}</span>
        </label>
        ${startAction}
      </div>`;

      if (task.subtasks && task.subtasks.length > 0) {
        for (const sub of task.subtasks) {
          const subCls = sub.done ? "task-subtask is-done" : "task-subtask";
          html += `<div class="${subCls}">
            <span class="task-subtask-dot"></span>
            <span class="task-text">${escapeHtml(sub.text)}</span>
          </div>`;
        }
      }
    }
  }
  if (!anyOpen) {
    html = '<p class="overlay-empty">暂时没有待处理任务，已完成内容可在任务详情查看。</p>';
  }

  return html;
}

export function renderPlanTaskFlow(
  state: ProjectPanelState,
  highlightLines: Set<number> | null = null,
): string {
  return renderTaskFlow(state, highlightLines);
}

// ---- overlay panel rendering ----

function renderDocsOverlay(state: ProjectPanelState): string {
  return renderDocsCatalog(state);
}

function renderProjectsOverlay(state: ProjectPanelState): string {
  // If switch confirm target is set, show confirm instead of list
  if (state.switchConfirmTarget) {
    const item = state.switchConfirmTarget;
    return `<div class="overlay-switch-confirm" id="overlay-switch-confirm">
      <div class="overlay-switch-confirm-title">切换到 · ${escapeHtml(item.id)}</div>
      <div class="overlay-switch-confirm-text">
        将恢复已有会话。当前会话已自动保存。
      </div>
      <div class="overlay-switch-confirm-actions">
        <button type="button" class="unified-btn unified-btn-accent" id="overlay-switch-confirm-btn">确认切换</button>
        <button type="button" class="unified-btn" id="overlay-switch-cancel-btn">取消</button>
      </div>
    </div>`;
  }

  let html = `<input type="text" class="overlay-search-input" id="overlay-project-search" placeholder="搜索项目…" value="${escapeHtml(state.projectSearchQuery)}">`;

  const query = state.projectSearchQuery.toLowerCase().trim();

  if (!state.projects.length) {
    html += `<p class="overlay-empty">暂无项目 · 对话中说「项目 新建 &lt;id&gt;」</p>`;
    return html;
  }

  const filtered = query
    ? state.projects.filter((p) => p.id.toLowerCase().includes(query))
    : state.projects;

  if (filtered.length === 0) {
    html += `<p class="overlay-empty">无匹配 · 「项目 新建 ${escapeHtml(query)}」</p>`;
    return html;
  }

  for (const item of filtered) {
    const current = item.id === state.projectId || item.isCurrent;
    const disabled = state.switchInProgress || current;
    html += `<button type="button" class="overlay-project-item${current ? " is-current" : ""}" data-project-id="${escapeHtml(item.id)}"${disabled ? " disabled" : ""}>
      <span class="overlay-project-item-name">${escapeHtml(item.id)}</span>
      <span class="overlay-project-item-meta">${escapeHtml(projectProgressLabel(item))} · ${escapeHtml(projectSessionHint(item))}</span>
    </button>`;
  }

  return html;
}

function renderThreadsOverlay(state: ProjectPanelState): string {
  if (!state.projectId) {
    return `<p class="overlay-empty">未绑定项目</p>`;
  }

  let html = `<div class="overlay-threads-actions">
    <button type="button" class="unified-btn unified-btn-accent" id="overlay-new-thread-btn">新开线</button>
    <button type="button" class="unified-btn" data-action="refresh-threads">刷新</button>
  </div>`;

  if (state.threadsLoading) {
    html += `<p class="overlay-empty">加载中…</p>`;
    return html;
  }

  if (!state.threads.length) {
    html += `<p class="overlay-empty">暂无会话线记录</p>`;
    return html;
  }

  for (const item of state.threads) {
    const isLive = item.sessionId === state.activeSessionId;
    const isCurrent = item.sessionId === state.currentSessionId;
    const label = isLive ? "活线" : "归档";
    const meta = item.preview || item.sessionId;
    html += `<button type="button" class="overlay-project-item overlay-thread-item${isCurrent ? " is-current" : ""}" data-thread-id="${escapeHtml(item.sessionId)}"${isCurrent ? " disabled" : ""}>
      <span class="overlay-project-item-name">${escapeHtml(item.title)} <span class="overlay-thread-tag">${label}</span></span>
      <span class="overlay-project-item-meta">${escapeHtml(meta)}</span>
    </button>`;
  }

  return html;
}

function renderOverlayBody(state: ProjectPanelState): string {
  switch (state.overlayPanel) {
    case "plan":
      return `<p class="overlay-empty">完整计划已在主区打开。点主区「← 返回聊天」关闭。</p>`;
    case "docs":
      return renderDocsOverlay(state);
    case "projects":
      return renderProjectsOverlay(state);
    case "threads":
      return renderThreadsOverlay(state);
    default:
      return "";
  }
}

function overlayTitle(panel: OverlayPanel): string {
  switch (panel) {
    case "plan": return "完整计划";
    case "docs": return "文档";
    case "projects": return "我的项目";
    case "threads": return "会话线";
    default: return "";
  }
}

// ---- degradation banner ----

function renderDegradeBanner(level: string, label: string, explain: string): string {
  const accent = level === "L3" ? "var(--ma-danger)" : "#d4a000";
  return `<div class="sidebar-change-banner" style="border-color:${accent};background:color-mix(in srgb, ${accent} 6%, var(--ma-surface));">
    <span style="font-size:0.72rem;">⚠ 项目管理器: ${label}</span>
    <div style="font-size:0.72rem;color:var(--ma-text-muted);margin:0.2rem 0;">${explain}</div>
    <button type="button" class="unified-btn" data-action="dismiss-degrade" style="font-size:0.68rem;padding:0.15rem 0.4rem;">关闭</button>
  </div>`;
}

function renderCodeFollowupBanner(followup: CodeFollowup): string {
  if (followup.mode === "agent_cleanup") {
    return `<div class="sidebar-change-banner textbook-followup-banner"><div class="sidebar-change-banner-title">任务已删 · 清理出口已准备</div><div class="sidebar-change-banner-changes">主聊已预填清理请求；不会自动发送，也不会自动删除文件。</div><button type="button" class="unified-btn unified-btn-accent" data-action="open-code-followup">打开清理请求</button><button type="button" class="unified-btn" data-action="dismiss-code-followup">关闭</button></div>`;
  }
  const guide = followup.guide;
  const commands = (guide?.commands || []).map((command) => `<code>${escapeHtml(command)}</code>`).join("<br>");
  return `<div class="sidebar-change-banner textbook-followup-banner"><div class="sidebar-change-banner-title">任务已删 · git 清理指引</div><div class="sidebar-change-banner-changes">${escapeHtml(guide?.note || "不会自动 revert 或提交。")}</div><pre>${commands || "请在项目目录检查未提交变更。"}</pre><button type="button" class="unified-btn" data-action="dismiss-code-followup">关闭</button></div>`;
}

// ---- change banner / plan confirmation inline ----

function renderChangeBanner(state: ProjectPanelState): string {
  if (state.runawayEnabled) return "";
  const recentLedger = state.changeTimeline.slice(-3).reverse();
  const ledgerRows = recentLedger.map((change) => {
    const affected = [...change.requirements, ...change.tasks, ...change.acceptance, ...change.verification];
    const impact = affected.length > 0 ? `ID: ${affected.join(", ")}` : "ID: none";
    const stale = change.stale_docs.length > 0 ? `stale: ${change.stale_docs.join(", ")}` : "stale: none";
    const replan = change.replan_required ? "需要重新规划" : "无需重新规划";
    return `<div class="sidebar-change-banner-changes"><strong>${escapeHtml(change.change_id)}</strong> · ${escapeHtml(change.paths.join(", "))}<br>${escapeHtml(impact)}<br>${escapeHtml(stale)} · ${replan}</div>`;
  }).join("");
  const ledgerHtml = recentLedger.length > 0
    ? `<div class="sidebar-change-banner sidebar-change-timeline" style="border-color:#6b7cff;background:color-mix(in srgb, #6b7cff 6%, var(--ma-surface));"><div class="sidebar-change-banner-title" style="display:flex;align-items:center;justify-content:space-between;gap:0.35rem;">CHG 影响时间线 · ${state.changeTimeline.length} 条<button type="button" class="unified-btn" data-action="toggle-change-timeline" aria-expanded="${state.changeTimelineExpanded ? "true" : "false"}" style="font-size:0.68rem;padding:0.12rem 0.35rem;">${state.changeTimelineExpanded ? "收起" : "展开"}</button></div>${state.changeTimelineExpanded ? ledgerRows : ""}</div>`
    : "";
  // Plan confirmation (draft / plan_dirty with overlay)
  const needsPlanConfirm =
    !state.runawayEnabled && state.planOverlay && state.planStatus !== "confirmed";
  const needsPlanDirtyBanner =
    !state.runawayEnabled && state.planStatus === "plan_dirty" && !needsPlanConfirm;
  const isTaskLevelChange =
    state.changesLevel === "task" && !needsPlanConfirm && !needsPlanDirtyBanner;
  const needsScopeBanner = !state.runawayEnabled && state.scopeNeedsReconfirm && state.planStatus === "confirmed";

  // Plan confirmation takes priority over change banner
  if (needsPlanConfirm) {
    const planLabel = state.planStatus === "plan_dirty"
      ? "方案有变更 · 请确认"
      : "方案待确认";
    return `<div class="sidebar-change-banner" style="border-color:var(--ma-accent);background:color-mix(in srgb, var(--ma-accent) 6%, var(--ma-surface));">
      <div class="sidebar-change-banner-title">${escapeHtml(planLabel)} (${escapeHtml(state.projectId)})</div>
      <button type="button" class="unified-btn unified-btn-accent" data-action="confirm-plan" style="margin-right:0.4rem;">确认开工</button>
      <button type="button" class="unified-btn" data-action="edit-plan">修改计划</button>
    </div>`;
  }

  if (needsScopeBanner) {
    const stop = state.turnInProgress
      ? `<button type="button" class="unified-btn" data-action="stop-turn">一键停止</button>`
      : "";
    return `<div class="sidebar-change-banner" style="border-color:#d4a000;background:color-mix(in srgb, #d4a000 6%, var(--ma-surface));"><div class="sidebar-change-banner-title">范围已变 · 可再确认</div><div class="sidebar-change-banner-changes">计划变更已由采纳控件写入真源；当前执行阶段不被伪造改变。</div><button type="button" class="unified-btn unified-btn-accent" data-action="confirm-scope">再确认范围</button>${stop}</div>`;
  }

  // Task-level changes: 30s auto-confirm
  if (isTaskLevelChange && getTaskChangeFingerprint(state) !== state.dismissedTaskChangeFingerprint) {
    let changesHtml = "";
    if (state.planChangeLog.length > 0) {
      const recent = state.planChangeLog.slice(-6);
      changesHtml = recent.map((c) => {
        const icon = c.kind === "toggle" ? "✓" : c.kind === "add" ? "+" : c.kind === "drop" ? "−" : c.kind === "skip" ? "~" : "⇅";
        return `${icon} ${escapeHtml(c.task_text)}`;
      }).join("<br>");
    } else {
      changesHtml = "任务已变更";
    }
    return `<div class="sidebar-change-banner" style="border-color:color-mix(in srgb, var(--ma-accent) 50%, transparent);background:color-mix(in srgb, var(--ma-accent) 4%, var(--ma-surface));">
      <div class="sidebar-change-banner-title">任务已变更 · <span data-countdown="30">30</span>s 后自动确认</div>
      <div class="sidebar-change-banner-changes">${changesHtml}</div>
      <div class="sidebar-change-banner-actions">
        <button type="button" class="unified-btn unified-btn-accent" data-action="confirm-changes">确认</button>
        <button type="button" class="unified-btn" data-action="collapse-banner">关闭</button>
      </div>
    </div>`;
  }

  if (needsPlanDirtyBanner) {
    if (state.planBannerCollapsed) {
      const pendingCount = state.planChangeLog.length;
      return `<div class="sidebar-change-banner" style="border-color:#d4a000;background:color-mix(in srgb, #d4a000 6%, var(--ma-surface));">
        <span style="color:#d4a000">⚠ 方案有变更${pendingCount > 0 ? ` (${pendingCount} 项)` : ""}</span>
        <button type="button" class="unified-btn unified-btn-accent" data-action="confirm-changes" style="margin-left:0.35rem;font-size:0.72rem;">确认变更</button>
        <button type="button" class="unified-btn" data-action="expand-banner" style="margin-left:0.25rem;font-size:0.72rem;">查看</button>
      </div>`;
    }

    // Build change summary from change_log
    let changesHtml = "";
    if (state.planChangeLog.length > 0) {
      const recent = state.planChangeLog.slice(-8);
      changesHtml = recent.map((c) => {
        const icon = c.kind === "toggle" ? "✓" : c.kind === "add" ? "+" : c.kind === "drop" ? "−" : c.kind === "skip" ? "~" : "⇅";
        return `${icon} ${escapeHtml(c.task_text)}`;
      }).join("<br>");
    } else {
      changesHtml = "任务结构已变更，请确认后继续。";
    }

    const highlightLabel = state.highlightChanges ? "取消高亮" : "查看变更";
    return `<div class="sidebar-change-banner">
      <div class="sidebar-change-banner-title">⚠ 方案有变更 · 请确认</div>
      <div class="sidebar-change-banner-changes">${changesHtml}</div>
      <div class="sidebar-change-banner-actions">
        <button type="button" class="unified-btn unified-btn-accent" data-action="confirm-changes">确认变更</button>
        <button type="button" class="unified-btn" data-action="toggle-highlight">${highlightLabel}</button>
        <button type="button" class="unified-btn" data-action="collapse-banner">收起</button>
      </div>
    </div>`;
  }

  return ledgerHtml;
}

// ---- project event application (keep compat) ----

export function applyProjectStateEvent(
  state: ProjectPanelState,
  event: Extract<ServerEvent, { type: "project.state" }>,
): boolean {
  const nextProjectId = event.project_id ?? "";
  if (!shouldApplyProjectEvent(state, nextProjectId)) return false;
  if (state.projectId !== nextProjectId) {
    resetProjectScopedState(state);
  }
  state.projectId = nextProjectId;
  state.projectSummary = event.project_summary ?? state.projectSummary;
  state.planStatus = event.plan_status ?? "draft";
  state.workflowStage = isFlowStage(event.workflow_stage) ? event.workflow_stage : state.workflowStage;
  state.needsDesignConfirm = Boolean(event.needs_design_confirm);
  state.executionStage = isFlowStage(event.execution_stage) ? event.execution_stage : state.executionStage;
  state.executionStageStatus = event.execution_stage_status ?? state.executionStageStatus;
  state.executionStageReason = event.execution_stage_reason ?? state.executionStageReason;
  state.executionStageBlockers = event.execution_stage_blockers ?? state.executionStageBlockers;
  state.executionStageMissing = event.execution_stage_missing ?? state.executionStageBlockers;
  state.executionStageWarnings = event.execution_stage_warnings ?? state.executionStageWarnings;
  state.executionStageAffected = event.execution_stage_affected ?? state.executionStageAffected;
  state.executionStageDeferred = event.execution_stage_deferred ?? state.executionStageDeferred;
  state.executionStageArtifacts = (event.execution_stage_artifacts ?? []).filter(
    (artifact): artifact is ProjectArtifactSummary => Boolean(
      artifact && typeof artifact.path === "string" && typeof artifact.role === "string"
        && typeof artifact.revision === "string" && typeof artifact.status === "string",
    ),
  );
  if (event.release_acceptance) {
    state.milestoneAccepted = Boolean(event.release_acceptance.accepted);
    state.milestoneAcceptedAt = event.release_acceptance.accepted_at ?? null;
  }
  state.tasksMarkdown = event.tasks_markdown ?? "";
  state.mapMarkdown = event.map_markdown ?? "";
  state.tasksDone = event.tasks_done ?? 0;
  state.tasksTotal = event.tasks_total ?? 0;
  state.tasksAllDone = Boolean(event.tasks_all_done);
  state.deliveryProfile = event.delivery_profile ?? "solo";
  state.runawayEnabled = Boolean(event.runaway_enabled);
  state.runawayStatus = event.runaway_status ?? "";
  state.runawayCheckpoint = event.runaway_checkpoint ?? state.runawayCheckpoint;
  state.runawayRepairCount = event.runaway_repair_count ?? 0;
  state.runawayLastVerification = event.runaway_last_verification ?? null;
  state.runawayPausedReason = event.runaway_paused_reason ?? null;
  state.runawayAcceptancePassed = Boolean(event.runaway_acceptance_passed);
  state.runawayVerificationEvidence = event.runaway_verification_evidence ?? null;
  state.runawayVerificationEvidencePath = event.runaway_verification_evidence_path ?? null;
  applyRunawayPayload(state, event);
  state.reviewVerdict = event.review_verdict ?? null;
  state.reviewBlockersCount = event.review_blockers_count ?? 0;
  state.reviewProgressBlocked = Boolean(event.review_progress_blocked);
  state.scopeConfirmedAt = event.scope_confirmed_at ?? state.scopeConfirmedAt;
  state.scopeNeedsReconfirm = state.planStatus === "plan_dirty";

  // Diff old vs new to detect changes
  const oldPhases = state.taskPhases.length > 0 ? state.taskPhases : null;
  const oldSnapshot = state.taskPhases.length > 0
    ? captureTaskSnapshot(state.taskPhases)
    : { lines: new Set<number>(), lineTexts: new Map<number, string>() } as TaskSnapshot;

  state.taskPhases = parseTasksMarkdown(state.tasksMarkdown);

  if (oldPhases) {
    const diff = diffTaskPhases(oldSnapshot, state.taskPhases);
    // Mark newly added lines as "new" status
    for (const phase of state.taskPhases) {
      for (const task of phase.tasks) {
        if (diff.addedLines.has(task.line)) {
          task.status = "new";
        }
      }
    }
    // Collect highlighted lines from diff
    state.highlightedLines = new Set([
      ...diff.addedLines,
      ...diff.removedLines,
      ...diff.changedLines,
    ]);
    // If there are changes and highlight mode isn't active, store but don't show yet
    if (state.highlightedLines.size > 0 && !state.highlightChanges) {
      // Changes detected; planBanner will show them
    }
  }

  state.taskSnapshot = captureTaskSnapshot(state.taskPhases);

  if (!event.needs_plan_confirm) {
    state.planOverlay = null;
    state.highlightChanges = false;
  }
  updatePlanWarningState(state, getExecutionStage(state));
  return true;
}

export function applyProjectPlanState(
  state: ProjectPanelState,
  event: Extract<ServerEvent, { type: "project.plan.state" }>,
): boolean {
  const nextProjectId = event.project_id ?? "";
  if (!shouldApplyProjectEvent(state, nextProjectId)) return false;
  if (state.projectId !== nextProjectId) resetProjectScopedState(state);
  state.projectId = nextProjectId;
  state.planStatus = event.plan_status ?? "draft";
  const hasAuthoritativeRunawayV2 = event.runaway_version !== undefined;
  const preserveAuthoritativeRunawayV2 = state.runawayVersion >= 2 && !hasAuthoritativeRunawayV2;
  if (!preserveAuthoritativeRunawayV2) {
    state.runawayEnabled = event.runaway_enabled ?? state.runawayEnabled;
    state.runawayStatus = event.runaway_status ?? state.runawayStatus;
    state.runawayCheckpoint = event.runaway_checkpoint ?? state.runawayCheckpoint;
    state.runawayRepairCount = event.runaway_repair_count ?? state.runawayRepairCount;
    state.runawayLastVerification = event.runaway_last_verification ?? state.runawayLastVerification;
    state.runawayPausedReason = event.runaway_paused_reason ?? state.runawayPausedReason;
    state.runawayAcceptancePassed = event.runaway_acceptance_passed ?? state.runawayAcceptancePassed;
    state.runawayVerificationEvidence = event.runaway_verification_evidence ?? state.runawayVerificationEvidence;
    state.runawayVerificationEvidencePath = event.runaway_verification_evidence_path ?? state.runawayVerificationEvidencePath;
    applyRunawayPayload(state, event);
    state.workflowStage = isFlowStage(event.workflow_stage) ? event.workflow_stage : state.workflowStage;
  }
  state.needsDesignConfirm = Boolean(event.needs_design_confirm);
  state.executionStage = isFlowStage(event.execution_stage) ? event.execution_stage : state.executionStage;
  state.executionStageStatus = event.execution_stage_status ?? state.executionStageStatus;
  state.executionStageReason = event.execution_stage_reason ?? state.executionStageReason;
  state.executionStageBlockers = event.execution_stage_blockers ?? state.executionStageBlockers;
  state.executionStageMissing = event.execution_stage_missing ?? state.executionStageBlockers;
  state.executionStageWarnings = event.execution_stage_warnings ?? state.executionStageWarnings;
  state.executionStageAffected = event.execution_stage_affected ?? state.executionStageAffected;
  state.executionStageDeferred = event.execution_stage_deferred ?? state.executionStageDeferred;
  state.executionStageArtifacts = (event.execution_stage_artifacts ?? []).filter(
    (artifact): artifact is ProjectArtifactSummary => Boolean(
      artifact && typeof artifact.path === "string" && typeof artifact.role === "string"
        && typeof artifact.revision === "string" && typeof artifact.status === "string",
    ),
  );
  if (event.release_acceptance) {
    state.milestoneAccepted = Boolean(event.release_acceptance.accepted);
    state.milestoneAcceptedAt = event.release_acceptance.accepted_at ?? null;
  }
  state.tasksMarkdown = event.tasks_markdown ?? "";
  state.mapMarkdown = event.map_markdown ?? "";
  state.tasksDone = event.tasks_done ?? 0;
  state.tasksTotal = event.tasks_total ?? 0;
  state.tasksAllDone = Boolean(event.tasks_all_done);
  state.planChangeLog = event.change_log ?? [];
  state.changeTimeline = event.change_timeline ?? [];
  updatePlanWarningState(
    state,
    getExecutionStage(state),
    event.execution_stage_warnings ?? event.warnings ?? [],
  );
  state.degradationLevel = event.degradation_level ?? "L1";
  state.degradationLabel = event.degradation_label ?? "全功能";
  state.changesLevel = event.changes_level ?? null;
  state.scopeNeedsReconfirm = state.planStatus === "plan_dirty" || state.planChangeLog.some((change) => change.kind !== "toggle");
  state.externalChanges = event.external_changes ?? false;
  state.suggestions = normalizeSuggestions(event.suggestions ?? []);
  if (state.suggestions.some((s) => Boolean(s.action))) {
    state.adoptedFooterMessage = null;
  }
  state.autoFixNotices = event.auto_fix_actions ?? [];
  const operationalNotices = Array.from(new Set((event.operational_notices ?? []).map((notice) => notice.trim()).filter(Boolean)));
  const operationalFingerprint = operationalNoticeFingerprint(operationalNotices);
  state.operationalNotices = operationalFingerprint === state.dismissedOperationalNoticeFingerprint ? [] : operationalNotices;
  state.partnerNotices = event.partner_notices ?? [];
  state.partnerBusy = false;
  state.nextTask = event.next_task ?? null;
  state.nextTaskLine = typeof event.next_task_line === "number" ? event.next_task_line : null;

  // Diff old vs new task phases
  const oldSnapshot = state.taskPhases.length > 0
    ? captureTaskSnapshot(state.taskPhases)
    : { lines: new Set<number>(), lineTexts: new Map<number, string>() } as TaskSnapshot;

  state.taskPhases = parseTasksMarkdown(state.tasksMarkdown);

  if (oldSnapshot.lines.size > 0) {
    const diff = diffTaskPhases(oldSnapshot, state.taskPhases);
    for (const phase of state.taskPhases) {
      for (const task of phase.tasks) {
        if (diff.addedLines.has(task.line)) {
          task.status = "new";
        }
      }
    }
    state.highlightedLines = new Set([
      ...diff.addedLines,
      ...diff.removedLines,
      ...diff.changedLines,
    ]);
  }

  state.taskSnapshot = captureTaskSnapshot(state.taskPhases);
  return true;
}

export function applyProjectListEvent(
  state: ProjectPanelState,
  event: Extract<ServerEvent, { type: "project.list" }>,
): void {
  state.projects = event.projects.map((item) => ({
    id: item.id,
    tasksDone: item.tasks_done,
    tasksTotal: item.tasks_total,
    sessionId: item.session_id,
    isCurrent: item.is_current,
  }));
}

export function applyProjectThreadsEvent(
  state: ProjectPanelState,
  event: Extract<ServerEvent, { type: "project.threads" }>,
): boolean {
  if (!shouldApplyProjectEvent(state, event.project_id)) return false;
  state.threadsLoading = false;
  state.activeSessionId = event.active_session_id || "";
  state.threads = (event.threads || []).map((item) => ({
    sessionId: item.session_id,
    title: item.title || item.session_id,
    preview: item.preview || "",
    updatedAt: item.updated_at || "",
    archived: Boolean(item.archived),
  }));
  return true;
}

export function isViewingArchivedThread(state: ProjectPanelState): boolean {
  if (!state.projectId || !state.activeSessionId || !state.currentSessionId) {
    return false;
  }
  return state.currentSessionId !== state.activeSessionId;
}

// ---- element refs (matches new DOM) ----

export function setupProjectPanel(container: HTMLElement): {
  goalCard: HTMLElement;
  sidebarTitle: HTMLElement;
  sidebarMeta: HTMLElement;
  sidebarProgressWrap: HTMLElement;
  sidebarProgressFill: HTMLElement;
  taskFlow: HTMLElement;
  servicesPanel: HTMLElement;
  terminalsPanel: HTMLElement;
  changeBanner: HTMLElement;
  iconBar: HTMLElement;
  overlayPanel: HTMLElement;
  overlayTitle: HTMLElement;
  overlayBody: HTMLElement;
  overlayBackBtn: HTMLButtonElement;
  // compat refs kept for index.ts event wiring
  pickerRefreshBtn: HTMLButtonElement;
  switchConfirmBtn: HTMLButtonElement;
  switchCancelBtn: HTMLButtonElement;
  switchCard: HTMLElement;
  switchTitle: HTMLElement;
  switchMessage: HTMLElement;
  planCard: HTMLElement;
  planTitle: HTMLElement;
  planPreview: HTMLElement;
  planConfirmBtn: HTMLButtonElement;
  planEditBtn: HTMLButtonElement;
  sidebarTabs: HTMLElement;
  tasksPanel: HTMLElement;
  mapPanel: HTMLElement;
  pickerList: HTMLElement;
} {
  const el = (id: string) => container.querySelector<HTMLElement>(`#${id}`)!;

  return {
    // new elements
    goalCard: el("unified-goal-card"),
    sidebarTitle: el("project-sidebar-title"),
    sidebarMeta: el("project-sidebar-meta"),
    sidebarProgressWrap: el("project-sidebar-progress"),
    sidebarProgressFill: el("sidebar-progress-fill"),
    taskFlow: el("sidebar-task-flow"),
    terminalsPanel: el("sidebar-terminals"),
    servicesPanel: el("sidebar-services"),
    changeBanner: el("sidebar-change-banner"),
    iconBar: el("sidebar-icon-bar"),
    overlayPanel: el("sidebar-overlay"),
    overlayTitle: el("overlay-title"),
    overlayBody: el("overlay-body"),
    overlayBackBtn: el("overlay-back-btn") as HTMLButtonElement,
    // compat refs (dummy elements kept for old index.ts event wiring)
    pickerRefreshBtn: el("project-picker-refresh") as HTMLButtonElement,
    switchConfirmBtn: el("project-switch-confirm") as HTMLButtonElement,
    switchCancelBtn: el("project-switch-cancel") as HTMLButtonElement,
    switchCard: el("project-switch-card"),
    switchTitle: el("project-switch-title"),
    switchMessage: el("project-switch-message"),
    planCard: el("project-plan-card"),
    planTitle: el("project-plan-title"),
    planPreview: el("project-plan-preview"),
    planConfirmBtn: el("project-plan-confirm") as HTMLButtonElement,
    planEditBtn: el("project-plan-edit") as HTMLButtonElement,
    sidebarTabs: el("project-sidebar-tabs"),
    tasksPanel: el("project-panel-tasks"),
    mapPanel: el("project-panel-map"),
    pickerList: el("project-picker-list"),
  };
}

const TERMINAL_STATE_LABELS: Record<string, string> = {
  starting: "正在启动",
  running: "运行中",
  closed: "已关闭",
  exited: "已退出",
  lost: "会话已丢失",
  orphaned: "宿主已断开",
  unsupported: "当前环境不支持",
  failed: "启动失败",
};

function terminalIsActive(session: TerminalSessionItem): boolean {
  return session.state === "starting" || session.state === "running";
}

function terminalStatusLabel(session: TerminalSessionItem): string {
  return TERMINAL_STATE_LABELS[session.state] || session.state || "未知状态";
}

function terminalStateClass(session: TerminalSessionItem): string {
  return Object.prototype.hasOwnProperty.call(TERMINAL_STATE_LABELS, session.state)
    ? session.state
    : "unknown";
}

function terminalActivityLabel(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value || "暂无活动记录";
  const elapsed = Math.max(0, Date.now() - timestamp);
  if (elapsed < 60_000) return "刚刚活动";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)} 分钟前活动`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)} 小时前活动`;
  return `${Math.floor(elapsed / 86_400_000)} 天前活动`;
}

function terminalDetailExitLabel(session: TerminalSessionItem): string {
  const parts: string[] = [];
  if (session.exit_code !== null) parts.push(`退出码 ${session.exit_code}`);
  if (session.signal) parts.push(`信号 ${session.signal}`);
  return parts.join(" · ");
}

function renderTerminalDetails(state: ProjectPanelState): string {
  const session = state.terminalDetails;
  if (!session) return "";
  const exitLabel = terminalDetailExitLabel(session);
  const reason = session.reason
    ? `<div class="sidebar-terminal-detail-reason">${escapeHtml(session.reason)}</div>`
    : "";
  const cursorNotice = state.terminalOutputCursorReset
    ? `<div class="sidebar-terminal-output-note">输出已被截断，已从最早可用位置重新读取。</div>`
    : state.terminalOutputTruncated
      ? `<div class="sidebar-terminal-output-note">本次输出达到上限，刷新可继续读取。</div>`
      : "";
  const error = state.terminalOutputError
    ? `<div class="sidebar-terminals-error">${escapeHtml(state.terminalOutputError)}</div>`
    : "";
  const output = state.terminalOutput || (state.terminalOutputLoading ? "正在读取输出…" : "暂无输出");
  const closeButton = terminalIsActive(session)
    ? `<button type="button" class="unified-btn unified-btn-danger" data-action="terminal-close" data-terminal-id="${escapeHtml(session.session_id)}">关闭会话</button>`
    : "";
  return `<div class="sidebar-terminal-details" id="terminalDetails">
    <div class="sidebar-terminal-details-header">
      <div>
        <div class="sidebar-terminal-details-title">${escapeHtml(terminalStatusLabel(session))}</div>
        <div class="sidebar-terminal-details-id">${escapeHtml(session.session_id)}</div>
      </div>
      <button type="button" class="sidebar-terminal-icon-btn" data-action="terminal-details-close" aria-label="关闭终端详情" title="关闭详情">×</button>
    </div>
    <div class="sidebar-terminal-details-command">${escapeHtml(session.command || "未命名命令")}</div>
    <div class="sidebar-terminal-details-meta">${escapeHtml(session.cwd || ".")} · ${escapeHtml(terminalActivityLabel(session.last_activity_at))}</div>
    ${exitLabel || reason ? `<div class="sidebar-terminal-details-meta">${escapeHtml(exitLabel)}${exitLabel && reason ? " · " : ""}${reason}</div>` : ""}
    ${error}
    ${cursorNotice}
    <pre class="sidebar-terminal-output" id="terminal-output" aria-live="polite">${escapeHtml(output)}</pre>
    <div class="sidebar-terminal-details-actions">
      <button type="button" class="unified-btn" data-action="terminal-output-refresh" data-terminal-id="${escapeHtml(session.session_id)}" ${state.terminalOutputLoading ? "disabled" : ""}>刷新输出</button>
      <button type="button" class="unified-btn" data-action="terminal-copy-output" data-terminal-id="${escapeHtml(session.session_id)}" ${state.terminalOutput ? "" : "disabled"}>复制可见输出</button>
      ${closeButton}
    </div>
  </div>`;
}

function renderTerminalsPanel(state: ProjectPanelState): string {
  const active = state.terminalSessions.filter(terminalIsActive).length;
  const inactive = state.terminalSessions.length - active;
  const attention = state.terminalSessions.filter((session) =>
    ["lost", "orphaned", "failed", "unsupported"].includes(session.state),
  ).length;
  const summary = state.terminalSessions.length === 0
    ? state.terminalsLoading ? "加载中…" : "暂无会话"
    : [
        active > 0 ? `${active} 个运行中` : "无运行中",
        inactive > 0 ? `${inactive} 个已结束` : "",
        attention > 0 ? `${attention} 个需处理` : "",
      ].filter(Boolean).join(" · ");

  const rows = state.terminalSessions.length === 0
    ? `<div class="sidebar-terminals-empty">${state.terminalsLoading ? "正在读取终端会话…" : "暂无终端会话"}</div>`
    : state.terminalSessions.map((session) => {
      const status = terminalStatusLabel(session);
      const command = truncateSummary(session.command || "未命名命令", 64);
      const cwd = truncateSummary(session.cwd || ".", 72);
      const activity = terminalActivityLabel(session.last_activity_at);
      const selected = state.terminalDetails?.session_id === session.session_id;
      return `<button type="button" class="sidebar-terminal-row is-${terminalStateClass(session)}${selected ? " is-selected" : ""}" data-action="terminal-open" data-terminal-id="${escapeHtml(session.session_id)}" aria-pressed="${selected ? "true" : "false"}" title="${escapeHtml(session.command || "")}">
        <span class="sidebar-terminal-dot" aria-hidden="true"></span>
        <div class="sidebar-terminal-main">
          <div class="sidebar-terminal-primary"><span class="sidebar-terminal-status">${escapeHtml(status)}</span><span class="sidebar-terminal-command">${escapeHtml(command)}</span></div>
          <div class="sidebar-terminal-meta"><span>${escapeHtml(cwd)}</span><span>· ${escapeHtml(activity)}</span></div>
        </div>
      </button>`;
    }).join("");

  const collapsed = state.terminalsCollapsed;
  const bodyClass = collapsed ? "sidebar-terminals-body is-collapsed" : "sidebar-terminals-body";
  const error = state.terminalsError
    ? `<div class="sidebar-terminals-error">${escapeHtml(state.terminalsError)}</div>`
    : "";
  const toggleLabel = collapsed
    ? (active > 0 ? `终端 · ${active} 运行中` : "终端")
    : `终端会话 · ${summary}`;
  return `<section class="sidebar-terminals" aria-label="终端会话">
    <button type="button" class="sidebar-terminals-toggle" data-action="toggle-terminals" aria-expanded="${collapsed ? "false" : "true"}">
      <span class="sidebar-terminals-toggle-chevron">${collapsed ? "▸" : "▾"}</span>
      <span>${escapeHtml(toggleLabel)}</span>
    </button>
    <div class="${bodyClass}">
      <div class="sidebar-terminals-header">
        <span>持久终端</span>
        <button type="button" class="unified-btn" data-action="terminals-refresh" style="font-size:0.7rem;padding:0.1rem 0.4rem;" ${state.terminalsLoading ? "disabled" : ""}>刷新</button>
      </div>
      ${error}
      <div class="sidebar-terminals-list">${rows}</div>
      ${renderTerminalDetails(state)}
    </div>
  </section>`;
}

// ---- Phase 27 services panel ----

function renderServicesPanel(state: ProjectPanelState): string {
  const stopped = state.services.filter((s) => !s.alive).length;
  const running = state.services.length - stopped;
  const summary =
    state.services.length === 0
      ? state.servicesLoading
        ? "加载中…"
        : "暂无登记"
      : running > 0
        ? `${running} 个运行中 · ${stopped} 已停止`
        : `${stopped} 个已停止`;

  const rows =
    state.services.length === 0
      ? `<div class="sidebar-services-empty">${state.servicesLoading ? "加载中…" : "暂无登记服务"}</div>`
      : state.services
          .map((s) => {
            const alive = s.alive ? "alive" : "dead";
            const port =
              s.ready_port != null && s.ready_port !== undefined
                ? ` · :${escapeHtml(String(s.ready_port))}`
                : "";
            const status = s.status ? escapeHtml(String(s.status)) : s.alive ? "running" : "stopped";
            return `<div class="sidebar-service-row is-${alive}">
              <div class="sidebar-service-main">
                <span class="sidebar-service-dot" title="${alive}"></span>
                <span class="sidebar-service-name">${escapeHtml(s.name)}</span>
                <span class="sidebar-service-meta">${status}${port}</span>
              </div>
              <button type="button" class="unified-btn sidebar-service-logs" data-action="service-logs" data-service-name="${escapeHtml(s.name)}" style="font-size:0.7rem;padding:0.1rem 0.35rem;">日志</button>
            </div>`;
          })
          .join("");
  const err = state.servicesError
    ? `<div class="sidebar-services-error">${escapeHtml(state.servicesError)}</div>`
    : "";
  const log =
    state.servicesLogName && state.servicesLogText
      ? `<details class="sidebar-services-log" open>
          <summary>${escapeHtml(state.servicesLogName)} 日志尾</summary>
          <pre>${escapeHtml(state.servicesLogText)}</pre>
        </details>`
      : state.servicesLogName
        ? `<div class="sidebar-services-empty">（无日志）</div>`
        : "";
  const collapsed = state.servicesCollapsed;
  const chevron = collapsed ? "▸" : "▾";
  const bodyCls = collapsed ? "sidebar-services-body is-collapsed" : "sidebar-services-body";
  const toggleLabel = collapsed
    ? (running > 0 ? `服务 · ${running} 运行中` : "服务")
    : `服务 · ${summary}`;
  return `<button type="button" class="sidebar-services-toggle" data-action="toggle-services">
      <span class="sidebar-services-toggle-chevron">${chevron}</span>
      <span>${escapeHtml(toggleLabel)}</span>
    </button>
    <div class="${bodyCls}">
      <div class="sidebar-services-header">
        <span>Services</span>
        <button type="button" class="unified-btn" data-action="services-refresh" style="font-size:0.7rem;padding:0.1rem 0.4rem;" ${state.servicesLoading ? "disabled" : ""}>刷新</button>
      </div>
      ${err}
      <div class="sidebar-services-list">${rows}</div>
      ${log}
    </div>`;
}

// ---- main render ----

export function renderProjectSidebar(
  els: ReturnType<typeof setupProjectPanel>,
  state: ProjectPanelState,
  callbacks: ProjectPanelCallbacks,
): void {
  const goalView = deriveProjectGoalViewModel(state);
  els.goalCard.classList.toggle("hidden", !state.projectId);
  els.goalCard.dataset.goalStatus = goalView.status;
  els.goalCard.innerHTML = renderProjectGoalCard(state);

  // header
  if (state.projectId) {
    els.sidebarTitle.textContent = state.projectId;
    els.sidebarMeta.textContent = sidebarOpenTasksLabel(state);
    els.sidebarProgressWrap.classList.remove("hidden");
    const pct = state.tasksTotal > 0
      ? Math.round((state.tasksDone / state.tasksTotal) * 100)
      : 0;
    els.sidebarProgressFill.style.width = `${pct}%`;
  } else {
    els.sidebarTitle.textContent = "项目";
    els.sidebarMeta.textContent = "未绑定项目 · 使用「项目 新建 <id>」";
    els.sidebarProgressWrap.classList.add("hidden");
  }

  // Phase 27 — Services panel (always in project sidebar)
  els.terminalsPanel.innerHTML = renderTerminalsPanel(state);
  els.servicesPanel.innerHTML = renderServicesPanel(state);

  // --- banner area: single priority chain (UX-026: suggestions live in body — SP-9) ---
  // Priority: undo > partner busy > partner notices (non-adopted) > adopted footer >
  //           external > auto_fix > …
  let bannerHtml = "";
  const actionableSuggestions = state.runawayEnabled
    ? []
    : state.suggestions.filter((s) => Boolean(s.action));
  const hasAdoptFlash = Boolean(state.suggestionAdoptFlash);

  if (state.switchInProgress) {
    bannerHtml = `<div class="sidebar-change-banner" style="border-color:var(--ma-accent);">
      <div class="sidebar-change-banner-title">正在切换项目</div>
      <div class="sidebar-change-banner-changes">正在加载新项目的目标、任务和会话。</div>
    </div>`;
  } else if (state.codeFollowup) {
    bannerHtml = renderCodeFollowupBanner(state.codeFollowup);
  } else if (state.undoDescription) {
    bannerHtml = `<div class="sidebar-undo-toast">
      <span>${escapeHtml(state.undoDescription)}</span>
      <button type="button" class="unified-btn" data-action="undo-last" style="font-size:0.75rem;">撤销</button>
    </div>`;
  } else if (state.externalChanges) {
    bannerHtml = `<div class="sidebar-change-banner" style="border-color:#d4a000;background:color-mix(in srgb, #d4a000 6%, var(--ma-surface));">
      <div class="sidebar-change-banner-title">检测到外部修改</div>
      <div class="sidebar-change-banner-changes">TASKS.md 被外部工具修改（git / 编辑器 等）。任务流已刷新为最新内容。</div>
      <button type="button" class="unified-btn" data-action="dismiss-external" style="font-size:0.72rem;padding:0.15rem 0.4rem;">关闭</button>
    </div>`;
  } else if (!state.runawayEnabled && isDocumentationBatch(state)) {
    const pending = state.planWarningsPending.length;
    bannerHtml = `<div class="sidebar-change-banner" style="border-color:var(--ma-accent);background:color-mix(in srgb, var(--ma-accent) 6%, var(--ma-surface));">
      <div class="sidebar-change-banner-title">文档整理中</div>
      <div class="sidebar-change-banner-changes">四个核心制品按一个批次处理；${pending ? `已收集 ${pending} 条待完善建议，` : ""}阶段结束后统一告诉你结果。</div>
    </div>`;
  } else if (!state.runawayEnabled && state.partnerBusy) {
    bannerHtml = renderPartnerNotices(state.partnerNotices || [], true, actionableSuggestions.length);
  } else if (state.adoptedFooterMessage && actionableSuggestions.length === 0 && !hasAdoptFlash) {
    bannerHtml = renderAdoptedFooterBanner(state.adoptedFooterMessage);
  } else if (
    !state.runawayEnabled &&
    state.partnerNotices &&
    state.partnerNotices.length > 0 &&
    actionableSuggestions.length === 0 &&
    !hasAdoptFlash &&
    isAdoptedPartnerNotice(state.partnerNotices)
  ) {
    bannerHtml = renderAdoptedNotice(state.partnerNotices);
  } else if (!state.runawayEnabled && state.partnerNotices && state.partnerNotices.length > 0 && !isAdoptedPartnerNotice(state.partnerNotices)) {
    bannerHtml = renderPartnerNotices(state.partnerNotices, false, actionableSuggestions.length);
  } else if (!state.runawayEnabled && state.autoFixNotices.length > 0) {
    bannerHtml = renderAutoFixNotice(state.autoFixNotices);
  } else if (state.operationalNotices.length > 0) {
    bannerHtml = renderOperationalNotices(state.operationalNotices);
  } else if (state.reviewProgressBlocked) {
    bannerHtml = renderReviewProgressBanner(state);
  } else if (state.detectedProject && !state.projectId) {
    bannerHtml = `<div class="sidebar-change-banner" style="border-color:var(--ma-accent);background:color-mix(in srgb, var(--ma-accent) 8%, var(--ma-surface));">
      <div class="sidebar-change-banner-title">检测到项目目录</div>
      <div class="sidebar-change-banner-changes">${escapeHtml(state.detectedProject.reason)}</div>
      <div class="sidebar-change-banner-actions">
        <button type="button" class="unified-btn unified-btn-accent" data-action="detect-switch" data-project-id="${escapeHtml(state.detectedProject.id)}">切换为项目</button>
        <button type="button" class="unified-btn" data-action="detect-dismiss">忽略</button>
      </div>
    </div>`;
  } else if (state.degradationLevel !== "L1") {
    const level = state.degradationLevel || "L1";
    const label = state.degradationLabel || level;
    const explain = level === "L3"
      ? "项目服务暂时不可用，退回直接文件操作模式。拆分和新增任务请通过聊天框。"
      : "项目服务暂时不可用，拆分和新增任务退回单条添加模式。";
    bannerHtml = renderDegradeBanner(level, label, explain);
  } else if (!state.runawayEnabled && isDocumentationBatch(state)) {
    bannerHtml = `<div class="sidebar-change-banner" style="border-color:var(--ma-accent);background:color-mix(in srgb, var(--ma-accent) 6%, var(--ma-surface));">
      <div class="sidebar-change-banner-title">文档整理中</div>
      <div class="sidebar-change-banner-changes">项目资料正在连续整理，完成后会统一告诉你下一步。</div>
    </div>`;
  } else {
    bannerHtml = renderChangeBanner(state);
  }

  if (bannerHtml) {
    els.changeBanner.classList.remove("hidden");
    els.changeBanner.innerHTML = bannerHtml;
  } else {
    els.changeBanner.classList.add("hidden");
    els.changeBanner.innerHTML = "";
  }

  // A7 decision surface (main) — full TASKS only in plan overlay
  els.taskFlow.innerHTML = renderDecisionSurface(state, callbacks);

  // project count badge
  const projectBadge = els.iconBar.querySelector<HTMLElement>("#project-count-badge");
  if (projectBadge) {
    projectBadge.textContent = String(state.projects.length);
  }
  const threadBadge = els.iconBar.querySelector<HTMLElement>("#thread-count-badge");
  if (threadBadge) {
    const archivedCount = state.threads.filter((t) => t.archived).length;
    threadBadge.textContent = String(archivedCount);
    threadBadge.classList.toggle("hidden", archivedCount === 0);
  }
  // degradation indicator
  const degradeDot = els.iconBar.querySelector<HTMLElement>("#sidebar-degrade-dot");
  if (degradeDot) {
    const level = state.degradationLevel || "L1";
    if (level === "L1") {
      degradeDot.classList.add("hidden");
    } else {
      degradeDot.classList.remove("hidden");
      degradeDot.className = degradeDot.className
        .replace("hidden", "")
        .replace("is-l2", "").replace("is-l3", "").trim();
      degradeDot.classList.add(`is-${level.toLowerCase()}`);
      degradeDot.title = `项目管理器: ${state.degradationLabel || level}`;
    }
  }
  // active icon
  for (const btn of els.iconBar.querySelectorAll<HTMLButtonElement>(".sidebar-icon-btn")) {
    const panel = btn.dataset.panel as OverlayPanel | "tasks" | undefined;
    const active =
      (panel === "tasks" && !state.overlayPanel && state.mainFocus === "chat") ||
      (panel === "plan" && state.mainFocus === "plan_full") ||
      (panel !== "tasks" && panel !== "plan" && panel === state.overlayPanel);
    btn.classList.toggle("is-active", Boolean(active));
  }

  // overlay panel
  if (state.overlayPanel) {
    els.overlayPanel.classList.remove("hidden");
    els.overlayTitle.textContent = overlayTitle(state.overlayPanel);
    els.overlayBody.innerHTML = renderOverlayBody(state);
    void hydrateMermaid(els.overlayBody);
  } else {
    els.overlayPanel.classList.add("hidden");
  }

  // -- compat update: handle plan overlay / switch overlay in new style --
  updateCompatElements(els, state, callbacks);
}

// ---- compat bridge: wire up old element refs hidden in DOM ----

function updateCompatElements(
  els: ReturnType<typeof setupProjectPanel>,
  state: ProjectPanelState,
  callbacks: ProjectPanelCallbacks,
): void {
  // UX-024: compat plan card stays hidden — A7 uses change-banner + header meta only.
  els.planCard.classList.add("hidden");
  if (state.planOverlay && state.planStatus !== "confirmed") {
    els.planTitle.textContent = state.planOverlay.title;
    els.planPreview.textContent = state.planOverlay.tasksPreview || state.planOverlay.summary;
  } else if (state.planStatus === "draft" || state.planStatus === "plan_dirty") {
    els.planTitle.textContent =
      state.planStatus === "plan_dirty" ? "方案有变更 · 请确认" : "方案待确认";
    els.planPreview.textContent = state.tasksMarkdown.slice(0, 1200) || "（等待助手生成 TASKS.md）";
  }

  // Switch card (hidden, used by switchOverlay logic in index.ts)
  if (state.switchOverlay) {
    els.switchCard.classList.remove("hidden");
    els.switchTitle.textContent = `切换到 · ${state.switchOverlay.projectId}`;
    els.switchMessage.textContent = state.switchOverlay.message;
  } else {
    els.switchCard.classList.add("hidden");
    els.switchMessage.textContent = "";
  }

  // Markdown panels (hidden, task flow + overlay handle these)
  els.tasksPanel.classList.add("hidden");
  els.mapPanel.classList.add("hidden");

  // Sidebar tabs (hidden, icon bar replaces them)
  els.sidebarTabs.classList.add("hidden");
}
