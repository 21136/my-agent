import type { AgentWsClient, ChangeLedgerItem, PlanChangeItem, PlanSuggestion, ProjectArtifactSummary, ProjectDocItem, ServerEvent, ServiceListItem } from "../../api/ws";
import { renderMarkdown } from "../../markdown";
import { escapeHtml } from "../chat-state";
import type { MainFocus } from "./plan-review";
import { acceptLabel, truncateSummary, diffStats } from "./plan-review";

export type { PlanSuggestion, ServiceListItem };

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

export type FlowStage = "requirements" | "design" | "implementation" | "verification" | "release";

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
  executionStageReason: string;
  executionStageBlockers: string[];
  executionStageMissing: string[];
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
  // task add
  quickAddText: string;
  // auto-detect
  detectedProject: { id: string; reason: string } | null;
  // Plan Agent warnings (legacy / non-structured)
  planWarnings: string[];
  // Undo toast
  undoDescription: string;
  undoTimerId: number | null;
  // Degradation indicator
  degradationLevel: string;
  degradationLabel: string;
  // Change confirmation
  changesLevel: string | null;
  autoConfirmTimerId: number | null;
  externalChanges: boolean;
  suggestions: PlanSuggestion[];
  autoFixNotices: string[];
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
  scopeNeedsReconfirm: boolean;
  flowPreviewStage: FlowStage | null;
  milestoneAccepted: boolean;
  milestoneAcceptedAt: string | null;
  codeFollowup: CodeFollowup | null;
  nextTurnChangeSummary: string | null;
  dropTaskPendingId: string | null;
  dropTaskPendingWorking: boolean;
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
  onScopeConfirm: () => void;
  onStopTurn: () => void;
  onMilestoneAccept: () => void;
}

// ---- helpers ----

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
  { id: "design", label: "设计", short: "计划" },
  { id: "implementation", label: "实现", short: "写码" },
  { id: "verification", label: "验证", short: "证据" },
  { id: "release", label: "发布", short: "里程碑" },
];

