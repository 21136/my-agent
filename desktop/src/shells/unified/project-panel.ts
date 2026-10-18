import type { AgentWsClient, ProjectArtifactSummary, ProjectDocItem, ServerEvent, ServiceListItem, TerminalSessionItem } from "../../api/ws";
import { escapeHtml } from "../chat-state";
import { renderDocCatalogHtml } from "./doc-reading";
import { userFacingThreadTitle } from "./ordinary-guards";
import { RAIL_TAB_LABELS, type MainFocus, type RailTab } from "./rail";
import {
  SERVICE_SCROLL_SELECTORS,
  TERMINAL_ENDED_PREVIEW,
  TERMINAL_SCROLL_SELECTORS,
  TERMINAL_STATE_LABELS,
  clearPanelHtml,
  groupTerminalSessions,
  patchPanelHtml,
  reconcileTerminalSession,
  servicesPanelFingerprint,
  terminalActivityCaption,
  terminalCwdLabel,
  terminalDisplayStatusLabel,
  terminalHumanTitle,
  terminalIsActive,
  terminalsPanelFingerprint,
  terminalsSummaryParts,
  type TerminalListSession,
} from "./terminal-list";
import {
  expectedProjectIdForEvents,
  shouldApplyProjectEvent as shouldApplyProjectEventForFilter,
  type ProjectSwitchFilterState,
} from "./project-switch-state";

export type { RailTab };

export type { ServiceListItem, TerminalSessionItem };

// ---- panel / stage types ----

export type OverlayPanel = "plan" | "docs" | "projects" | "threads" | null;

export type FlowStage = "requirements" | "documentation" | "design" | "implementation" | "verification" | "release";

// ---- existing types (compat) ----

export interface SwitchOverlayData {
  requestId: string;
  projectId: string;
  message: string;
  action: string;
}

export interface ExternalProjectScan {
  external_root: string;
  project_id: string;
  logical_root: string;
  markers: string[];
  has_git: boolean;
  git_dirty: boolean | null;
  has_verify: boolean;
  has_readme: boolean;
  has_env: boolean;
  has_build_manifest: boolean;
  requires_confirmation: boolean;
}

export interface ProjectListItem {
  id: string;
  sessionId: string | null;
  isCurrent: boolean;
  externalAdopted?: boolean;
  externalHealthy?: boolean;
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
  projectEntry: string;
  projectTemplate: string;
  mapMarkdown: string;
  projects: ProjectListItem[];
  externalImportPath: string;
  externalImportProjectId: string;
  externalImportScan: ExternalProjectScan | null;
  switchOverlay: SwitchOverlayData | null;
  switchInProgress: boolean;
  pendingPickerId: string;
  // new fields
  overlayPanel: OverlayPanel;
  railTab: RailTab;
  switchConfirmTarget: ProjectListItem | null;
  projectSearchQuery: string;
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
  // doc panel
  projectDocs: ProjectDocItem[];
  currentDocPath: string;
  currentDocContent: string;
  currentDocMtime: number;
  currentDocHash: string;
  newDocName: string;
  newDocFolder: string;
  docSearchQuery: string;
  renamingDocPath: string;
  renameDraft: string;
  deleteConfirmPath: string;
  docView: "preview" | "edit";
  docEditDraft: string;
  docDirty: boolean;
  docSaving: boolean;
  docConflictPrompt: boolean;
  docsChatSplit: boolean;
  docPlaceholderCount: number;
  recentlyChangedDocs: Set<string>;
  pendingDocUndo: Record<string, unknown> | null;
  // task add
  // auto-detect
  detectedProject: { id: string; reason: string } | null;
  // Plan Agent warnings (legacy / non-structured)
  // Undo toast
  undoDescription: string;
  undoTimerId: number | null;
  // Degradation indicator
  // Change confirmation
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
  terminalsEndedCollapsed: boolean;
  terminalsEndedShowAll: boolean;
  terminalDetails: TerminalSessionItem | null;
  terminalOutput: string;
  terminalOutputCursor: number;
  terminalOutputLoading: boolean;
  terminalOutputError: string;
  terminalOutputCursorReset: boolean;
  terminalOutputTruncated: boolean;
  // Phase 27 M1 — turn evidence strip
  turnEvidence: Array<{ tool: string; ok: boolean }>;
  turnGateNotice: string;
  // G14 M2 — exec reliability strip
  turnPostcondition: string;
  turnCircuitOpen: string[];
  turnPlaybookId: string;
  turnFailureClass: string;
  /** Finish reason of the latest turn. Empty while a turn is starting. */
  lastTurnFinishReason: string;
  // Phase 36 — project threads (live + archive)
  activeSessionId: string;
  threads: ProjectThreadItem[];
  threadsLoading: boolean;
  currentSessionId: string;
  mainFocus: MainFocus;
  // UX-026 — sidebar body / adopt feedback
  servicesCollapsed: boolean;
  turnInProgress: boolean;
  deliveryProfile: string;
  reviewVerdict: string | null;
  reviewBlockersCount: number;
  reviewProgressBlocked: boolean;
  scopeConfirmedAt: string;
  scopeNeedsReconfirm: boolean;
  flowPreviewStage: FlowStage | null;
  milestoneAccepted: boolean;
  milestoneAcceptedAt: string | null;
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

export interface ProjectPanelCallbacks {
  onProjectSwitch: (projectId: string) => void;
  onProjectSwitchConfirm: () => void;
  onProjectSwitchCancel: () => void;
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
  onExternalImportScan: (path: string, projectId: string) => void;
  onExternalImportApply: (path: string, projectId: string) => void;
  onExternalImportCancel: () => void;
}

// ---- helpers ----

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

export function resetProjectScopedState(state: ProjectPanelState): void {
  if (typeof window !== "undefined") {
    if (state.undoTimerId !== null) window.clearTimeout(state.undoTimerId);
  }
  state.projectSummary = "";
  state.planStatus = "";
  state.projectEntry = "";
  state.projectTemplate = "";
  state.mapMarkdown = "";
  state.switchOverlay = null;
  state.switchConfirmTarget = null;
  state.projectDocs = [];
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
  state.currentDocPath = "";
  state.currentDocContent = "";
  state.currentDocMtime = 0;
  state.currentDocHash = "";
  state.newDocName = "";
  state.newDocFolder = "";
  state.docSearchQuery = "";
  state.renamingDocPath = "";
  state.renameDraft = "";
  state.deleteConfirmPath = "";
  state.docView = "preview";
  state.docEditDraft = "";
  state.docDirty = false;
  state.docSaving = false;
  state.docConflictPrompt = false;
  state.docsChatSplit = true;
  state.docPlaceholderCount = 0;
  state.recentlyChangedDocs = new Set();
  state.pendingDocUndo = null;
  state.undoDescription = "";
  state.undoTimerId = null;
  state.services = [];
  state.servicesLoading = false;
  state.servicesError = "";
  state.servicesLogName = "";
  state.servicesLogText = "";
  state.terminalSessions = [];
  state.terminalsLoading = false;
  state.terminalsError = "";
  state.terminalDetails = null;
  state.terminalOutput = "";
  state.terminalOutputCursor = 0;
  state.terminalOutputLoading = false;
  state.terminalOutputError = "";
  state.terminalOutputCursorReset = false;
  state.terminalOutputTruncated = false;
  state.turnEvidence = [];
  state.turnGateNotice = "";
  state.turnPostcondition = "none";
  state.turnCircuitOpen = [];
  state.turnPlaybookId = "";
  state.turnFailureClass = "";
  state.lastTurnFinishReason = "";
  state.activeSessionId = "";
  state.threads = [];
  state.threadsLoading = false;
  state.currentSessionId = "";
  state.overlayPanel = null;
  state.railTab = "now";
  state.mainFocus = "chat";
  state.turnInProgress = false;
  state.deliveryProfile = "solo";
  state.reviewVerdict = null;
  state.reviewBlockersCount = 0;
  state.reviewProgressBlocked = false;
  state.scopeConfirmedAt = "";
  state.scopeNeedsReconfirm = false;
  state.flowPreviewStage = null;
  state.milestoneAccepted = false;
  state.milestoneAcceptedAt = null;
}

function switchFilterState(state: ProjectPanelState): ProjectSwitchFilterState {
  return {
    projectId: state.projectId,
    switchInProgress: state.switchInProgress,
    pendingPickerId: state.pendingPickerId,
    switchOverlayProjectId: state.switchOverlay?.projectId || "",
  };
}

export function shouldApplyProjectEvent(
  state: ProjectPanelState,
  eventProjectId: string | null | undefined,
): boolean {
  return shouldApplyProjectEventForFilter(switchFilterState(state), eventProjectId);
}

export function beginAtomicProjectSwitch(state: ProjectPanelState, projectId: string): void {
  const target = projectId.trim();
  resetProjectScopedState(state);
  if (target) {
    state.projectId = target;
    state.pendingPickerId = target;
  }
  state.switchInProgress = true;
  state.switchOverlay = null;
  state.switchConfirmTarget = null;
}

export function projectSwitchHeaderMeta(state: ProjectPanelState): { title: string; meta: string } | null {
  const pending = expectedProjectIdForEvents(switchFilterState(state));
  if (state.switchInProgress) {
    return {
      title: pending || state.projectId || "项目",
      meta: "正在加载…",
    };
  }
  if (state.switchOverlay) {
    return {
      title: state.switchOverlay.projectId || pending || state.projectId || "项目",
      meta: "等待确认切换",
    };
  }
  return null;
}

export function getExecutionStage(state: ProjectPanelState): FlowStage {
  if (isFlowStage(state.executionStage)) return state.executionStage;
  return state.projectId ? "release" : "requirements";
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
  ".plan-agent/manifest.json": "项目状态清单",
};

const USER_BLOCKER_REASON_LABELS: Record<string, string> = {
  documentation_in_progress: "项目文档正在整理中，当前还没有进入实现阶段。",
  design_not_confirmed: "项目设计尚未完成，当前还不能进入实现阶段。",
  design_incomplete: "项目设计还不完整。",
  plan_not_confirmed: "项目方案尚未确认，当前还不能开始实现。",
  scope_incomplete: "项目范围或验收标准还不完整。",
  no_executable_tasks: "当前没有满足依赖条件的可执行任务。",
  no_project: "还没有绑定项目。",
  manifest_unavailable: "项目状态清单暂时不可用，无法安全继续。",
  l2_stale: "项目文档与当前变更不一致，需要先更新受影响内容。",
  review_blocked: "交付检查没有通过，仍有问题需要处理。",
  tasks_not_started: "任务还没有开始。",
  tasks_in_progress: "任务正在实现中。",
  verification_pending: "可以先收尾验收；这不是继续改代码的硬阻挡。",
  verification_artifact_stale: "验证相关文档需要先更新。",
  verification_passed: "验证已经通过。",
};

const USER_FAILURE_CLASS_LABELS: Record<string, string> = {
  A: "最近一次执行没有给出可用结果。",
  B: "环境或依赖没有就绪。",
  C: "测试或构建没有通过。",
  D: "权限或路径不允许这次操作。",
  E: "这次执行出错，需要换一种做法。",
  F: "输出不完整，无法确认结果。",
};

function looksLikeInternalToken(value: string): boolean {
  const normalized = value.trim();
  if (!normalized) return false;
  if (/^[A-F]$/.test(normalized)) return true;
  return /^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+$/.test(normalized);
}

function humanizeVisibleText(value: string, fallback: string): string {
  const normalized = value.trim();
  if (!normalized) return fallback;
  return USER_BLOCKER_REASON_LABELS[normalized]
    || USER_FAILURE_CLASS_LABELS[normalized]
    || USER_BLOCKER_LABELS[normalized]
    || (looksLikeInternalToken(normalized) ? fallback : normalized);
}

export interface ProjectBlockerDetails {
  reason: string;
  impact: string;
  attempted: string;
  nextStep: string;
}

function userFacingBlockerReason(reason: string): string {
  const fallback = "当前还不能继续，请查看原因。";
  const normalized = reason.trim();
  if (!normalized) return fallback;
  if (USER_BLOCKER_REASON_LABELS[normalized]) return USER_BLOCKER_REASON_LABELS[normalized];
  if (normalized.includes("verification_pending")) return USER_BLOCKER_REASON_LABELS.verification_pending;
  if (normalized.includes("documentation_in_progress")) return USER_BLOCKER_REASON_LABELS.documentation_in_progress;
  if (normalized.includes("design_not_confirmed")) return USER_BLOCKER_REASON_LABELS.design_not_confirmed;
  if (normalized.includes("verification_artifact_stale")) return USER_BLOCKER_REASON_LABELS.verification_artifact_stale;
  return humanizeVisibleText(normalized, fallback);
}

function userFacingBlockerSummary(state: ProjectPanelState): string {
  if (state.reviewProgressBlocked && state.executionStageBlockers.length === 0) {
    return USER_BLOCKER_REASON_LABELS.review_blocked;
  }
  if (state.executionStageReason.trim()) {
    return userFacingBlockerReason(state.executionStageReason);
  }
  const labels = Array.from(new Set(
    state.executionStageBlockers
      .map((item) => USER_BLOCKER_LABELS[item] || (looksLikeInternalToken(item) ? "" : item))
      .filter(Boolean),
  ));
  return labels.length > 0
    ? "还需要处理：" + labels.join("、")
    : "当前还不能继续，请查看原因。";
}

function isOrdinaryVerificationWrapUp(state: ProjectPanelState): boolean {
  // Review pending is optional wrap-up, not a hard execution wall.
  return state.executionStageReason === "verification_pending";
}

function hasActualProjectBlocker(state: ProjectPanelState): boolean {
  return state.reviewProgressBlocked;
}

export function projectBlockerDetails(state: ProjectPanelState): ProjectBlockerDetails {
  const reviewBlocked = state.reviewProgressBlocked;
  const reason = reviewBlocked && !state.executionStageReason.trim()
    ? USER_BLOCKER_REASON_LABELS.review_blocked
    : userFacingBlockerReason(state.executionStageReason.trim());
  const stageLabel = USER_STAGE_LABELS[getExecutionStage(state)] || "当前阶段";
  const blockerLabels = Array.from(new Set(
    [...state.executionStageBlockers, ...state.executionStageMissing]
      .map((item) => USER_BLOCKER_LABELS[item] || (looksLikeInternalToken(item) ? "" : item))
      .filter(Boolean),
  ));
  const impact = reviewBlocked
    ? "项目不会进入下一阶段，也不会被标记为已完成。"
    : `当前停在「${stageLabel}」阶段${blockerLabels.length ? `，待处理项：${blockerLabels.join("、")}` : ""}。`;
  const attempted = "已停在当前阶段出口，没有绕过前置条件继续写入。";
  const nextStep = "处理上述问题后，再回到聊天继续当前项目。";
  return { reason, impact, attempted, nextStep };
}

export function hasProjectBlocker(state: ProjectPanelState): boolean {
  return hasActualProjectBlocker(state);
}

export function renderProjectBlockerDetails(state: ProjectPanelState): string {
  const details = projectBlockerDetails(state);
  return `<section class="unified-surface unified-blocker-details" aria-label="阻塞原因">
    <div class="unified-expand-title">为什么停在这里</div>
    <p><strong>原因</strong>：${escapeHtml(details.reason)}</p>
    <p><strong>影响</strong>：${escapeHtml(details.impact)}</p>
    <p><strong>已处理</strong>：${escapeHtml(details.attempted)}</p>
    <p><strong>下一步</strong>：${escapeHtml(details.nextStep)}</p>
    <div class="unified-expand-actions">
      <button type="button" class="unified-btn" data-action="blocker-details-close">关闭详情</button>
    </div>
  </section>`;
}

function compactGoalText(text: string, max = 180): string {
  const plain = text
    .replace(/[`*_#>\-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const t = plain.trim();
  return t.length <= max ? t : `${t.slice(0, max - 1)}…`;
}

function projectTaskProgressLabel(state: ProjectPanelState): string {
  if (getExecutionStage(state) === "documentation") return "正在整理资料";
  return state.projectId ? "项目进行中" : "等待开始";
}

const ORDINARY_TURN_ABORT_REASONS = new Set([
  "error",
  "cancelled",
  "timeout",
  "tool_error",
  "tool_loop_exceeded",
  "total_cap_exceeded",
  "segment_cap_pause",
]);
// provider_error is a model-service miss. The chat reply already explains it,
// and it is not stored on the project, so the goal must stay as it is after reload.

/** A finished ordinary turn failed or aborted. A tool miss inside a normal stop does not. */
export function ordinaryProjectTurnFailed(state: ProjectPanelState): boolean {
  if (state.executionStageStatus === "failed" || state.turnPostcondition === "fail") return true;
  return ORDINARY_TURN_ABORT_REASONS.has((state.lastTurnFinishReason || "").trim());
}

export function deriveProjectGoalViewModel(state: ProjectPanelState): ProjectGoalViewModel {
  if (!state.projectId) {
    return {
      status: "empty",
      statusLabel: "未开始",
      title: "选择或新建项目",
      summary: "绑定项目后，系统会把目标、文档和验证串成一条工作链路。",
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
      summary: `正在加载项目 ${state.projectId} 的目标和文档。`,
      nextStep: "项目状态加载完成后即可继续。",
      action: null,
      actionLabel: null,
    };
  }

  const goal = compactGoalText(state.projectSummary);
  const executionStage = getExecutionStage(state);
  const stageLabel = USER_STAGE_LABELS[executionStage];
  const blocker = userFacingBlockerSummary(state);
  if (hasActualProjectBlocker(state)) {
    return {
      status: "blocked",
      statusLabel: "需要处理",
      title: "当前无法继续",
      summary: blocker,
      nextStep: "查看原因，处理后再继续。",
      action: state.reviewProgressBlocked ? "jump-review-summary" : "jump-turn-process",
      actionLabel: "查看原因",
    };
  }

  if (!state.turnInProgress && ordinaryProjectTurnFailed(state)) {
    return {
      status: "failed",
      statusLabel: "验证未通过",
      title: "上一步没有完成",
      summary: humanizeVisibleText(state.turnFailureClass, "最近一次执行或验证没有通过。"),
      nextStep: "查看失败原因，再决定修复、重试或调整方案。",
      action: "jump-turn-process",
      actionLabel: "查看失败原因",
    };
  }

  if (state.turnInProgress || state.executionStageStatus === "in_progress") {
    return {
      status: "processing",
      statusLabel: "进行中",
      title: stageLabel,
      summary: goal,
      nextStep: "系统正在处理当前内容；你可以查看过程或停止。",
      action: "jump-turn-process",
      actionLabel: "查看过程",
    };
  }

  if (executionStage === "verification" || isOrdinaryVerificationWrapUp(state)) {
    return {
      status: "decision",
      statusLabel: "待验证",
      title: "可以先收尾验收",
      summary: goal,
      nextStep: "跑验收是可选收尾：可以先收尾验收并查看本回合证据，也可以继续改代码。",
      action: "run-verify",
      actionLabel: "跑验收",
    };
  }
  return {
    status: "decision",
    statusLabel: "等你决定",
    title: "准备开始下一步",
    summary: goal,
    nextStep: "在聊天里描述下一步要做的事。",
    action: null,
    actionLabel: null,
  };
}

export function renderProjectGoalCard(state: ProjectPanelState): string {
  // Compact one-line fallback only. Decision/blocker/delivery banners stay in the sidebar.
  const view = deriveProjectGoalViewModel(state);
  const contextGoal = compactGoalText(state.projectSummary) || view.title;
  return `<div class="unified-context-bar" data-goal-status="${escapeHtml(view.status)}">
    <div class="unified-context-main">
      <strong class="unified-context-project" title="${escapeHtml(state.projectId)}">${escapeHtml(state.projectId)}</strong>
      <span class="unified-context-separator" aria-hidden="true">·</span>
      <span class="unified-context-goal" title="${escapeHtml(contextGoal)}">${escapeHtml(contextGoal)}</span>
      <span class="unified-context-progress">${escapeHtml(projectTaskProgressLabel(state))}</span>
      <span class="unified-context-status is-${escapeHtml(view.status)}">${escapeHtml(view.statusLabel)}</span>
    </div>
  </div>`;
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

export function renderRailBindEmpty(title: string, summary?: string): string {
  return renderSidebarStatusCard({
    ariaLabel: "未绑定项目",
    title,
    summary: summary || "绑定一个项目后，这个标签会显示对应内容。",
    action: "open-projects",
    actionLabel: "打开项目",
    actionAccent: true,
  });
}

export function renderRailStageEmpty(params: {
  title: string;
  copy: string;
  action?: string;
  actionLabel?: string;
}): string {
  const action = params.action && params.actionLabel
    ? `<button type="button" class="unified-btn unified-btn-accent" data-action="${escapeHtml(params.action)}">${escapeHtml(params.actionLabel)}</button>`
    : "";
  return `<div class="rail-stage-empty">
    <h2 class="rail-stage-empty-title">${escapeHtml(params.title)}</h2>
    <p class="rail-stage-empty-copy">${escapeHtml(params.copy)}</p>
    ${action}
  </div>`;
}

export function renderProjectsStage(state: ProjectPanelState): string {
  if (!state.projectId) {
    if (state.projects.length) {
      return renderRailStageEmpty({
        title: "选择项目",
        copy: "从左侧列表打开一个项目，打开后会回到当下继续工作。",
      });
    }
    return renderRailStageEmpty({
      title: "还没有项目",
      copy: "新建一个项目后即可开始对话与改代码。",
      action: "new-project",
      actionLabel: "新建项目",
    });
  }
  const view = deriveProjectGoalViewModel(state);
  return `<div class="rail-stage-empty rail-project-overview">
    <h2 class="rail-stage-empty-title">${escapeHtml(state.projectId)}</h2>
    <p class="rail-stage-empty-copy">${escapeHtml(view.summary || "项目已打开。")}</p>
    <p class="rail-stage-empty-meta">${escapeHtml(view.nextStep || "进入当下继续当前工作。")}</p>
    <button type="button" class="unified-btn unified-btn-accent" data-action="open-now">进入当下</button>
  </div>`;
}

function renderNowRailSidebar(_state: ProjectPanelState, _callbacks: ProjectPanelCallbacks): string {
  // 当下 shows the session list (rendered by index.ts) — no focus/“为什么卡住” card.
  return "";
}

function renderDocsRailSidebar(state: ProjectPanelState): string {
  if (!state.projectId) {
    return `<p class="overlay-empty">绑定项目后，这里会列出文档目录。</p>`;
  }
  const chip = `<div class="rail-project-chip">${escapeHtml(state.projectId)}</div>`;
  const undo = state.undoDescription
    ? `<div class="sidebar-undo-toast">
      <span>${escapeHtml(state.undoDescription)}</span>
      <button type="button" class="unified-btn" data-action="undo-last" style="font-size:0.75rem;">撤销</button>
    </div>`
    : "";
  return chip + undo + renderDocCatalogHtml(state.projectDocs, state.currentDocPath, {
    newDocName: state.newDocName,
    newDocFolder: state.newDocFolder,
    currentContent: state.currentDocContent,
    inputId: "overlay-new-doc-input",
    createAction: "overlay-new-doc",
    renamingDocPath: state.renamingDocPath,
    renameDraft: state.renameDraft,
    deleteConfirmPath: state.deleteConfirmPath,
    searchQuery: state.docSearchQuery,
    recentlyChanged: state.recentlyChangedDocs,
    placeholderCount: state.docPlaceholderCount,
    showFolderField: true,
  });
}

function renderProjectsRailSidebar(state: ProjectPanelState): string {
  return renderProjectsOverlay(state);
}

function renderRailSidebarBody(state: ProjectPanelState, callbacks: ProjectPanelCallbacks): string {
  if (state.switchInProgress) {
    return renderSwitchLoadingHtml(state);
  }
  if (state.switchOverlay) {
    return renderSwitchConfirmHtml({
      projectId: state.switchOverlay.projectId,
      message: state.switchOverlay.message,
    });
  }
  switch (state.railTab) {
    case "docs":
      return renderDocsRailSidebar(state);
    case "projects":
      return renderProjectsRailSidebar(state);
    case "now":
    default:
      return renderNowRailSidebar(state, callbacks);
  }
}

function projectSessionHint(item: ProjectListItem): string {
  if (item.isCurrent) return "当前";
  if (item.sessionId) return "可续接";
  return "新建会话";
}

// ---- overlay panel rendering ----

export function renderSwitchConfirmHtml(params: {
  projectId: string;
  message: string;
}): string {
  return `<div class="overlay-switch-confirm" id="overlay-switch-confirm">
      <div class="overlay-switch-confirm-title">切换到 · ${escapeHtml(params.projectId)}</div>
      <div class="overlay-switch-confirm-text">${escapeHtml(params.message)}</div>
      <div class="overlay-switch-confirm-actions">
        <button type="button" class="unified-btn unified-btn-accent" id="overlay-switch-confirm-btn">确认切换</button>
        <button type="button" class="unified-btn" id="overlay-switch-cancel-btn">取消</button>
      </div>
    </div>`;
}

function renderSwitchLoadingHtml(state: ProjectPanelState): string {
  const target = expectedProjectIdForEvents(switchFilterState(state)) || state.projectId || "目标项目";
  return renderSidebarStatusCard({
    ariaLabel: "项目切换中",
    title: `正在加载 ${target}`,
    summary: "旧项目内容已暂时隐藏，等待新项目状态加载完成。",
  });
}

function renderProjectsOverlay(state: ProjectPanelState): string {
  if (state.switchConfirmTarget) {
    const item = state.switchConfirmTarget;
    return renderSwitchConfirmHtml({
      projectId: item.id,
      message: "将恢复已有会话。当前会话已自动保存。",
    });
  }

  let html = `<input type="text" class="overlay-search-input" id="overlay-project-search" placeholder="搜索项目…" value="${escapeHtml(state.projectSearchQuery)}">
    <section class="external-project-import" aria-label="导入外部项目">
      <div class="sidebar-change-banner-title">导入已有项目</div>
      <div class="sidebar-change-banner-changes">原目录不复制；确认后通过 junction 接入，后续修改直接落到原项目。</div>
      <input type="text" class="overlay-search-input" id="external-project-path" placeholder="外部项目绝对路径" value="${escapeHtml(state.externalImportPath)}">
      <input type="text" class="overlay-search-input" id="external-project-id" placeholder="项目 ID（可选）" value="${escapeHtml(state.externalImportProjectId)}">
      <button type="button" class="unified-btn" data-action="external-import-scan">扫描外部项目</button>
    </section>`;
  if (state.externalImportScan) {
    const scan = state.externalImportScan;
    const git = scan.git_dirty === true ? "有未提交修改" : scan.git_dirty === false ? "Git 干净" : "未检测到 Git";
    html += `<section class="external-project-import is-scan" aria-label="外部项目扫描结果">
      <div class="sidebar-change-banner-title">${escapeHtml(scan.project_id)} · 待确认接入</div>
      <div class="sidebar-change-banner-changes">${escapeHtml(git)} · ${escapeHtml(scan.markers.join("、") || "无标准标记")}</div>
      <button type="button" class="unified-btn unified-btn-accent" data-action="external-import-apply">确认原地接入</button>
      <button type="button" class="unified-btn" data-action="external-import-cancel">取消</button>
    </section>`;
  }

  const query = state.projectSearchQuery.toLowerCase().trim();

  if (!state.projects.length) {
    html += `<p class="overlay-empty">暂无项目</p>`;
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
    const externalHint = item.externalAdopted
      ? (item.externalHealthy === false ? " · 外部目录不可用" : " · 外部项目")
      : "";
    html += `<button type="button" class="overlay-project-item${current ? " is-current" : ""}" data-project-id="${escapeHtml(item.id)}"${disabled ? " disabled" : ""}>
      <span class="overlay-project-item-name">${escapeHtml(item.id)}</span>
      <span class="overlay-project-item-meta">${escapeHtml(projectSessionHint(item))}${externalHint}</span>
    </button>`;
  }

  return html;
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
  state.projectEntry = event.project_entry ?? state.projectEntry;
  state.projectTemplate = event.project_template ?? state.projectTemplate;
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
  state.mapMarkdown = event.map_markdown ?? "";
  state.deliveryProfile = event.delivery_profile ?? "solo";
  state.reviewVerdict = event.review_verdict ?? null;
  state.reviewBlockersCount = event.review_blockers_count ?? 0;
  state.reviewProgressBlocked = Boolean(event.review_progress_blocked);
  state.scopeConfirmedAt = event.scope_confirmed_at ?? state.scopeConfirmedAt;
  state.scopeNeedsReconfirm = state.planStatus === "plan_dirty";

  return true;
}

export function applyProjectListEvent(
  state: ProjectPanelState,
  event: Extract<ServerEvent, { type: "project.list" }>,
): void {
  state.projects = event.projects.map((item) => ({
    id: item.id,
    sessionId: item.session_id,
    isCurrent: item.is_current,
    externalAdopted: item.external_adopted,
    externalHealthy: item.external_healthy,
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
    title: userFacingThreadTitle(item.title || "", item.session_id),
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

function terminalStatusLabel(session: TerminalListSession): string {
  return terminalDisplayStatusLabel(session);
}

function terminalStateClass(session: TerminalListSession): string {
  const display = reconcileTerminalSession(session);
  return Object.prototype.hasOwnProperty.call(TERMINAL_STATE_LABELS, display.state)
    ? display.state
    : "unknown";
}

function terminalDetailExitLabel(session: TerminalSessionItem): string {
  const parts: string[] = [];
  if (session.exit_code !== null) parts.push(`退出码 ${session.exit_code}`);
  if (session.signal) parts.push(`信号 ${session.signal}`);
  return parts.join(" · ");
}

function renderTerminalDetails(state: ProjectPanelState): string {
  const raw = state.terminalDetails;
  if (!raw) return "";
  const session = reconcileTerminalSession(raw);
  const exitLabel = terminalDetailExitLabel(raw);
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
  const activity = terminalActivityCaption(session.last_activity_at, { live: terminalIsActive(session) });
  return `<div class="sidebar-terminal-details" id="terminalDetails">
    <div class="sidebar-terminal-details-header">
      <div>
        <div class="sidebar-terminal-details-title">${escapeHtml(terminalStatusLabel(session))}</div>
        <div class="sidebar-terminal-details-id">${escapeHtml(session.session_id)}</div>
      </div>
      <button type="button" class="sidebar-terminal-icon-btn" data-action="terminal-details-close" aria-label="关闭终端详情" title="关闭详情">×</button>
    </div>
    <div class="sidebar-terminal-details-command">${escapeHtml(session.command || "未命名命令")}</div>
    <div class="sidebar-terminal-details-meta">${escapeHtml(session.cwd || ".")} · ${escapeHtml(activity)}</div>
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

function renderTerminalRow(state: ProjectPanelState, session: TerminalSessionItem): string {
  const display = reconcileTerminalSession(session);
  const status = terminalStatusLabel(display);
  const title = terminalHumanTitle(display.command || "", display.cwd || "");
  const cwd = terminalCwdLabel(display.cwd || "");
  const activity = terminalActivityCaption(display.last_activity_at, { live: terminalIsActive(display) });
  const selected = state.terminalDetails?.session_id === display.session_id;
  const meta = [cwd, activity].filter(Boolean).join(" · ");
  return `<button type="button" class="sidebar-terminal-row is-${terminalStateClass(display)}${selected ? " is-selected" : ""}" data-action="terminal-open" data-terminal-id="${escapeHtml(display.session_id)}" aria-pressed="${selected ? "true" : "false"}" title="${escapeHtml(display.command || title)}">
        <span class="sidebar-terminal-dot" aria-hidden="true"></span>
        <div class="sidebar-terminal-main">
          <div class="sidebar-terminal-primary"><span class="sidebar-terminal-status">${escapeHtml(status)}</span><span class="sidebar-terminal-command">${escapeHtml(title)}</span></div>
          <div class="sidebar-terminal-meta">${escapeHtml(meta)}</div>
        </div>
      </button>`;
}

function renderTerminalsPanel(state: ProjectPanelState): string {
  const grouped = groupTerminalSessions(state.terminalSessions, {
    selectedId: state.terminalDetails?.session_id,
    endedCollapsed: state.terminalsEndedCollapsed,
    endedShowAll: state.terminalsEndedShowAll,
    endedPreview: TERMINAL_ENDED_PREVIEW,
  });
  const summaryParts = terminalsSummaryParts(grouped.active.length, grouped.attention.length, grouped.endedTotal);
  const summary = state.terminalSessions.length === 0
    ? (state.terminalsLoading ? "加载中…" : "暂无会话")
    : (summaryParts.join(" · ") || "暂无会话");
  const pinnedEnded = state.terminalsEndedCollapsed ? grouped.endedVisible : [];
  const liveSessions = [...grouped.active, ...grouped.attention, ...pinnedEnded];
  let liveRows: string;
  if (state.terminalSessions.length === 0) {
    liveRows = `<div class="sidebar-terminals-empty">${state.terminalsLoading ? "正在读取终端会话…" : "暂无终端会话"}</div>`;
  } else if (liveSessions.length === 0 && grouped.endedTotal > 0) {
    liveRows = `<div class="sidebar-terminals-empty">没有运行中或需处理的会话</div>`;
  } else {
    liveRows = liveSessions.map((session) => renderTerminalRow(state, session)).join("");
  }

  let endedBlock = "";
  if (grouped.endedTotal > 0) {
    const endedCollapsed = state.terminalsEndedCollapsed;
    const endedRows = endedCollapsed
      ? ""
      : grouped.endedVisible.map((session) => renderTerminalRow(state, session)).join("");
    const showAll = !endedCollapsed && !state.terminalsEndedShowAll && grouped.endedHiddenCount > 0
      ? `<button type="button" class="sidebar-terminals-more" data-action="terminals-show-all-ended">查看全部 ${grouped.endedTotal}</button>`
      : "";
    endedBlock = `<div class="sidebar-terminals-ended">
      <button type="button" class="sidebar-terminals-ended-toggle" data-action="toggle-ended-terminals" aria-expanded="${endedCollapsed ? "false" : "true"}">
        <span class="sidebar-terminals-toggle-chevron">${endedCollapsed ? "▸" : "▾"}</span>
        <span>已结束 · ${grouped.endedTotal}</span>
      </button>
      <div class="sidebar-terminals-ended-list${endedCollapsed ? " is-collapsed" : ""}">${endedRows}${showAll}</div>
    </div>`;
  }

  const collapsed = state.terminalsCollapsed;
  const bodyClass = collapsed ? "sidebar-terminals-body is-collapsed" : "sidebar-terminals-body";
  const error = state.terminalsError
    ? `<div class="sidebar-terminals-error">${escapeHtml(state.terminalsError)}</div>`
    : "";
  const toggleLabel = summary === "暂无会话" || summary === "加载中…"
    ? (collapsed ? "终端" : `终端 · ${summary}`)
    : `终端 · ${summary}`;
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
      <div class="sidebar-terminals-list">${liveRows}</div>
      ${endedBlock}
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
  return `<button type="button" class="sidebar-services-toggle" data-action="toggle-services" aria-expanded="${collapsed ? "false" : "true"}">
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
  els.goalCard.classList.add("hidden");
  els.goalCard.dataset.goalStatus = goalView.status;
  els.goalCard.innerHTML = renderProjectGoalCard(state);

  const rail = state.railTab || "now";
  const railMeta = RAIL_TAB_LABELS[rail];
  const switchHeader = projectSwitchHeaderMeta(state);
  if (switchHeader) {
    els.sidebarTitle.textContent = switchHeader.title;
    els.sidebarMeta.textContent = switchHeader.meta;
  } else if (rail === "now") {
    els.sidebarTitle.textContent = railMeta.label;
    els.sidebarMeta.textContent = state.projectId || "未绑定项目";
  } else if (rail === "docs") {
    els.sidebarTitle.textContent = railMeta.label;
    els.sidebarMeta.textContent = state.projectId || "文档目录";
  } else {
    els.sidebarTitle.textContent = railMeta.label;
    els.sidebarMeta.textContent = state.projects.length ? `${state.projects.length} 个项目` : "我的项目";
  }

  const showRuntimeChrome = rail === "now" && !state.switchInProgress && !state.switchOverlay;
  els.terminalsPanel.classList.toggle("hidden", !showRuntimeChrome);
  els.servicesPanel.classList.toggle("hidden", !showRuntimeChrome);
  if (showRuntimeChrome) {
    patchPanelHtml(
      els.terminalsPanel,
      renderTerminalsPanel(state),
      terminalsPanelFingerprint(state),
      TERMINAL_SCROLL_SELECTORS,
    );
    patchPanelHtml(
      els.servicesPanel,
      renderServicesPanel(state),
      servicesPanelFingerprint(state),
      SERVICE_SCROLL_SELECTORS,
    );
  } else {
    clearPanelHtml(els.terminalsPanel);
    clearPanelHtml(els.servicesPanel);
  }

  // --- banner area: single priority chain ---
  // Priority: switching > undo > detected project.
  // Plan/task banners (方案待确认, partner notices, TASKS.md external edits) were
  // removed with the task subsystem; only runtime/project banners remain.
  let bannerHtml = "";
  if (state.switchInProgress) {
    bannerHtml = `<div class="sidebar-change-banner" style="border-color:var(--ma-accent);">
      <div class="sidebar-change-banner-title">正在切换项目</div>
      <div class="sidebar-change-banner-changes">正在加载新项目的会话和文档。</div>
    </div>`;
  } else if (state.undoDescription) {
    bannerHtml = `<div class="sidebar-undo-toast">
      <span>${escapeHtml(state.undoDescription)}</span>
      <button type="button" class="unified-btn" data-action="undo-last" style="font-size:0.75rem;">撤销</button>
    </div>`;
  } else if (state.detectedProject && !state.projectId) {
    bannerHtml = `<div class="sidebar-change-banner" style="border-color:var(--ma-accent);background:color-mix(in srgb, var(--ma-accent) 8%, var(--ma-surface));">
      <div class="sidebar-change-banner-title">检测到项目目录</div>
      <div class="sidebar-change-banner-changes">${escapeHtml(state.detectedProject.reason)}</div>
      <div class="sidebar-change-banner-actions">
        <button type="button" class="unified-btn unified-btn-accent" data-action="detect-switch" data-project-id="${escapeHtml(state.detectedProject.id)}">切换为项目</button>
        <button type="button" class="unified-btn" data-action="detect-dismiss">忽略</button>
      </div>
    </div>`;
  }

  if (bannerHtml && showRuntimeChrome) {
    els.changeBanner.classList.remove("hidden");
    els.changeBanner.innerHTML = bannerHtml;
  } else {
    els.changeBanner.classList.add("hidden");
    els.changeBanner.innerHTML = "";
  }

  els.taskFlow.innerHTML = renderRailSidebarBody(state, callbacks);

  // project count badge
  const projectBadge = els.iconBar.querySelector<HTMLElement>("#project-count-badge");
  if (projectBadge) {
    projectBadge.textContent = String(state.projects.length);
  }
  // degradation indicator (fed by the removed plan-state channel; stays hidden)
  els.iconBar.querySelector<HTMLElement>("#sidebar-degrade-dot")?.classList.add("hidden");
  for (const btn of els.iconBar.querySelectorAll<HTMLButtonElement>(".sidebar-icon-btn")) {
    btn.classList.toggle("is-active", btn.dataset.panel === rail);
  }

  els.overlayPanel.classList.add("hidden");
  els.overlayBody.innerHTML = "";

  // -- compat update: handle plan overlay / switch overlay in new style --
  updateCompatElements(els, state, callbacks);
}

// ---- compat bridge: wire up old element refs hidden in DOM ----

function updateCompatElements(
  els: ReturnType<typeof setupProjectPanel>,
  state: ProjectPanelState,
  callbacks: ProjectPanelCallbacks,
): void {
  // UX-024: compat plan card stays hidden.
  els.planCard.classList.add("hidden");

  // Compat switch card stays hidden; confirm chrome lives in the sidebar body above the rail.
  els.switchCard.classList.add("hidden");
  if (state.switchOverlay) {
    els.switchTitle.textContent = `切换到 · ${state.switchOverlay.projectId}`;
    els.switchMessage.textContent = state.switchOverlay.message;
  } else {
    els.switchMessage.textContent = "";
  }

  // Markdown panels (hidden, task flow + overlay handle these)
  els.tasksPanel.classList.add("hidden");
  els.mapPanel.classList.add("hidden");

  // Sidebar tabs (hidden, icon bar replaces them)
  els.sidebarTabs.classList.add("hidden");
}