function isFlowStage(value: unknown): value is FlowStage {
  return FLOW_STAGES.some((stage) => stage.id === value);
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

function renderFlowRail(state: ProjectPanelState): string {
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

function renderStagePlanCard(state: ProjectPanelState, callbacks?: ProjectPanelCallbacks): string {
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
      body = `<div class="textbook-plan-goal">${state.projectId ? `项目 ${escapeHtml(state.projectId)}：` : ""}先让第一轮有可执行交付物。</div><div class="textbook-plan-stat">开放任务 ${openCount} 条${state.scopeNeedsReconfirm ? " · 范围已变" : ""}</div>`;
      action = state.scopeConfirmedAt
        ? `<span class="textbook-soft-signal">范围已确认</span>`
        : `<button type="button" class="unified-btn unified-btn-accent" data-action="confirm-scope" ${openCount === 0 ? "disabled" : ""}>确认范围</button>`;
      break;
    case "design":
      body = `<div class="textbook-plan-goal">计划已采纳后，选择下一条可武装任务。</div><div class="textbook-plan-stat">待审阅 ${state.suggestions.filter((suggestion) => Boolean(suggestion.action)).length} 条 · 计划 ${escapeHtml(state.planStatus || "draft")}</div>`;
      action = state.suggestions.some((suggestion) => Boolean(suggestion.action))
        ? `<button type="button" class="unified-btn unified-btn-accent" data-action="open-plan-review">打开审阅面</button>`
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
  const documentGroups = [
    renderDocumentGroup("本阶段阻塞", state.executionStageMissing, "blocker"),
    renderDocumentGroup("本次变更受影响", state.executionStageAffected, "affected"),
    renderDocumentGroup("后续阶段待完善", state.executionStageDeferred, "deferred"),
  ].filter(Boolean);
  const missing = documentGroups.length
    ? `<div class="textbook-stage-findings">${documentGroups.join("")}<div class="textbook-soft-signal">只处理当前阶段与本次变更包的直接影响，不递归补齐全项目文档。</div></div>`
    : "";
  body += missing + renderStageArtifactSummary(state, stage) + renderStageBasis(state, stage);
  return `<section class="textbook-plan-card" aria-label="阶段计划卡"><div class="textbook-plan-card-header"><span>阶段计划 · ${escapeHtml(FLOW_STAGES.find((item) => item.id === stage)?.label || "需求")}</span>${stage !== execution ? `<span class="textbook-preview-badge">预览中</span>` : ""}</div><div class="textbook-plan-card-body">${body}</div><div class="textbook-plan-card-actions">${action}</div></section>`;
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

function renderTurnSummary(state: ProjectPanelState): string {
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

function renderReliabilityStrip(state: ProjectPanelState): string {
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
      const short = n.length > 140 ? `${n.slice(0, 137)}…` : n;
      return `<div style="font-size:0.78rem;opacity:0.92;margin-top:0.2rem">${escapeHtml(short)}</div>`;
    })
    .join("");
  const title = busy ? "计划搭档 · 思考中…" : "计划搭档";
  const pendingAction = !busy && /待采纳|待审阅/.test(notices.join("\n"))
    ? actionableCount > 0
      ? `<button type="button" class="unified-btn unified-btn-accent" data-action="open-plan-review">打开计划审阅（${actionableCount}）</button>`
      : `<div class="sidebar-change-banner-changes">当前没有可采纳提案卡，请重新发送计划请求。</div>`
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

function isAdoptedPartnerNotice(notices: string[]): boolean {
  const head = normalizePartnerNoticeLine(notices[0] ?? "");
  if (!head) return false;
  if (/待审阅|待采纳|待侧栏/.test(head)) return false;
  return /^(已采纳写入|已写入|已从归档恢复)/.test(head);
}

function renderAdoptedNotice(notices: string[]): string {
  const line = normalizePartnerNoticeLine(notices[0] ?? "");
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

function renderDecisionSurface(state: ProjectPanelState, callbacks: ProjectPanelCallbacks): string {
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
    html += `<button type="button" class="unified-btn" data-action="open-full-plan" style="width:100%;margin:0.35rem 0 0.15rem;font-size:0.78rem;">查看完整计划</button>`;
  }
  html += `</div>`;
  html += renderSuggestionStack(state);
  html += renderTurnSummary(state);
  html += renderReliabilityStrip(state);
  return html;
}

function renderReviewProgressBanner(state: ProjectPanelState): string {
  if (!state.reviewProgressBlocked) return "";
  const count = state.reviewBlockersCount > 0 ? ` · ${state.reviewBlockersCount} 项阻塞` : "";
  return `<div class="sidebar-change-banner" style="border-color:#d4a000;background:color-mix(in srgb, #d4a000 6%, var(--ma-surface));">
    <div class="sidebar-change-banner-title">交付审查未通过</div>
    <div class="sidebar-change-banner-changes">审查 verdict=fail${escapeHtml(count)}；ritual 模式下暂不可勾选 TASKS。请先修复 blocker 或重新审查。</div>
    <button type="button" class="unified-btn" data-action="jump-review-summary" style="font-size:0.72rem;">查看审查摘要</button>
  </div>`;
}

function planStatusLabel(state: ProjectPanelState): string {
  if (state.planStatus === "confirmed") {
    if (state.tasksAllDone && state.tasksTotal > 0) return "全部完成";
    const open = Math.max(0, state.tasksTotal - state.tasksDone);
    return `还有 ${open} 条开放`;
  }
  if (state.planStatus === "plan_dirty") return "计划已变更 · 待确认";
  return "计划待确认";
}

function projectProgressLabel(item: ProjectListItem): string {
  if (item.tasksTotal === 0) return "无任务";
  const open = Math.max(0, item.tasksTotal - item.tasksDone);
  if (open === 0) return "全部完成";
  return `${open}/${item.tasksTotal} 未完成`;
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
      currentPhase = { title: phaseMatch[1].trim(), tasks: [] };
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

function renderTaskFlow(state: ProjectPanelState, highlightLines: Set<number> | null = null): string {
  if (!state.taskPhases.length) {
    return `<p class="overlay-empty">TASKS.md 将显示在这里</p>`;
  }

  // A7 overlay: open items only (done items live in archive)
  let html = `<p class="overlay-empty" style="padding:0.25rem 0 0.5rem;font-size:0.72rem;">完整开放队列 · 勾选/右键可操作</p>`;
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

      html += `<label class="${cls.join(" ")}" data-line="${task.line}">
        <input type="checkbox" class="task-checkbox" data-line="${task.line}">
        <span class="task-text">${escapeHtml(task.text)}</span>
      </label>`;

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
    html = `<p class="overlay-empty">暂无开放任务（已完成项在 TASKS.archive.md）</p>`;
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
  // If viewing a specific document
  if (state.currentDocPath) {
    return `<div style="padding:0.25rem 0;font-size:0.78rem;color:var(--ma-text-muted);margin-bottom:0.5rem;">${escapeHtml(state.currentDocPath)}</div>
      <div class="unified-markdown-panel">${state.currentDocContent
        ? renderMarkdown(state.currentDocContent)
        : `<p class="overlay-empty">加载中…</p>`}</div>`;
  }

  // List all docs
  let html = "";

  // New doc input
  html += `<div style="display:flex;gap:0.3rem;margin-bottom:0.5rem;">
    <input type="text" class="overlay-search-input" id="overlay-new-doc-input" placeholder="新建文档（如 需求分析.md）…" value="${escapeHtml(state.newDocName)}" style="margin-bottom:0;">
    <button type="button" class="unified-btn unified-btn-accent" id="overlay-new-doc-btn" style="flex-shrink:0;font-size:0.78rem;">新建</button>
  </div>`;

  if (!state.projectDocs.length) {
    html += `<p class="overlay-empty">暂无文档</p>`;
    return html;
  }

  // Sort: standard docs first
  const sorted = [...state.projectDocs].sort((a, b) => {
    if (a.is_standard && !b.is_standard) return -1;
    if (!a.is_standard && b.is_standard) return 1;
    return a.name.localeCompare(b.name);
  });

  html += `<div style="font-size:0.72rem;font-weight:600;color:var(--ma-text-muted);margin-bottom:0.25rem;">项目文档</div>`;
  for (const doc of sorted) {
    const icon = doc.is_standard ? "📋" : "📄";
    const sub = doc.name !== doc.path ? ` <span style="font-size:0.7rem;color:var(--ma-text-muted);">${escapeHtml(doc.path)}</span>` : "";
    html += `<button type="button" class="overlay-doc-item" data-doc-path="${escapeHtml(doc.path)}">
      <span>${icon} ${escapeHtml(doc.name)}</span>${sub}
    </button>`;
  }

  return html;
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
    state.planOverlay && state.planStatus !== "confirmed";
  const needsPlanDirtyBanner =
    state.planStatus === "plan_dirty" && !needsPlanConfirm;
  const isTaskLevelChange =
    state.changesLevel === "task" && !needsPlanConfirm && !needsPlanDirtyBanner;
  const needsScopeBanner = state.scopeNeedsReconfirm && state.planStatus === "confirmed";

  // Plan confirmation takes priority over change banner
  if (needsPlanConfirm) {
    const planLabel = state.planStatus === "plan_dirty"
      ? "计划已变更 · 请确认"
      : "计划待确认";
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
  if (isTaskLevelChange) {
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
        <span style="color:#d4a000">⚠ 计划已变更${pendingCount > 0 ? ` (${pendingCount} 项)` : ""}</span>
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
      changesHtml = "Phase 结构已变更，请确认后继续。";
    }

    const highlightLabel = state.highlightChanges ? "取消高亮" : "查看变更";
    return `<div class="sidebar-change-banner">
      <div class="sidebar-change-banner-title">⚠ 计划已变更 · 请确认</div>
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
): void {
  state.projectId = event.project_id ?? "";
  state.planStatus = event.plan_status ?? "draft";
  state.executionStage = isFlowStage(event.execution_stage) ? event.execution_stage : state.executionStage;
  state.executionStageReason = event.execution_stage_reason ?? state.executionStageReason;
  state.executionStageBlockers = event.execution_stage_blockers ?? state.executionStageBlockers;
  state.executionStageMissing = event.execution_stage_missing ?? state.executionStageBlockers;
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
}

export function applyProjectPlanState(
  state: ProjectPanelState,
  event: Extract<ServerEvent, { type: "project.plan.state" }>,
): void {
  state.projectId = event.project_id ?? "";
  state.planStatus = event.plan_status ?? "draft";
  state.executionStage = isFlowStage(event.execution_stage) ? event.execution_stage : state.executionStage;
  state.executionStageReason = event.execution_stage_reason ?? state.executionStageReason;
  state.executionStageBlockers = event.execution_stage_blockers ?? state.executionStageBlockers;
  state.executionStageMissing = event.execution_stage_missing ?? state.executionStageBlockers;
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
  state.planWarnings = event.warnings ?? [];
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
): void {
  state.threadsLoading = false;
  state.activeSessionId = event.active_session_id || "";
  state.threads = (event.threads || []).map((item) => ({
    sessionId: item.session_id,
    title: item.title || item.session_id,
    preview: item.preview || "",
    updatedAt: item.updated_at || "",
    archived: Boolean(item.archived),
  }));
}

export function isViewingArchivedThread(state: ProjectPanelState): boolean {
  if (!state.projectId || !state.activeSessionId || !state.currentSessionId) {
    return false;
  }
  return state.currentSessionId !== state.activeSessionId;
}

// ---- element refs (matches new DOM) ----

export function setupProjectPanel(container: HTMLElement): {
  sidebarTitle: HTMLElement;
  sidebarMeta: HTMLElement;
  sidebarProgressWrap: HTMLElement;
  sidebarProgressFill: HTMLElement;
  taskFlow: HTMLElement;
  servicesPanel: HTMLElement;
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
    sidebarTitle: el("project-sidebar-title"),
    sidebarMeta: el("project-sidebar-meta"),
    sidebarProgressWrap: el("project-sidebar-progress"),
    sidebarProgressFill: el("sidebar-progress-fill"),
    taskFlow: el("sidebar-task-flow"),
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
  return `<button type="button" class="sidebar-services-toggle" data-action="toggle-services">
      <span class="sidebar-services-toggle-chevron">${chevron}</span>
      <span>服务 · ${escapeHtml(summary)}</span>
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
  // header
  if (state.projectId) {
    els.sidebarTitle.textContent = `项目 · ${state.projectId}`;
    els.sidebarMeta.textContent = planStatusLabel(state);
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
  els.servicesPanel.innerHTML = renderServicesPanel(state);

  // --- banner area: single priority chain (UX-026: suggestions live in body — SP-9) ---
  // Priority: undo > partner busy > partner notices (non-adopted) > adopted footer >
  //           external > auto_fix > …
  let bannerHtml = "";
  const actionableSuggestions = state.suggestions.filter((s) => Boolean(s.action));
  const hasAdoptFlash = Boolean(state.suggestionAdoptFlash);

  if (state.codeFollowup) {
    bannerHtml = renderCodeFollowupBanner(state.codeFollowup);
  } else if (state.undoDescription) {
    bannerHtml = `<div class="sidebar-undo-toast">
      <span>${escapeHtml(state.undoDescription)}</span>
      <button type="button" class="unified-btn" data-action="undo-last" style="font-size:0.75rem;">撤销</button>
    </div>`;
  } else if (state.partnerBusy) {
    bannerHtml = renderPartnerNotices(state.partnerNotices || [], true, actionableSuggestions.length);
  } else if (state.adoptedFooterMessage && actionableSuggestions.length === 0 && !hasAdoptFlash) {
    bannerHtml = renderAdoptedFooterBanner(state.adoptedFooterMessage);
  } else if (
    state.partnerNotices &&
    state.partnerNotices.length > 0 &&
    actionableSuggestions.length === 0 &&
    !hasAdoptFlash &&
    isAdoptedPartnerNotice(state.partnerNotices)
  ) {
    bannerHtml = renderAdoptedNotice(state.partnerNotices);
  } else if (state.partnerNotices && state.partnerNotices.length > 0 && !isAdoptedPartnerNotice(state.partnerNotices)) {
    bannerHtml = renderPartnerNotices(state.partnerNotices, false, actionableSuggestions.length);
  } else if (state.externalChanges) {
    bannerHtml = `<div class="sidebar-change-banner" style="border-color:#d4a000;background:color-mix(in srgb, #d4a000 6%, var(--ma-surface));">
      <div class="sidebar-change-banner-title">检测到外部修改</div>
      <div class="sidebar-change-banner-changes">TASKS.md 被外部工具修改（git / 编辑器 等）。任务流已刷新为最新内容。</div>
      <button type="button" class="unified-btn" data-action="dismiss-external" style="font-size:0.72rem;padding:0.15rem 0.4rem;">关闭</button>
    </div>`;
  } else if (state.autoFixNotices.length > 0) {
    bannerHtml = renderAutoFixNotice(state.autoFixNotices);
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
      ? "项目管理器不可用，退回直接文件操作模式。勾选/排序仍可用，拆分和新任务需通过聊天框。"
      : "LLM 服务暂时不可用，拆分和新任务退回单条添加模式。勾选和排序正常。";
    bannerHtml = renderDegradeBanner(level, label, explain);
  } else if (state.planWarnings.length > 0) {
    const warnHtml = state.planWarnings
      .map((w) => `<div style="padding:0.2rem 0.75rem;font-size:0.78rem;color:#d4a000;">⚠ ${escapeHtml(w)}</div>`)
      .join("");
    bannerHtml = `<div class="sidebar-change-banner" style="border-color:#d4a000;background:color-mix(in srgb, #d4a000 6%, var(--ma-surface));">
      <div class="sidebar-change-banner-title">计划提示</div>
      ${warnHtml}
      <button type="button" class="unified-btn" data-action="dismiss-warnings" style="margin-top:0.3rem;font-size:0.72rem;">关闭</button>
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
      state.planStatus === "plan_dirty" ? "计划已变更 · 请确认" : "计划待确认";
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
