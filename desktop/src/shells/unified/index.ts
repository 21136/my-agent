import { setAgentBusy } from "../../agent-busy";
import { wireComposerAttachments } from "../../composer-attachments";
import { mountFileDrop } from "../../file-drop";
import { hydrateMermaid, renderMarkdown } from "../../markdown";
import { bindLazyMarkdownLookup, observeLazyMarkdown, resetLazyMarkdownObserver } from "../../lazy-markdown";
import { formatUserMessageHtml } from "../../user-message";
import { segmentChatBlocks, segmentPrint, renderTurnCardShell, liveTurnCardProcessBlocks, type ChatRenderSegment } from "../turn-card-layout";
import { activityEntriesPrint, ensureProcessEntries, isProcessThinkingLive, type ThinkEntry } from "../activity-state";
import { getProcessTools, renderActivityTimeline, syncThinkDom, timelineHasThinking } from "../activity-timeline";
import { createChatSession, escapeHtml, turnEndStatusText, checkerVerdictStatusText, formatToolElapsed, isConfirmInProgressLabel, type ChatBlock } from "../chat-state";
import { renderTopbar, type TopbarState } from "./topbar";
import { renderProposals, currentProposal, nextProposalIndex, type ProposalsState } from "./proposals";
import {
  setupProjectPanel,
  renderProjectSidebar,
  renderProjectGoalCard,
  deriveProjectGoalViewModel,
  deriveHeaderNextStepView,
  hasProjectBlocker,
  renderProjectBlockerDetails,
  getTaskChangeFingerprint,
  renderPlanTaskFlow,
  applyProjectStateEvent,
  resetProjectScopedState,
  shouldApplyProjectEvent,
  applyProjectListEvent,
  applyProjectPlanState,
  applyProjectThreadsEvent,
  isViewingArchivedThread,
  parseTasksMarkdown,
  type OverlayPanel,
  type FlowStage,
  type ProjectPanelState,
  type ProjectPanelCallbacks,
  type TaskItem,
} from "./project-panel";
import {
  actionableSuggestions,
  adoptPathFromSuggestion,
  clampReviewIndex,
  renderPlanFullHeader,
  renderPlanReviewPanel,
  type MainFocus,
} from "./plan-review";
import {
  adoptPathFromNotice,
  lastFailedTool,
  processPillLabel,
  renderAdoptChip,
  renderReviewCallout,
  renderToolFailAlert,
} from "./output-display";
import { renderConfirmCardHtml, summarizeConfirmPreview } from "./confirm-preview";
import { mountToolDisplayEditor, openToolDisplayEditor } from "../../copy/tool-display-editor";
import { onToolDisplayOverridesChange } from "../../copy/tool-display-overrides";
import "./unified.css";

export type Perspective = "default" | "project" | "night";

const FOCUS_TURNS = 2;
const RECALL_TURNS = 3;

function isRecallIntent(intent: string, intentLabel: string): boolean {
  return intent === "recall" || intentLabel.includes("回顾");
}

function recentTurnIndices(blocks: ChatBlock[], k: number): number[] {
  const turns = new Set<number>();
  for (const block of blocks) {
    if (
      block.kind === "user" ||
      block.kind === "assistant" ||
      block.kind === "assistant-streaming"
    ) {
      turns.add(block.turnIndex);
    }
  }
  return [...turns].sort((a, b) => b - a).slice(0, k);
}

function isRecentTurn(turnIndex: number, currentTurnIndex: number): boolean {
  return turnIndex >= currentTurnIndex - (FOCUS_TURNS - 1);
}

function computeInitialPerspective(
  _activeShell: string | undefined,
  projectRoot: string | undefined,
): Perspective {
  // Phase 34: workbench always uses project layout (sidebar on).
  if (projectRoot) return "project";
  return "project";
}

export function mountUnifiedShell(
  root: HTMLElement,
  client: AgentWsClient,
  shellId: string = "grow",
): () => void {
  // ---- perspective state (Phase 34: default = workbench / project layout) ----
  let perspective: Perspective = "project";
  let perspectiveLocked = true;

  function setPerspective(p: Perspective, reason: string = "manual"): void {
    if (perspectiveLocked && reason === "auto" && p !== "project") return;
    // Workbench keeps project layout; night is not an entry mode.
    if (p === "default") p = "project";
    perspective = p;
    shellEl.setAttribute("data-perspective", p === "night" ? "project" : p);
    sidebarEl.classList.toggle("hidden", false);
    updatePlaceholder();
    renderChat();
    renderConfirmGlass();
    syncWorkingVisual();
    refreshServices();
    updateWorkbenchEmpty();
  }

  // ---- ui state ----
  const topbarState: TopbarState = {
    proposals: [] as ProposalItem[],
    intentLabel: "",
    checkerLabel: "",
    memoryLabel: "",
    projectLabel: "",
    contextLabel: "",
    sessionCount: 0,
    headerCtas: [],
  };

  let sessionsDropdown: Array<{
    session_id: string;
    title: string;
    preview?: string;
    updated_at: string;
    message_count?: number;
    project_id?: string;
  }> = [];
  let sessionsOpen = false;
  let sessionsTab: "chat" | "project" = "chat";

  function syncTopbarFromProject(): void {
    if (projectState.projectId) {
      topbarState.projectLabel = projectState.projectId;
      topbarState.contextLabel = deriveProjectGoalViewModel(projectState).title;
      topbarState.headerCtas = deriveHeaderNextStepView(projectState).actions;
    } else {
      topbarState.projectLabel = "";
      topbarState.contextLabel = "";
      topbarState.headerCtas = [];
    }
  }

  function refreshTopbar(): void {
    syncTopbarFromProject();
    renderTopbar(
      topbarEl,
      topbarState,
      openProposals,
      handleNewChat,
      handleOpenSessions,
      handleNewProject,
      handleNewThread,
      handleHeaderNextStepAction,
    );
  }

  const proposalsState: ProposalsState = {
    proposals: [] as ProposalItem[],
    proposalIndex: 0,
    expandOpen: false,
  };

  const projectState: ProjectPanelState = {
    projectId: "",
    projectSummary: "",
    planStatus: "",
    tasksMarkdown: "",
    mapMarkdown: "",
    tasksDone: 0,
    tasksTotal: 0,
    tasksAllDone: false,
    planOverlay: null,
    projects: [],
    switchOverlay: null,
    switchInProgress: false,
    pendingPickerId: "",
    overlayPanel: null,
    taskPhases: [],
    planBannerCollapsed: true,
    switchConfirmTarget: null,
    projectSearchQuery: "",
    planChangeLog: [],
    changeTimeline: [],
    executionStage: null,
    workflowStage: null,
    needsDesignConfirm: false,
    executionStageStatus: "ready",
    executionStageReason: "",
    executionStageBlockers: [],
    executionStageMissing: [],
    executionStageWarnings: [],
    executionStageAffected: [],
    executionStageDeferred: [],
    executionStageArtifacts: [],
    taskSnapshot: { lines: new Set(), lineTexts: new Map() },
    highlightChanges: false,
    changeTimelineExpanded: false,
    highlightedLines: new Set(),
    projectDocs: [],
    currentDocPath: "",
    currentDocContent: "",
    newDocName: "",
    quickAddText: "",
    detectedProject: null,
    planWarnings: [],
    planWarningsPending: [],
    dismissedWarningFingerprint: "",
    planWarningStage: null,
    undoDescription: "",
    undoTimerId: null,
    degradationLevel: "L1",
    degradationLabel: "全功能",
    changesLevel: null,
    dismissedTaskChangeFingerprint: "",
    autoConfirmTimerId: null,
    externalChanges: false,
    suggestions: [],
    autoFixNotices: [],
    operationalNotices: [],
    dismissedOperationalNoticeFingerprint: "",
    partnerNotices: [],
    partnerBusy: false,
    nextTask: null,
    nextTaskLine: null,
    services: [],
    servicesLoading: false,
    servicesError: "",
    servicesLogName: "",
    servicesLogText: "",
    terminalSessions: [],
    terminalsLoading: false,
    terminalsError: "",
    terminalsCollapsed: true,
    terminalDetails: null,
    terminalOutput: "",
    terminalOutputCursor: 0,
    terminalOutputLoading: false,
    terminalOutputError: "",
    terminalOutputCursorReset: false,
    terminalOutputTruncated: false,
    turnArmedId: "",
    turnArmedText: "",
    turnEvidence: [],
    turnGateNotice: "",
    turnPostcondition: "none",
    turnCircuitOpen: [],
    turnPlaybookId: "",
    turnFailureClass: "",
    activeSessionId: "",
    threads: [],
    threadsLoading: false,
    currentSessionId: "",
    mainFocus: "chat",
    reviewFocusId: null,
    servicesCollapsed: true,
    suggestionAdoptFlash: null,
    adoptedFooterMessage: null,
    adoptPendingId: null,
    turnInProgress: false,
    deliveryProfile: "solo",
    reviewVerdict: null,
    reviewBlockersCount: 0,
    reviewProgressBlocked: false,
    scopeConfirmedAt: "",
    runawayEnabled: false,
    runawayStatus: "",
    runawayCheckpoint: "",
    runawayRepairCount: 0,
    runawayLastVerification: null,
    runawayPausedReason: null,
    runawayAcceptancePassed: false,
    runawayVerificationEvidence: null,
    runawayVerificationEvidencePath: null,
    runawayVersion: 0,
    runawayPhase: "",
    runawayUserLine: "",
    runawayMode: "auto",
    runawayBlocked: false,
    runawayChecklist: null,
    runawayCancelAvailable: false,
    runawayBlock: null,
    scopeNeedsReconfirm: false,
    flowPreviewStage: null,
    milestoneAccepted: false,
    milestoneAcceptedAt: null,
    codeFollowup: null,
    nextTurnChangeSummary: null,
    dropTaskPendingId: null,
    dropTaskPendingWorking: false,
  };

  let suggestionAdoptFlashTimerId: number | null = null;
  let pendingAdoptAcceptTimeoutId: number | null = null;
  let pendingAdoptAccept: { sid: string; path: string; whatChanged: string } | null = null;
  let acceptedChangeForNextTurn: string | null = null;
  const ADOPT_FLASH_MS = 1500;
  const ADOPT_PENDING_MS = 30000;

  let statusText = "连接中…";
  let cancelledStatusTimer: number | null = null;
  let cancelSafetyTimer: number | null = null;
  let destroyed = false;
  let renderThrottleTimer: number | null = null;
  let terminalPollTimerId: number | null = null;
  let terminalRequestTimeoutId: number | null = null;
  let terminalRequestSerial = 0;
  let terminalRequestId: string | null = null;
  let terminalOutputRequestTimeoutId: number | null = null;
  let terminalOutputRequestSerial = 0;
  let terminalOutputRequestId: string | null = null;
  let terminalOutputRequestSessionId: string | null = null;
  let renderedPrints: string[] = [];
  let renderedSegmentPrints: string[] = [];
  /** UI-5972 M4: full history load defers markdown; incremental paths render eagerly. */
  let markdownRenderEager = true;
  /** turnKey → user expanded the folded early tool list (UX-023). */
  const toolsListExpanded = new Set<string>();
  // night perspective state
  let recallHighlightTurns = new Set<number>();
  let focusObserver: IntersectionObserver | null = null;

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

  function clearCancelSafety(): void {
    if (cancelSafetyTimer !== null) {
      window.clearTimeout(cancelSafetyTimer);
      cancelSafetyTimer = null;
    }
  }

  // ---- chat session ----
  const chat = createChatSession(
    { showProcess: true, confirmInBlocks: true },
    {
      onChange: () => {
        renderChat();
        renderConfirmGlass();
        syncWorkingVisual();
      },
      onHistoryLoaded: () => {
        renderedPrints = [];
        renderedSegmentPrints = [];
        resetLazyMarkdownObserver();
        syncWorkingVisual();
        if (!chat.model.confirmPending) {
          setStatus("就绪");
        }
        scrollChatToBottom();
      },
      onTurnStart: (event) => {
        projectState.runawayCancelAvailable = false;
        clearRunawayResumePending();
        resetProcessJumpCursor();
        syncWorkingVisual();
        resetTurnCacheStats();
        if (acceptedChangeForNextTurn) {
          projectState.nextTurnChangeSummary = acceptedChangeForNextTurn;
          acceptedChangeForNextTurn = null;
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
        topbarState.intentLabel = event.intent_label;
        topbarState.checkerLabel = "";
        if (perspective === "night" && isRecallIntent(event.intent, event.intent_label)) {
          recallHighlightTurns = new Set(recentTurnIndices(chat.model.blocks, RECALL_TURNS));
          renderChat();
          requestAnimationFrame(() => scrollToRecallTurns());
        }
        refreshTopbar();
        setStatus(event.intent_label);
        syncToolElapsedTimer();
        requestAnimationFrame(() => scrollToBottomIfNear());
      },
      onLlmPending: () => {
        syncToolElapsedTimer();
        requestAnimationFrame(() => scrollToBottomIfNear());
      },
      onCheckerVerdict: (event) => {
        topbarState.checkerLabel = checkerVerdictStatusText(event.verdict);
        refreshTopbar();
        setStatus(topbarState.checkerLabel);
      },
      onConfirmRequest: () => {
        setComposerEnabled(false);
        setStatus("等待确认…");
        requestAnimationFrame(() => {
          requestAnimationFrame(() => scrollPendingConfirmIntoView());
        });
      },
      onConfirmDone: (choice) => {
        setComposerEnabled(choice !== "cancelled");
        if (choice === "cancelled") {
          setStatus("正在停止…");
          return;
        }
        if (choice === "n" || choice === "timeout" || choice === "stale") {
          setStatus(chat.isWorking() ? "处理中…" : "就绪");
        } else {
          setStatus("执行中…");
        }
      },
      onToolStart: () => {
        setStatus("处理中…");
      },
      onToolEnd: () => {
        if (!chat.model.confirmPending) {
          setStatus(chat.isWorking() ? "处理中…" : "就绪");
        }
      },
      onAssistantDone: () => {
        recallHighlightTurns = new Set();
        if (chat.model.cancelRequested) return;
        if (!chat.model.confirmPending) {
          setStatus("就绪");
        }
      },
      onTurnEnd: (_ok, finishReason) => {
        clearRunawayResumePending();
        syncWorkingVisual();
        clearCancelSafety();
        setComposerEnabled(true);
        if (!chat.model.confirmPending) {
          setTurnEndStatus(finishReason);
        }
        if (perspective === "project") {
          refreshServices();
        }
        // UX-017: notify when user switched away
        if (document.visibilityState !== "visible" && ("Notification" in window)) {
          if (Notification.permission === "granted") {
            new Notification("my-agent", { body: "已回复", silent: true });
          } else if (Notification.permission === "default") {
            Notification.requestPermission().then((perm) => {
              if (perm === "granted") {
                new Notification("my-agent", { body: "已回复", silent: true });
              }
            });
          }
        }
      },
      onCancelTimeout: () => {
        setComposerEnabled(true);
        setTurnEndStatus("cancelled");
      },
      onError: () => {
        setComposerEnabled(true);
        setStatus("错误");
      },
    },
  );

  bindLazyMarkdownLookup((turnIndex) => {
    const block = chat.model.blocks.find(
      (b) =>
        (b.kind === "assistant" || b.kind === "assistant-streaming")
        && b.turnIndex === turnIndex,
    );
    if (!block || block.kind === "assistant-streaming") return null;
    return block.text;
  });

  // ---- DOM layout ----
  root.innerHTML = `
    <div class="unified-shell" data-perspective="project">
      <aside class="unified-sidebar" id="unified-sidebar">
        <div class="sidebar-resize-handle" id="sidebar-resize-handle"></div>
        <div class="unified-sidebar-header">
          <div class="unified-sidebar-title" id="project-sidebar-title">项目</div>
          <div class="unified-sidebar-meta" id="project-sidebar-meta">未绑定项目</div>
          <div class="sidebar-progress-bar-wrap hidden" id="project-sidebar-progress">
            <div class="sidebar-progress-bar-fill" id="sidebar-progress-fill" style="width:0%"></div>
          </div>
        </div>
        <div class="sidebar-task-flow" id="sidebar-task-flow"></div>
        <div id="sidebar-terminals"></div>
        <div class="sidebar-services" id="sidebar-services"></div>
        <div class="sidebar-footer" id="sidebar-footer">
          <div class="sidebar-change-banner hidden" id="sidebar-change-banner"></div>
          <div class="sidebar-icon-bar" id="sidebar-icon-bar">
            <button type="button" class="sidebar-icon-btn is-active" data-panel="tasks" title="当下"><span class="sidebar-icon-label">当下</span></button>
            <button type="button" class="sidebar-icon-btn" data-panel="plan" title="任务"><span class="sidebar-icon-label">任务</span></button>
            <button type="button" class="sidebar-icon-btn" data-panel="docs" title="文档"><span class="sidebar-icon-label">文档</span></button>
            <button type="button" class="sidebar-icon-btn" data-panel="threads" title="会话线" id="icon-btn-threads">
              <span class="sidebar-icon-label">会话</span>
              <span class="sidebar-icon-badge" id="thread-count-badge">0</span>
            </button>
            <button type="button" class="sidebar-icon-btn" data-panel="projects" title="我的项目" id="icon-btn-projects">
              <span class="sidebar-icon-label">项目</span>
              <span class="sidebar-icon-badge" id="project-count-badge">0</span>
            </button>
            <span class="sidebar-degrade-dot hidden" id="sidebar-degrade-dot" data-action="toggle-degrade-info" title="项目管理器状态"></span>
          </div>
        </div>
        <div class="sidebar-overlay hidden" id="sidebar-overlay">
          <div class="sidebar-overlay-header">
            <button type="button" class="sidebar-overlay-back" id="overlay-back-btn">← 返回</button>
            <span class="sidebar-overlay-title" id="overlay-title"></span>
          </div>
          <div class="sidebar-overlay-body" id="overlay-body"></div>
        </div>
        <!-- compat: hidden elements for old event wiring -->
        <div id="project-picker-list" class="hidden"></div>
        <div id="project-picker-refresh" class="hidden"></div>
        <div class="hidden" id="project-switch-card"><div id="project-switch-title"></div><p id="project-switch-message"></p><div><button id="project-switch-confirm"></button><button id="project-switch-cancel"></button></div></div>
        <div class="hidden" id="project-plan-card"><div id="project-plan-title"></div><pre id="project-plan-preview"></pre><div><button id="project-plan-confirm"></button><button id="project-plan-edit"></button></div></div>
        <div class="hidden" id="project-sidebar-tabs"></div>
        <div class="hidden" id="project-panel-tasks"></div>
        <div class="hidden" id="project-panel-map"></div>
      </aside>
      <div class="unified-main">
        <header class="unified-topbar" id="unified-topbar"></header>
        <div class="thread-archive-banner hidden" id="thread-archive-banner">
          <span class="thread-archive-banner-text">归档线 · 只读回看（不会改回活线）</span>
          <button type="button" class="unified-btn" id="thread-return-active">回到活线</button>
        </div>
        <section class="unified-context-region hidden" id="unified-goal-card" aria-label="项目上下文"></section>
        <div class="workbench-empty" id="workbench-empty" hidden>
          <div class="workbench-empty-card">
            <h2 class="workbench-empty-title">选择或新建项目</h2>
            <p class="workbench-empty-copy">打开应用即工作台。先选一个项目，再开始对话与改代码。</p>
            <div class="workbench-empty-actions">
              <button type="button" class="unified-btn unified-btn-accent" id="empty-new-project">新建项目</button>
              <button type="button" class="unified-btn" id="empty-pick-project">我的项目</button>
            </div>
            <button type="button" class="workbench-empty-free-chat" id="empty-free-chat">先聊聊</button>
          </div>
        </div>
        <div class="unified-stage" id="unified-stage">
          <main class="unified-chat" id="unified-chat"></main>
          <section class="unified-plan-review hidden" id="unified-plan-review" aria-label="方案变更"></section>
          <section class="unified-plan-full hidden" id="unified-plan-full" aria-label="完整计划"></section>
          <section class="unified-document hidden" id="unified-document" aria-label="文档阅读"></section>
        </div>
        <div class="unified-composer-dock" id="unified-composer-dock">
          <section class="unified-expand hidden" id="unified-expand"></section>
          <div class="unified-composer-meta" id="unified-composer-meta">
            <div class="unified-status" id="unified-status"></div>
            <div class="unified-token-bar hidden" id="unified-token-bar"></div>
          </div>
          <footer class="unified-composer" id="unified-composer">
            <button type="button" class="unified-btn" id="unified-stop" hidden>停止</button>
            <textarea class="unified-input" id="unified-input" rows="1" placeholder="输入消息，或拖入/粘贴文件…"></textarea>
            <button type="button" class="unified-btn unified-btn-accent" id="unified-send">发送</button>
          </footer>
        </div>
      </div>
      <div class="unified-confirm-glass hidden" id="unified-confirm-glass" role="dialog" aria-modal="true"></div>
      <div class="unified-confirm-glass hidden" id="workbench-dialog" role="dialog" aria-modal="true"></div>
    </div>
  `;

  // ---- element refs ----
  const shellEl = root.querySelector<HTMLElement>(".unified-shell")!;
  const sidebarEl = root.querySelector<HTMLElement>("#unified-sidebar")!;
  const topbarEl = root.querySelector<HTMLElement>("#unified-topbar")!;
  const expandEl = root.querySelector<HTMLElement>("#unified-expand")!;
  const chatEl = root.querySelector<HTMLElement>("#unified-chat")!;
  const planReviewEl = root.querySelector<HTMLElement>("#unified-plan-review")!;
  const planFullEl = root.querySelector<HTMLElement>("#unified-plan-full")!;
  const documentEl = root.querySelector<HTMLElement>("#unified-document")!;
  const workbenchEmptyEl = root.querySelector<HTMLElement>("#workbench-empty")!;
  const emptyNewBtn = root.querySelector<HTMLButtonElement>("#empty-new-project")!;
  const emptyPickBtn = root.querySelector<HTMLButtonElement>("#empty-pick-project")!;
  const emptyFreeChatBtn = root.querySelector<HTMLButtonElement>("#empty-free-chat")!;
  const composerDock = root.querySelector<HTMLElement>("#unified-composer-dock")!;
  const composerMetaEl = root.querySelector<HTMLElement>("#unified-composer-meta")!;
  const statusEl = root.querySelector<HTMLElement>("#unified-status")!;
  const composer = root.querySelector<HTMLElement>("#unified-composer")!;
  const input = root.querySelector<HTMLTextAreaElement>("#unified-input")!;
  const stopBtn = root.querySelector<HTMLButtonElement>("#unified-stop")!;
  const sendBtn = root.querySelector<HTMLButtonElement>("#unified-send")!;
  const confirmGlass = root.querySelector<HTMLElement>("#unified-confirm-glass")!;
  const workbenchDialogEl = root.querySelector<HTMLElement>("#workbench-dialog")!;
  const tokenBar = root.querySelector<HTMLElement>("#unified-token-bar")!;
  const threadArchiveBannerEl = root.querySelector<HTMLElement>("#thread-archive-banner")!;
  const threadReturnActiveBtn = root.querySelector<HTMLButtonElement>("#thread-return-active")!;
  let planReviewIndex = 0;

  let turnCachePromptTotal = 0;
  let turnCacheCachedTotal = 0;
  let lastLlmCacheRatio: number | undefined;
  let lastTokenUsage: number | undefined;
  let lastTokenLimit: number | undefined;

  function resetTurnCacheStats(): void {
    turnCachePromptTotal = 0;
    turnCacheCachedTotal = 0;
    lastLlmCacheRatio = undefined;
  }

  function recordLlmUsage(promptTokens: number, cachedTokens: number, cacheRatio: number): void {
    turnCachePromptTotal += promptTokens;
    turnCacheCachedTotal += cachedTokens;
    lastLlmCacheRatio = cacheRatio;
    updateTokenBar(lastTokenUsage, lastTokenLimit);
  }

  // project panel elements (only used in project perspective)
  const projectEls = setupProjectPanel(root);

  // ---- resize handle ----
  const resizeHandle = root.querySelector<HTMLElement>("#sidebar-resize-handle")!;
  let resizeDragging = false;

  function loadSidebarWidth(): number {
    try {
      const raw = localStorage.getItem("sidebar-width");
      if (raw) {
        const n = parseInt(raw, 10);
        if (n >= 200 && n <= window.innerWidth * 0.5) return n;
      }
    } catch { /* ignore */ }
    return 280;
  }

  function saveSidebarWidth(w: number): void {
    try { localStorage.setItem("sidebar-width", String(w)); } catch { /* ignore */ }
  }

  function applySidebarWidth(w: number): void {
    shellEl.style.setProperty("--sidebar-width", `${w}px`);
  }

  function clampSidebarWidth(w: number): number {
    return Math.max(200, Math.min(Math.round(w), Math.floor(window.innerWidth * 0.5)));
  }

  applySidebarWidth(loadSidebarWidth());

  resizeHandle.addEventListener("mousedown", (ev) => {
    ev.preventDefault();
    resizeDragging = true;
    resizeHandle.classList.add("is-dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });

  document.addEventListener("mousemove", (ev) => {
    if (!resizeDragging) return;
    const rect = shellEl.getBoundingClientRect();
    const w = clampSidebarWidth(ev.clientX - rect.left);
    applySidebarWidth(w);
  });

  document.addEventListener("mouseup", () => {
    if (!resizeDragging) return;
    resizeDragging = false;
    resizeHandle.classList.remove("is-dragging");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    const w = parseInt(
      shellEl.style.getPropertyValue("--sidebar-width").replace("px", ""),
      10,
    );
    if (!isNaN(w)) saveSidebarWidth(w);
  });

  // Q4: grow 无绑项目会话（空态「先聊聊」或恢复的无项目会话）
  let freeChatActive = false;

  function isWorkbenchChatAllowed(): boolean {
    if (isViewingArchivedThread(projectState)) return false;
    return Boolean(projectState.projectId) || freeChatActive;
  }

  function syncFreeChatFromSession(hasProject: boolean, sessionId: string): void {
    freeChatActive = !hasProject && Boolean(sessionId);
  }

  function updatePlaceholder(): void {
    if (isViewingArchivedThread(projectState)) {
      input.placeholder = "归档线只读回看；点顶栏「回到活线」继续工作";
      return;
    }
    if (freeChatActive && !projectState.projectId) {
      input.placeholder = "普通对话：可问答或造工具；写项目代码请先新建/选择项目";
      return;
    }
    if (!projectState.projectId) {
      input.placeholder = "先选择或新建项目…";
      return;
    }
    input.placeholder =
      "输入消息或拖入/粘贴文件；改计划会自动交给计划搭档";
  }

  function syncArchivedViewUi(): void {
    const archived = isViewingArchivedThread(projectState);
    threadArchiveBannerEl.classList.toggle("hidden", !archived);
    updatePlaceholder();
    composerWire.syncSendEnabled();
  }

  function updateWorkbenchEmpty(): void {
    const showEmpty = !projectState.projectId && !freeChatActive;
    workbenchEmptyEl.hidden = !showEmpty;
    chatEl.classList.toggle("is-empty-gated", showEmpty);
    composer.classList.toggle("is-empty-gated", showEmpty);
    updatePlaceholder();
    composerWire.syncSendEnabled();
  }

  // ---- file drop + composer ----
  const fileDrop = mountFileDrop({
    composer,
    pasteTargets: [composer, input],
    client,
    shell: shellId,
    canAccept: () => {
      if (chat.model.confirmPending) return false;
      if (!projectState.projectId) return false;
      if (isViewingArchivedThread(projectState)) return false;
      return true;
    },
    onChange: () => composerWire.syncSendEnabled(),
    onNotice: (text) => setStatus(text),
  });

  const composerWire = wireComposerAttachments({
    input,
    sendBtn,
    client,
    chat,
    fileDrop,
    onStatus: (text) => setStatus(text),
    allowSend: () => isWorkbenchChatAllowed(),
    beforeSend: () => {
      topbarState.intentLabel = "";
      setStatus("发送中…");
      resetUserScrollPin();
    },
  });

  // ---- visual sync ----
  let lastSidebarWorking = false;
  let runawayResumePending = false;
  let runawayResumeTimer: number | null = null;
  let processJumpCursor = -1;
  let processJumpTurnIndex = -1;
  let lastRunawayNudgeAt = 0;
  let runawayIdleResumeTimer: number | null = null;

  const RUNAWAY_IDLE_RESUME_MS = 2500;
  const RUNAWAY_NUDGE_DEBOUNCE_MS = 4000;

  function clearRunawayIdleResume(): void {
    if (runawayIdleResumeTimer !== null) {
      window.clearTimeout(runawayIdleResumeTimer);
      runawayIdleResumeTimer = null;
    }
  }

  function scheduleRunawayIdleResume(): void {
    clearRunawayIdleResume();
    const canAutoResume = () => {
      if (
        !projectState.runawayEnabled
        || projectState.runawayBlocked
        || projectState.runawayCancelAvailable
        || chat.isWorking()
      ) {
        return false;
      }
      // Runaway v2 performs continuation in the backend controller. The
      // legacy desktop idle watchdog must not become a second owner.
      if (projectState.runawayVersion >= 2) {
        return false;
      }
      return !["paused", "completed", "release_wait"].includes(projectState.runawayCheckpoint);
    };
    if (!canAutoResume()) {
      return;
    }
    runawayIdleResumeTimer = window.setTimeout(() => {
      runawayIdleResumeTimer = null;
      if (!canAutoResume()) {
        return;
      }
      resumeRunawayFromUi("狂奔待命，正在自动续接…", { force: true });
    }, RUNAWAY_IDLE_RESUME_MS);
  }

  function resetProcessJumpCursor(): void {
    processJumpCursor = -1;
    processJumpTurnIndex = -1;
  }

  function clearRunawayResumePending(): void {
    runawayResumePending = false;
    if (runawayResumeTimer !== null) {
      window.clearTimeout(runawayResumeTimer);
      runawayResumeTimer = null;
    }
    clearRunawayIdleResume();
  }

  function syncWorkingVisual(): void {
    const working = chat.isWorking() || runawayResumePending || projectState.runawayCancelAvailable;
    const workingChanged = lastSidebarWorking !== working;
    lastSidebarWorking = working;
    projectState.turnInProgress = working;
    projectEls.goalCard.classList.toggle("hidden", !projectState.projectId);
    projectEls.goalCard.dataset.goalStatus = deriveProjectGoalViewModel(projectState).status;
    projectEls.goalCard.innerHTML = renderProjectGoalCard(projectState);
    shellEl.classList.toggle("is-working", working);
    statusEl.classList.toggle("is-idle", statusText === "就绪" && !working);
    syncComposerMetaVisibility();
    stopBtn.hidden = !(working || chat.model.confirmPending);
    stopBtn.disabled = chat.model.cancelRequested;
    setAgentBusy(working, perspective === "project" ? "project" : "grow");
    if (perspective === "project" && workingChanged) {
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      refreshTopbar();
    }
  }

  function sendStopRequest(): void {
    const activeTurn = chat.isWorking() || chat.model.confirmPending;
    if (activeTurn) {
      if (!chat.requestCancel()) return;
      setComposerEnabled(false);
      setStatus("正在停止…");
      clearCancelSafety();
      cancelSafetyTimer = window.setTimeout(() => {
        if (chat.model.cancelRequested) {
          chat.model.cancelRequested = false;
          chat.model.confirmPending = false;
          setComposerEnabled(true);
          setStatus("就绪");
          syncWorkingVisual();
        }
        cancelSafetyTimer = null;
      }, 3000);
      try {
        client.sendTurnCancel();
      } catch (err) {
        clearCancelSafety();
        chat.model.cancelRequested = false;
        setComposerEnabled(!chat.model.confirmPending);
        setStatus(`停止发送失败：${err instanceof Error ? err.message : String(err)}`);
        syncWorkingVisual();
      }
      return;
    }
    if (!projectState.runawayCancelAvailable) return;
    projectState.runawayCancelAvailable = false;
    syncWorkingVisual();
    setStatus("正在停止狂奔续接…");
    try {
      client.sendTurnCancel();
    } catch (err) {
      projectState.runawayCancelAvailable = true;
      syncWorkingVisual();
      setStatus(`停止续接失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function useTurnCardLayout(): boolean {
    return perspective !== "night";
  }

  function syncComposerMetaVisibility(): void {
    const statusVisible =
      !statusEl.hidden && !statusEl.classList.contains("is-idle");
    const tokenVisible =
      !tokenBar.hidden && !tokenBar.classList.contains("hidden");
    const showMeta = statusVisible || tokenVisible;
    composerMetaEl.classList.toggle("hidden", !showMeta);
    composerMetaEl.hidden = !showMeta;
  }

  function setStatus(text: string): void {
    statusText = text;
    statusEl.textContent = text;
    const idle = text === "就绪" && !shellEl.classList.contains("is-working");
    statusEl.classList.toggle("is-idle", idle);
    syncComposerMetaVisibility();
  }

  function setTurnEndStatus(finishReason: string): void {
    if (cancelledStatusTimer !== null) {
      window.clearTimeout(cancelledStatusTimer);
      cancelledStatusTimer = null;
    }
    const label = turnEndStatusText(finishReason);
    if (label === null) {
      setStatus("就绪");
      return;
    }
    setStatus(label);
    cancelledStatusTimer = window.setTimeout(() => {
      if (!chat.isWorking() && !chat.model.confirmPending) setStatus("就绪");
      cancelledStatusTimer = null;
    }, 2000);
  }

  function updateTokenBar(usage: number | undefined, limit: number | undefined): void {
    // Ignore spurious zero from backend before deferred messages.jsonl is loaded.
    if (usage === 0 && lastTokenUsage !== undefined && lastTokenUsage > 0) {
      usage = lastTokenUsage;
    }
    if (usage !== undefined) lastTokenUsage = usage;
    if (limit !== undefined) lastTokenLimit = limit;
    if (usage === undefined || limit === undefined || limit <= 0) {
      if (lastLlmCacheRatio === undefined && turnCachePromptTotal <= 0) {
        tokenBar.classList.add("hidden");
        syncComposerMetaVisibility();
        return;
      }
    }
    tokenBar.classList.remove("hidden");
    const ratio = usage !== undefined && limit !== undefined && limit > 0 ? usage / limit : 0;
    let cls = "unified-token-bar";
    if (limit !== undefined && limit > 0) {
      if (ratio >= 0.95) cls += " unified-token-red";
      else if (ratio >= 0.85) cls += " unified-token-yellow";
    }
    const parts: string[] = [];
    if (usage !== undefined && limit !== undefined && limit > 0) {
      const usageK = Math.round(usage / 1000);
      const limitK = Math.round(limit / 1000);
      parts.push(`${usageK}k / ${limitK}k tokens`);
    }
    const turnRatio =
      turnCachePromptTotal > 0 ? turnCacheCachedTotal / turnCachePromptTotal : undefined;
    if (turnRatio !== undefined && turnCachePromptTotal > 0) {
      parts.push(`回合缓存 ${Math.round(turnRatio * 100)}%`);
      if (turnRatio >= 0.5) cls += " unified-token-cache-good";
    } else if (lastLlmCacheRatio !== undefined) {
      parts.push(`缓存 ${Math.round(lastLlmCacheRatio * 100)}%`);
      if (lastLlmCacheRatio >= 0.5) cls += " unified-token-cache-good";
    }
    tokenBar.className = cls;
    tokenBar.textContent = parts.join(" · ");
    tokenBar.title =
      turnRatio !== undefined
        ? `本回合 LLM 累计：${turnCacheCachedTotal} / ${turnCachePromptTotal} prompt tokens 来自缓存`
        : "最近一次 LLM 调用的 prompt 缓存命中率";
    syncComposerMetaVisibility();
  }

  function setComposerEnabled(enabled: boolean): void {
    input.disabled = !enabled;
    composer.classList.toggle("disabled", !enabled);
    if (enabled) {
      composerWire.syncSendEnabled();
    } else {
      sendBtn.disabled = true;
    }
  }

  async function handleNewThread(): Promise<void> {
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再新开线");
      return;
    }
    if (!projectState.projectId) {
      setStatus("请先打开项目");
      return;
    }
    const ok = await showWorkbenchConfirm(
      "将为当前项目新开一条会话线（聊天区清空）；当前活线将归档，可在侧栏「会话线」回看。继续？",
    );
    if (!ok) return;
    try {
      client.newProjectThread(projectState.projectId);
      setStatus("正在新开线…");
    } catch (err) {
      setStatus(`新开线失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function refreshProjectThreads(): void {
    if (!projectState.projectId) return;
    if (refreshProjectThreadsTimer !== null) {
      window.clearTimeout(refreshProjectThreadsTimer);
    }
    refreshProjectThreadsTimer = window.setTimeout(() => {
      refreshProjectThreadsTimer = null;
      refreshProjectThreadsNow();
    }, 0);
  }

  let refreshProjectThreadsTimer: number | null = null;
  let listSessionsTimer: number | null = null;
  let listProjectsTimer: number | null = null;
  /** UI-5972: suppress redundant list* until switch/open batch finishes (session.history). */
  let pendingSwitchBatch = false;
  let hydrationSessionId = "";

  function shortSessionId(sessionId: string): string {
    const trimmed = sessionId.trim();
    if (trimmed.length <= 18) return trimmed;
    return `${trimmed.slice(0, 18)}…`;
  }

  function startHydrationWait(sessionId: string, label: string): void {
    hydrationSessionId = sessionId;
    pendingSwitchBatch = true;
    projectState.switchInProgress = true;
    setStatus(`${label} ${shortSessionId(sessionId)}…`);
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
  }

  function completeHydrationWait(sessionId?: string): void {
    if (sessionId && hydrationSessionId && sessionId !== hydrationSessionId) return;
    if (!pendingSwitchBatch && !hydrationSessionId) return;
    hydrationSessionId = "";
    pendingSwitchBatch = false;
    projectState.switchInProgress = false;
    debouncedListProjects();
    debouncedListSessions();
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
  }

  function debouncedListSessions(): void {
    if (pendingSwitchBatch) return;
    if (listSessionsTimer !== null) window.clearTimeout(listSessionsTimer);
    listSessionsTimer = window.setTimeout(() => {
      listSessionsTimer = null;
      client.listSessions();
    }, 250);
  }

  function debouncedListProjects(): void {
    if (listProjectsTimer !== null) window.clearTimeout(listProjectsTimer);
    listProjectsTimer = window.setTimeout(() => {
      listProjectsTimer = null;
      client.listProjects();
    }, 250);
  }

  function refreshProjectThreadsNow(): void {
    if (!projectState.projectId) return;
    projectState.threadsLoading = true;
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
    try {
      client.listProjectThreads(projectState.projectId);
    } catch (err) {
      projectState.threadsLoading = false;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      setStatus(`加载会话线失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // ---- workbench dialog (Electron does not support window.prompt/confirm) ----
  type WorkbenchDialogState =
    | { kind: "prompt"; title: string; value: string; resolve: (value: string | null) => void }
    | { kind: "confirm"; title: string; resolve: (value: boolean) => void };

  let workbenchDialogState: WorkbenchDialogState | null = null;

  function closeWorkbenchDialog(): void {
    workbenchDialogState = null;
    workbenchDialogEl.classList.add("hidden");
    workbenchDialogEl.innerHTML = "";
  }

  function renderWorkbenchDialog(): void {
    if (!workbenchDialogState) {
      closeWorkbenchDialog();
      return;
    }
    workbenchDialogEl.classList.remove("hidden");
    if (workbenchDialogState.kind === "confirm") {
      workbenchDialogEl.innerHTML = `
        <div class="unified-confirm-glass-card" role="document">
          <div class="unified-confirm-glass-title">${escapeHtml(workbenchDialogState.title)}</div>
          <div class="unified-confirm-glass-actions">
            <button type="button" class="unified-btn unified-btn-accent" id="workbench-dialog-ok">确定</button>
            <button type="button" class="unified-btn" id="workbench-dialog-cancel">取消</button>
          </div>
        </div>
      `;
      return;
    }
    workbenchDialogEl.innerHTML = `
      <div class="unified-confirm-glass-card" role="document">
        <div class="unified-confirm-glass-title">${escapeHtml(workbenchDialogState.title)}</div>
        <input
          type="text"
          class="workbench-dialog-input"
          id="workbench-dialog-input"
          value="${escapeHtml(workbenchDialogState.value)}"
          autocomplete="off"
          spellcheck="false"
        />
        <div class="unified-confirm-glass-actions">
          <button type="button" class="unified-btn unified-btn-accent" id="workbench-dialog-ok">确定</button>
          <button type="button" class="unified-btn" id="workbench-dialog-cancel">取消</button>
        </div>
      </div>
    `;
    const inputEl = workbenchDialogEl.querySelector<HTMLInputElement>("#workbench-dialog-input");
    inputEl?.focus();
    inputEl?.select();
  }

  function showWorkbenchPrompt(title: string): Promise<string | null> {
    return new Promise((resolve) => {
      workbenchDialogState = { kind: "prompt", title, value: "", resolve };
      renderWorkbenchDialog();
    });
  }

  function showWorkbenchConfirm(title: string): Promise<boolean> {
    return new Promise((resolve) => {
      workbenchDialogState = { kind: "confirm", title, resolve };
      renderWorkbenchDialog();
    });
  }

  workbenchDialogEl.addEventListener("click", (ev) => {
    if (!workbenchDialogState) return;
    const target = ev.target as HTMLElement;
    if (target.id === "workbench-dialog-cancel") {
      const state = workbenchDialogState;
      closeWorkbenchDialog();
      if (state.kind === "confirm") state.resolve(false);
      else state.resolve(null);
      return;
    }
    if (target.id !== "workbench-dialog-ok") return;
    const state = workbenchDialogState;
    if (state.kind === "confirm") {
      closeWorkbenchDialog();
      state.resolve(true);
      return;
    }
    const inputEl = workbenchDialogEl.querySelector<HTMLInputElement>("#workbench-dialog-input");
    const value = inputEl?.value ?? "";
    closeWorkbenchDialog();
    state.resolve(value);
  });

  workbenchDialogEl.addEventListener("keydown", (ev) => {
    if (!workbenchDialogState) return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      const state = workbenchDialogState;
      closeWorkbenchDialog();
      if (state.kind === "confirm") state.resolve(false);
      else state.resolve(null);
      return;
    }
    if (ev.key !== "Enter" || workbenchDialogState.kind !== "prompt") return;
    ev.preventDefault();
    const state = workbenchDialogState;
    const inputEl = workbenchDialogEl.querySelector<HTMLInputElement>("#workbench-dialog-input");
    const value = inputEl?.value ?? "";
    closeWorkbenchDialog();
    state.resolve(value);
  });

  // ---- new chat / new project (UX-POLISH §7.6) ----
  async function handleFreeChat(): Promise<void> {
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再开对话");
      return;
    }
    if (freeChatActive && !projectState.projectId) {
      input.focus();
      return;
    }
    try {
      client.sendCommand("新会话");
      setStatus("普通对话…");
    } catch (err) {
      setStatus(`开对话失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  async function handleNewChat(): Promise<void> {
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再开新对话");
      return;
    }
    if (projectState.projectId) {
      const ok = await showWorkbenchConfirm(
        "将挂起当前项目会话并打开普通对话（可在此用 write_evolve 造工具；不是同项目新开线）。继续？",
      );
      if (!ok) return;
    }
    try {
      client.sendCommand("新会话");
      setStatus("普通对话…");
    } catch (err) {
      setStatus(`开对话失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  async function handleNewProject(): Promise<void> {
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再新建项目");
      return;
    }
    if (projectState.projectId) {
      const ok = await showWorkbenchConfirm("离开当前项目去建新项目？");
      if (!ok) return;
    }
    const raw = await showWorkbenchPrompt("新项目 id（字母数字与连字符）:");
    if (raw === null) return;
    const id = raw.trim();
    if (!id) {
      setStatus("未输入项目 id");
      return;
    }
    try {
      client.sendCommand(`项目 新建 ${id}`);
      setStatus(`新建项目 ${id}…`);
    } catch (err) {
      setStatus(`新建项目失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // ---- sessions dropdown ----
  function relativeTime(iso: string): string {
    if (!iso) return "";
    const then = new Date(iso).getTime();
    const diffMs = Date.now() - then;
    if (diffMs < 0) return "";
    const min = Math.floor(diffMs / 60000);
    if (min < 1) return "刚刚";
    if (min < 60) return `${min} 分钟前`;
    const hours = Math.floor(min / 60);
    if (hours < 24) return `${hours} 小时前`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days} 天前`;
    return new Date(iso).toLocaleDateString("zh-CN");
  }

  function handleOpenSessions(): void {
    try {
      client.listSessions();
    } catch (err) {
      setStatus(`获取会话列表失败：${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    sessionsOpen = !sessionsOpen;
    if (!sessionsOpen) {
      expandEl.classList.add("hidden");
      expandEl.innerHTML = "";
      return;
    }
    sessionsTab = perspective === "project" || projectState.projectId ? "project" : "chat";
    renderSessionsDropdown();
  }

  function renderSessionsDropdown(): void {
    const chatItems = sessionsDropdown.filter((s) => !s.project_id);
    const projectItems = sessionsDropdown.filter((s) => !!s.project_id);
    const items = sessionsTab === "project" ? projectItems : chatItems;
    const emptyHint = sessionsDropdown.length
      ? sessionsTab === "project"
        ? "暂无可恢复的项目会话 · 项目目录请从左侧打开"
        : "暂无普通对话"
      : "加载中…";

    let listHtml = "";
    if (!items.length) {
      listHtml = `<p class="text-muted unified-sessions-empty">${emptyHint}</p>`;
    } else {
      for (const s of items) {
        const isCurrent = s.session_id === (chat.model as any).sessionId;
        const time = relativeTime(s.updated_at);
        const preview =
          sessionsTab === "chat" && s.preview
            ? `<span class="unified-expand-item-preview">${escapeHtml(s.preview)}</span>`
            : "";
        const count = s.message_count ? `${s.message_count} 条消息` : "";
        const meta = [time, count].filter(Boolean).join(" · ");
        listHtml += `<div class="unified-expand-item ${isCurrent ? "is-current" : ""}">
          <span class="unified-expand-item-title">${escapeHtml(s.title)}</span>
          ${preview}
          <span class="unified-expand-item-meta">${escapeHtml(meta || s.session_id)}</span>
          <button type="button" class="unified-btn" data-open-session="${escapeHtml(s.session_id)}" ${isCurrent ? "disabled" : ""}>${isCurrent ? "当前" : "打开"}</button>
        </div>`;
      }
    }

    expandEl.innerHTML = `
      <div class="unified-sessions-header">
        <div class="unified-expand-title">会话 <button type="button" class="unified-btn" id="unified-sessions-close">关闭</button></div>
        <div class="unified-sessions-tabs" role="tablist">
          <button type="button" class="unified-sessions-tab ${sessionsTab === "chat" ? "is-active" : ""}" data-sessions-tab="chat" role="tab">对话 (${chatItems.length})</button>
          <button type="button" class="unified-sessions-tab ${sessionsTab === "project" ? "is-active" : ""}" data-sessions-tab="project" role="tab">项目会话 (${projectItems.length})</button>
        </div>
      </div>
      <div class="unified-sessions-list">${listHtml}</div>
    `;
    expandEl.classList.remove("hidden");

    expandEl.querySelector("#unified-sessions-close")?.addEventListener("click", () => {
      sessionsOpen = false;
      expandEl.classList.add("hidden");
      expandEl.innerHTML = "";
    });
    expandEl.querySelectorAll<HTMLButtonElement>("[data-sessions-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const tab = btn.dataset.sessionsTab;
        if (tab !== "chat" && tab !== "project") return;
        sessionsTab = tab;
        renderSessionsDropdown();
      });
    });
    expandEl.querySelectorAll<HTMLButtonElement>("[data-open-session]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const sid = btn.dataset.openSession;
        if (!sid) return;
        try {
          client.openSession(sid);
          sessionsOpen = false;
          expandEl.classList.add("hidden");
          expandEl.innerHTML = "";
          startHydrationWait(sid, "打开会话");
        } catch (err) {
          setStatus(`打开失败：${err instanceof Error ? err.message : String(err)}`);
        }
      });
    });
  }

  // ---- proposals panel helpers ----
  function syncProposalsState(): void {
    proposalsState.proposals = topbarState.proposals;
    // proposalsState and topbarState share proposals array;
    // sync is called before each renderProposalsPanel()
  }

  function openProposals(): void {
    proposalsState.expandOpen = true;
    syncProposalsState();
    renderProposalsPanel();
  }

  function renderProposalsPanel(): void {
    syncProposalsState();
    renderProposals(expandEl, proposalsState, client);

    // Listen for custom events dispatched by proposals.ts buttons
    expandEl.addEventListener("proposals:next", () => {
      proposalsState.proposalIndex = nextProposalIndex(proposalsState);
      renderProposalsPanel();
    }, { once: true });

    expandEl.addEventListener("proposals:close", () => {
      proposalsState.expandOpen = false;
      renderProposalsPanel();
    }, { once: true });
  }

  function handleProposalsEvent(items: ProposalItem[]): void {
    proposalsState.proposals = items;
    proposalsState.proposalIndex = 0;
    if (!items.length) proposalsState.expandOpen = false;
    // also sync topbarState for renderTopbar
    topbarState.proposals = items;
    syncProposalsState();
    refreshTopbar();
    renderProposalsPanel();
  }

  // ---- project panel helpers ----
  const projectCallbacks: ProjectPanelCallbacks = {
    onProjectSwitch: (projectId: string) => {
      const target = projectId.trim();
      if (!target || target === projectState.projectId) return;
      if (chat.isWorking()) {
        setStatus("助手执行中，请稍后再切换项目");
        return;
      }
      closePlanMainFocus();
      projectState.switchInProgress = true;
      projectState.pendingPickerId = target;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      try {
        client.switchProject(target);
      } catch (err) {
        projectState.switchInProgress = false;
        projectState.pendingPickerId = "";
        setStatus(`切换失败：${err instanceof Error ? err.message : String(err)}`);
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }
    },
    onProjectSwitchConfirm: () => {
      if (!projectState.switchOverlay) return;
      const target = projectState.switchOverlay.projectId;
      resetProjectScopedState(projectState);
      projectState.projectId = target;
      projectState.switchOverlay = null;
      projectState.pendingPickerId = target;
      projectState.switchInProgress = true;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      try {
        client.switchProject(target, {
          confirm: true,
          requestId: projectState.switchOverlay.requestId,
        });
      } catch (err) {
        projectState.switchInProgress = false;
        setStatus(`切换失败：${err instanceof Error ? err.message : String(err)}`);
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }
    },
    onProjectSwitchCancel: () => {
      projectState.switchOverlay = null;
      projectState.switchInProgress = false;
      projectState.pendingPickerId = "";
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
    },
    onPlanConfirm: async () => {
      const overlay = projectState.planOverlay;
      if (!overlay) {
        try {
          client.sendCommand("项目 确认");
          setStatus("正在确认计划…");
        } catch (err) {
          setStatus(`计划确认失败：${err instanceof Error ? err.message : String(err)}`);
        }
        return;
      }
      try {
        client.sendPlanResponse(overlay.requestId, "confirm");
        setStatus("正在确认计划…");
      } catch (err) {
        setStatus(`计划确认失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onPlanEdit: async () => {
      const overlay = projectState.planOverlay;
      if (!overlay) return;
      try {
        client.sendPlanResponse(overlay.requestId, "edit");
        projectState.planOverlay = null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        setStatus("继续修改计划…");
      } catch (err) {
        setStatus(`计划确认失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onRefreshProjects: () => {
      try {
        client.listProjects();
        setStatus("刷新项目列表…");
      } catch (err) {
        setStatus(`刷新失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onNewThread: () => {
      handleNewThread();
    },
    onOpenThread: (sessionId: string) => {
      const sid = sessionId.trim();
      if (!sid || sid === projectState.currentSessionId) return;
      if (chat.isWorking()) {
        setStatus("助手执行中，请稍后再切换会话线");
        return;
      }
      closePlanMainFocus();
      try {
        client.openSession(sid);
        startHydrationWait(sid, "打开会话线");
      } catch (err) {
        setStatus(`打开会话线失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onReturnActiveThread: () => {
      const active = projectState.activeSessionId.trim();
      if (!active) return;
      projectCallbacks.onOpenThread(active);
    },
    onOpenProjects: () => {
      projectState.overlayPanel = "projects";
      projectState.switchConfirmTarget = null;
      projectState.projectSearchQuery = "";
      try { client.listProjects(); } catch { /* ignore */ }
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
    },
    onNewProject: () => {
      handleNewProject();
    },
    onScopeConfirm: () => {
      try {
        setStatus("正在确认范围…");
        client.confirmProjectScope();
      } catch (err) {
        setStatus(`确认范围失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onDesignConfirm: () => {
      try {
        setStatus("正在确认设计…");
        client.confirmProjectDesign();
      } catch (err) {
        setStatus(`确认设计失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onStopTurn: () => {
      sendStopRequest();
    },
    onMilestoneAccept: () => {
      try {
        client.acceptProjectRelease();
        setStatus("正在保存发布确认…");
      } catch (err) {
        setStatus(`发布确认失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
  };

  threadReturnActiveBtn.addEventListener("click", () => {
    projectCallbacks.onReturnActiveThread();
  });

  function getActionableQueue() {
    return actionableSuggestions(projectState.suggestions);
  }

  function setMainFocus(focus: MainFocus): void {
    projectState.mainFocus = focus;
    if (focus !== "chat") {
      clearExpandedSurface();
    }
    if (focus === "chat") {
      projectState.reviewFocusId = null;
    }
    syncMainFocusView();
  }

  function syncMainFocusView(): void {
    const focus = projectState.mainFocus;
    const chatFocus = focus === "chat";
    const runtimeSurface = chatFocus || chat.isWorking() || chat.model.confirmPending;
    shellEl.dataset.mainFocus = focus;
    chatEl.classList.toggle("hidden", focus !== "chat");
    planReviewEl.classList.toggle("hidden", focus !== "plan_review");
    planFullEl.classList.toggle("hidden", focus !== "plan_full");
    documentEl.classList.toggle("hidden", focus !== "document");
    chatEl.hidden = focus !== "chat";
    planReviewEl.hidden = focus !== "plan_review";
    planFullEl.hidden = focus !== "plan_full";
    documentEl.hidden = focus !== "document";
    composerDock.hidden = !runtimeSurface;
    composer.hidden = !runtimeSurface;
    statusEl.hidden = !runtimeSurface;
    tokenBar.hidden = !chatFocus;
    syncComposerMetaVisibility();
    if (focus === "plan_review") {
      renderPlanReviewPane();
      planReviewEl.scrollTop = 0;
    } else if (focus === "plan_full") {
      renderPlanFullPane();
    } else if (focus === "document") {
      renderDocumentPane();
    }
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
  }


  function renderPlanReviewPane(): void {
    const queue = getActionableQueue();
    planReviewIndex = clampReviewIndex(planReviewIndex, queue.length);
    projectState.reviewFocusId = queue[planReviewIndex]?.id ?? null;
    planReviewEl.innerHTML = renderPlanReviewPanel({
      suggestions: projectState.suggestions,
      reviewIndex: planReviewIndex,
      adoptPendingId: projectState.adoptPendingId,
      dropTaskPendingId: projectState.dropTaskPendingId,
      dropTaskPendingWorking: projectState.dropTaskPendingWorking,
    });
  }

  function renderPlanFullPane(): void {
    const highlight =
      projectState.highlightChanges && projectState.highlightedLines.size > 0
        ? projectState.highlightedLines
        : null;
    planFullEl.innerHTML = `${renderPlanFullHeader()}<div class="unified-plan-full-body">${renderPlanTaskFlow(projectState, highlight)}</div>`;
  }

  function renderDocumentPane(): void {
    const path = projectState.currentDocPath;
    const content = projectState.currentDocContent;
    documentEl.innerHTML = `<div class="unified-document-inner">
      <header class="unified-document-header">
        <button type="button" class="unified-btn" data-action="document-back">← 返回聊天</button>
        <div class="unified-document-heading">
          <div class="unified-document-kicker">项目文档</div>
          <h1>${escapeHtml(path || "文档")}</h1>
        </div>
        <button type="button" class="unified-btn" data-action="document-list">文档列表</button>
      </header>
      <article class="unified-document-content unified-markdown">${content
        ? renderMarkdown(content)
        : `<p class="overlay-empty">加载中…</p>`}</article>
    </div>`;
    void hydrateMermaid(documentEl);
    documentEl.scrollTop = 0;
  }

  function openDocument(path: string): void {
    if (!path) return;
    projectState.currentDocPath = path;
    projectState.currentDocContent = "";
    projectState.overlayPanel = null;
    setMainFocus("document");
    try { client.readDoc(path); } catch { /* ignore */ }
  }

  function openPlanReview(suggestionId?: string): void {
    try {
      setStatus("正在读取待处理方案…");
      const queue = getActionableQueue();
      if (!queue.length) {
        setStatus("暂无待采纳提案");
        return;
      }
      let index = 0;
      if (suggestionId) {
        const found = queue.findIndex((s) => s.id === suggestionId);
        if (found >= 0) index = found;
      }
      planReviewIndex = index;
      projectState.reviewFocusId = queue[index]?.id ?? null;
      projectState.overlayPanel = null;
      setStatus("正在切换主区…");
      projectState.mainFocus = "plan_review";
      projectState.reviewFocusId = queue[index]?.id ?? null;
      shellEl.dataset.mainFocus = "plan_review";
      chatEl.classList.add("hidden");
      chatEl.hidden = true;
      planFullEl.classList.add("hidden");
      planFullEl.hidden = true;
      planReviewEl.classList.remove("hidden");
      planReviewEl.hidden = false;
      setStatus("正在打开方案变更…");
      renderPlanReviewPane();
      planReviewEl.scrollTop = 0;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      setStatus("方案变更已打开");
    } catch (err) {
      setStatus(`方案变更打开失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function handleReviewSuggestionClick(ev: Event): void {
    const target = ev.target;
    const btn = target instanceof Element
      ? target.closest<HTMLButtonElement>('[data-action="review-suggestion"]')
      : null;
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    ev.stopImmediatePropagation();
    openPlanReview(btn.dataset.suggestionId);
  }

  function openPlanFull(): void {
    projectState.overlayPanel = null;
    setMainFocus("plan_full");
  }

  function clearExpandedSurface(): void {
    sessionsOpen = false;
    expandEl.classList.add("hidden");
    expandEl.innerHTML = "";
  }

  function resumeRunawayFromUi(label = "正在恢复狂奔…", opts?: { force?: boolean }): void {
    try {
      if (chat.isWorking()) {
        setStatus("狂奔已在执行中，请查看活动区");
        return;
      }
      const now = Date.now();
      if (
        !opts?.force
        && projectState.runawayEnabled
        && !hasProjectBlocker(projectState)
        && now - lastRunawayNudgeAt < RUNAWAY_NUDGE_DEBOUNCE_MS
      ) {
        setStatus("狂奔已开启，系统会自动续接");
        jumpToCurrentActivity();
        return;
      }
      lastRunawayNudgeAt = now;
      clearRunawayResumePending();
      runawayResumePending = true;
      syncWorkingVisual();
      client.resumeProjectRunaway();
      setStatus(label);
      runawayResumeTimer = window.setTimeout(() => {
        if (runawayResumePending && !chat.isWorking()) {
          clearRunawayResumePending();
          syncWorkingVisual();
          setStatus("恢复超时：可能被上一回合占用，请点停止或重启桌面端");
        }
      }, 12000);
    } catch (err) {
      clearRunawayResumePending();
      syncWorkingVisual();
      setStatus(`恢复狂奔失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function showBlockerDetails(): void {
    setMainFocus("chat");
    clearExpandedSurface();
    expandEl.innerHTML = renderProjectBlockerDetails(projectState);
    expandEl.classList.remove("hidden");
    expandEl.querySelector<HTMLButtonElement>('[data-action="resume-runaway"]')?.addEventListener("click", () => {
      resumeRunawayFromUi();
    });
    expandEl.querySelector<HTMLButtonElement>('[data-action="directed-runaway-retry"]')?.addEventListener("click", () => {
      try {
        client.resumeProjectRunawayDirected();
        setStatus("正在定向再试…");
      } catch (err) {
        setStatus(`定向再试失败：${err instanceof Error ? err.message : String(err)}`);
      }
    });
    expandEl.querySelector<HTMLButtonElement>('[data-action="blocker-details-close"]')?.addEventListener("click", () => {
      clearExpandedSurface();
    });
  }

  function startProjectTaskFromUi(taskId: string): void {
    const normalizedTaskId = taskId.trim();
    if (!normalizedTaskId) return;
    try {
      setStatus(`正在启动 ${normalizedTaskId}…`);
      setMainFocus("chat");
      client.startProjectTask(normalizedTaskId);
    } catch (err) {
      setStatus(`启动任务失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function startDirectImplementFromUi(): void {
    // Ordinary M3: same user-visible command as CLI「项目 直接实现」.
    // On this base the verb is not registered yet (M1 PR #2), so it falls
    // through to chat and hits is_direct_implement_request →
    // maybe_auto_confirm_plan_for_direct_implement + implement overlay.
    // TODO(M1): when project_entry lands, this command sets
    // project_entry=direct and later turns skip plan_partner. If M1's CLI
    // only confirms the gate without starting a turn, follow with a short
    // implement message so the CTA still kicks work.
    try {
      setStatus("正在直接实现…");
      setMainFocus("chat");
      client.sendCommand("项目 直接实现");
    } catch (err) {
      setStatus(`直接实现失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function runProjectVerifyFromUi(): void {
    try {
      setStatus("正在跑验收…");
      setMainFocus("chat");
      client.sendCommand("项目 验收");
    } catch (err) {
      setStatus(`验收失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function handleHeaderNextStepAction(action: string, taskId?: string): void {
    switch (action) {
      case "confirm-plan":
        void projectCallbacks.onPlanConfirm();
        return;
      case "direct-implement":
        startDirectImplementFromUi();
        return;
      case "run-verify":
        runProjectVerifyFromUi();
        return;
      case "start-task":
        if (taskId?.trim()) {
          startProjectTaskFromUi(taskId);
          return;
        }
        openPlanFull();
        return;
      case "accept-milestone":
        projectCallbacks.onMilestoneAccept();
        return;
      case "confirm-scope":
        projectCallbacks.onScopeConfirm();
        return;
      case "confirm-design":
        projectCallbacks.onDesignConfirm();
        return;
      case "open-plan-review":
        openPlanReview();
        return;
      case "open-full-plan":
        openPlanFull();
        return;
      case "open-projects":
        projectCallbacks.onOpenProjects();
        return;
      case "jump-turn-process":
        jumpToCurrentTurnProcess();
        return;
      case "jump-review-summary":
        jumpToReviewSummary();
        return;
      case "stop-turn":
        projectCallbacks.onStopTurn();
        return;
      case "resume-runaway":
        resumeRunawayFromUi();
        return;
      default:
        return;
    }
  }

  function openDocumentList(): void {
    projectState.overlayPanel = "docs";
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
    try { client.listDocs(); } catch { /* ignore */ }
  }

  function closePlanMainFocus(): void {
    setMainFocus("chat");
  }

  function afterSuggestionQueueChanged(): void {
    const queue = getActionableQueue();
    if (!queue.length) {
      if (projectState.mainFocus === "plan_review") {
        closePlanMainFocus();
        setStatus("计划提案已处理完");
      }
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      return;
    }
    planReviewIndex = clampReviewIndex(planReviewIndex, queue.length);
    projectState.reviewFocusId = queue[planReviewIndex]?.id ?? null;
    if (projectState.mainFocus === "plan_review") {
      renderPlanReviewPane();
    }
    if (projectState.mainFocus === "plan_full") {
      renderPlanFullPane();
    }
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
  }

  function clearAdoptFlashTimer(): void {
    if (suggestionAdoptFlashTimerId !== null) {
      window.clearTimeout(suggestionAdoptFlashTimerId);
      suggestionAdoptFlashTimerId = null;
    }
  }

  function clearPendingAdoptTimeout(): void {
    if (pendingAdoptAcceptTimeoutId !== null) {
      window.clearTimeout(pendingAdoptAcceptTimeoutId);
      pendingAdoptAcceptTimeoutId = null;
    }
  }

  function clearPendingAdopt(): void {
    pendingAdoptAccept = null;
    projectState.adoptPendingId = null;
    clearPendingAdoptTimeout();
  }

  function resolvePendingAdopt(): void {
    if (!pendingAdoptAccept) return;
    const { sid, path, whatChanged } = pendingAdoptAccept;
    if (projectState.suggestions.some((s) => s.id === sid)) return;

    const notices = projectState.partnerNotices.join("\n");
    clearPendingAdopt();

    if (/已撤回|无效提案|提案已失效|刷新待采纳/i.test(notices)) {
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      if (projectState.mainFocus === "plan_review") {
        renderPlanReviewPane();
      }
      return;
    }

    const match = notices.match(/已采纳写入\s+(\S+)/);
    const adoptPath = match?.[1] || path;
    const moreAfter = projectState.suggestions.some((s) => Boolean(s.action));
    acceptedChangeForNextTurn = whatChanged;
    startAdoptFlash(`已采纳写入 ${adoptPath}`, !moreAfter);
  }

  function finishAdoptFlash(showFooter: boolean): void {
    const msg = projectState.suggestionAdoptFlash;
    projectState.suggestionAdoptFlash = null;
    clearAdoptFlashTimer();
    if (showFooter && msg) {
      projectState.adoptedFooterMessage = msg;
    }
    afterSuggestionQueueChanged();
  }

  function startAdoptFlash(message: string, showFooterAfter: boolean): void {
    clearAdoptFlashTimer();
    projectState.suggestionAdoptFlash = message;
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
    suggestionAdoptFlashTimerId = window.setTimeout(() => {
      finishAdoptFlash(showFooterAfter);
    }, ADOPT_FLASH_MS);
  }

  function acceptSuggestionById(
    sid: string,
    codePolicy?: "plan_only" | "agent_cleanup" | "git_guide",
  ): void {
    if (projectState.suggestionAdoptFlash || pendingAdoptAccept) return;
    const sug = projectState.suggestions.find((s) => s.id === sid);
    if (!sug?.action) return;

    if (sug.action === "drop_task" && !codePolicy) {
      projectState.dropTaskPendingId = sid;
      projectState.dropTaskPendingWorking = chat.isWorking();
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      if (projectState.mainFocus === "plan_review") renderPlanReviewPane();
      return;
    }
    if (sug.action === "drop_task") {
      projectState.dropTaskPendingId = null;
      projectState.dropTaskPendingWorking = false;
    }

    const path = adoptPathFromSuggestion(sug);
    pendingAdoptAccept = {
      sid,
      path,
      whatChanged: typeof sug.payload?.what_changed === "string" && sug.payload.what_changed.trim()
        ? sug.payload.what_changed.trim()
        : "见 diff",
    };
    projectState.adoptPendingId = sid;
    clearPendingAdoptTimeout();
    pendingAdoptAcceptTimeoutId = window.setTimeout(() => {
      if (pendingAdoptAccept?.sid !== sid) return;
      clearPendingAdopt();
      setStatus("采纳请求超时，请重试");
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      if (projectState.mainFocus === "plan_review") {
        renderPlanReviewPane();
      }
    }, ADOPT_PENDING_MS);

    try {
      client.acceptPlanSuggestion(sid, codePolicy);
    } catch {
      clearPendingAdopt();
      return;
    }
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
    if (projectState.mainFocus === "plan_review") {
      renderPlanReviewPane();
    }
  }

  function ignoreSuggestionById(sid: string): void {
    projectState.suggestions = projectState.suggestions.filter((s) => s.id !== sid);
    if (!projectState.suggestions.some((s) => Boolean(s.action))) {
      projectState.partnerNotices = [];
    }
    try {
      client.ignorePlanSuggestion(sid);
    } catch {
      /* ignore */
    }
    afterSuggestionQueueChanged();
  }

  function processMetaForTurnKey(turnKey: string): { index: number; total: number } | undefined {
    const segments = liveTurnCardProcessBlocks(chat.model.blocks, chat.currentTurnIndex());
    if (segments.length <= 1) return undefined;
    const idx = segments.findIndex((block) => block.turnKey === turnKey);
    if (idx < 0) return undefined;
    return { index: idx + 1, total: segments.length };
  }

  function pickProcessBlockForJump(): Extract<ChatBlock, { kind: "process" }> | undefined {
    const turnIndex = chat.currentTurnIndex();
    const segments = liveTurnCardProcessBlocks(chat.model.blocks, turnIndex);
    if (!segments.length) {
      return findActiveProcessBlock();
    }
    if (chat.isWorking()) {
      resetProcessJumpCursor();
      const live = segments.find((block) => isProcessThinkingLive(block, chat.model.currentTurnKey));
      if (live) return live;
      const expanded = segments.find((block) => !block.collapsed);
      if (expanded) return expanded;
      return segments[segments.length - 1];
    }
    if (processJumpTurnIndex !== turnIndex) {
      processJumpCursor = -1;
      processJumpTurnIndex = turnIndex;
    }
    processJumpCursor = (processJumpCursor + 1) % segments.length;
    return segments[segments.length - 1 - processJumpCursor];
  }

  function processJumpStatusLabel(block: Extract<ChatBlock, { kind: "process" }>): string {
    const turnIndex = chat.currentTurnIndex();
    const segments = liveTurnCardProcessBlocks(chat.model.blocks, turnIndex);
    if (segments.length <= 1) return "已定位到当前活动";
    const idx = segments.findIndex((item) => item.turnKey === block.turnKey);
    if (idx < 0) return "已定位到当前活动";
    return `已定位到过程段 ${idx + 1}/${segments.length}`;
  }

  function findActiveProcessBlock(): Extract<ChatBlock, { kind: "process" }> | undefined {
    const blocks = chat.model.blocks;
    const currentKey = chat.model.currentTurnKey;
    if (currentKey) {
      for (let i = blocks.length - 1; i >= 0; i--) {
        const block = blocks[i];
        if (block.kind !== "process" || block.turnKey !== currentKey) continue;
        if (!block.collapsed || isProcessThinkingLive(block, currentKey)) {
          return block;
        }
      }
    }
    for (let i = blocks.length - 1; i >= 0; i--) {
      const block = blocks[i];
      if (block.kind !== "process") continue;
      if (!block.collapsed && isProcessThinkingLive(block, chat.model.currentTurnKey)) {
        return block;
      }
    }
    for (let i = blocks.length - 1; i >= 0; i--) {
      const block = blocks[i];
      if (block.kind === "process" && !block.collapsed) return block;
    }
    for (let i = blocks.length - 1; i >= 0; i--) {
      const block = blocks[i];
      if (block.kind === "process") return block;
    }
    return undefined;
  }

  function scrollToActivityElement(el: HTMLElement): void {
    el.classList.add("is-jump-target");
    window.setTimeout(() => el.classList.remove("is-jump-target"), 1400);
    el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    el.querySelector<HTMLElement>(".unified-thinking.is-streaming, .unified-thinking.is-waiting")?.scrollIntoView({
      behavior: "smooth",
      block: "nearest",
    });
  }

  function jumpToLiveTurnCard(): boolean {
    const card =
      chatEl.querySelector<HTMLElement>(".unified-turn-card.is-live") ??
      chatEl.querySelector<HTMLElement>(".unified-turn-card:last-of-type");
    if (!card) return false;
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    card.classList.add("is-jump-target");
    window.setTimeout(() => card.classList.remove("is-jump-target"), 1400);
    return true;
  }

  function jumpToCurrentActivity(): void {
    if (projectState.mainFocus !== "chat") {
      setMainFocus("chat");
    }
    if (hasProjectBlocker(projectState)) {
      showBlockerDetails();
    }
    const block = pickProcessBlockForJump();
    if (!block) {
      requestAnimationFrame(() => {
        renderChat();
        if (jumpToLiveTurnCard()) {
          setStatus("已定位到当前回合");
          return;
        }
        scrollChatToBottom();
        setStatus(chat.isWorking() ? "当前回合尚无活动记录" : "当前没有可查看的活动");
      });
      return;
    }
    if (block.collapsed) {
      chat.toggleProcessCollapsed(block.turnKey);
    }
    ensureProcessEntries(block);
    const streamingThink = block.entries?.find(
      (e) => e.kind === "think" && e.phase === "streaming" && e.text.trim(),
    );
    if (!streamingThink) {
      chat.openThinking(block.turnKey);
    }
    const turnKey = block.turnKey;
    requestAnimationFrame(() => {
      renderChat();
      const card =
        chatEl.querySelector<HTMLElement>(`.unified-turn-card[data-turn-key="${turnKey}"]`) ??
        chatEl.querySelector<HTMLElement>(".unified-turn-card.is-live");
      const el =
        card?.querySelector<HTMLElement>(`.unified-activity[data-turn="${turnKey}"], .unified-process[data-turn="${turnKey}"]`) ??
        chatEl.querySelector<HTMLElement>(`.unified-process[data-turn="${turnKey}"]`);
      if (!el) {
        if (jumpToLiveTurnCard()) {
          setStatus("已定位到当前回合");
        } else {
          scrollChatToBottom();
        }
        return;
      }
      scrollToActivityElement(el);
      setStatus(processJumpStatusLabel(block));
    });
  }

  const jumpToCurrentTurnProcess = jumpToCurrentActivity;

  function jumpToReviewSummary(): void {
    setMainFocus("chat");
    if (hasProjectBlocker(projectState)) {
      showBlockerDetails();
    }
    const blocks = chat.model.blocks;
    for (let i = blocks.length - 1; i >= 0; i--) {
      const block = blocks[i];
      if (block.kind !== "review-subagent") continue;
      requestAnimationFrame(() => {
        const el = chatEl.querySelector<HTMLElement>(
          `.unified-plan-subagent[data-turn-index="${block.turnIndex}"]`,
        );
        el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
      return;
    }
    scrollChatToBottom();
  }

  function handlePlanReviewAction(action: string, target: HTMLElement): void {
    switch (action) {
      case "back":
        closePlanMainFocus();
        return;
      case "open-full":
        openPlanFull();
        return;
      case "accept": {
        const sid = target.dataset.suggestionId;
        if (sid) acceptSuggestionById(sid);
        return;
      }
      case "confirm-drop-while-working": {
        const sid = target.dataset.suggestionId;
        if (sid) {
          projectState.dropTaskPendingWorking = false;
          renderPlanReviewPane();
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
        return;
      }
      case "drop-policy": {
        const sid = target.dataset.suggestionId;
        const policy = target.dataset.policy as "plan_only" | "agent_cleanup" | "git_guide" | undefined;
        if (sid && policy) acceptSuggestionById(sid, policy);
        return;
      }
      case "ignore": {
        const sid = target.dataset.suggestionId;
        if (sid) ignoreSuggestionById(sid);
        return;
      }
      case "prev":
        if (planReviewIndex > 0) {
          planReviewIndex -= 1;
          renderPlanReviewPane();
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
        return;
      case "next": {
        const queue = getActionableQueue();
        if (planReviewIndex < queue.length - 1) {
          planReviewIndex += 1;
          renderPlanReviewPane();
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
        return;
      }
      default:
        return;
    }
  }

  planReviewEl.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-plan-review-action]");
    if (!btn?.dataset.planReviewAction) return;
    handlePlanReviewAction(btn.dataset.planReviewAction, btn);
  });

  planFullEl.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-plan-review-action]");
    if (!btn?.dataset.planReviewAction) return;
    handlePlanReviewAction(btn.dataset.planReviewAction, btn);
  });

  planFullEl.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>('[data-action="start-task"]');
    if (!btn?.dataset.taskId) return;
    startProjectTaskFromUi(btn.dataset.taskId);
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if (projectState.mainFocus === "chat") return;
    if (document.activeElement === input) return;
    ev.preventDefault();
    closePlanMainFocus();
  });

  function refreshServices(): void {
    if (perspective !== "project") return;
    projectState.servicesLoading = true;
    projectState.servicesError = "";
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
    try {
      client.listServices();
    } catch (err) {
      projectState.servicesLoading = false;
      projectState.servicesError = err instanceof Error ? err.message : String(err);
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
    }
  }

  function refreshTerminals(): void {
    if (projectState.terminalsLoading) return;
    const requestSerial = ++terminalRequestSerial;
    const requestId = `terminal-${Date.now()}-${requestSerial}`;
    terminalRequestId = requestId;
    projectState.terminalsLoading = true;
    projectState.terminalsError = "";
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
    try {
      client.listTerminals(requestId);
      if (terminalRequestTimeoutId !== null) window.clearTimeout(terminalRequestTimeoutId);
      terminalRequestTimeoutId = window.setTimeout(() => {
        if (requestSerial !== terminalRequestSerial || !projectState.terminalsLoading) return;
        projectState.terminalsLoading = false;
        projectState.terminalsError = "读取终端状态超时";
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }, 8000);
    } catch (err) {
      projectState.terminalsLoading = false;
      projectState.terminalsError = err instanceof Error ? err.message : String(err);
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
    }
  }

  function requestTerminalOutput(sessionId: string, cursor: number): void {
    if (!sessionId || projectState.terminalDetails?.session_id !== sessionId) return;
    const requestSerial = ++terminalOutputRequestSerial;
    const requestId = `terminal-output-${Date.now()}-${requestSerial}`;
    terminalOutputRequestId = requestId;
    terminalOutputRequestSessionId = sessionId;
    projectState.terminalOutputLoading = true;
    projectState.terminalOutputError = "";
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
    try {
      client.readTerminalOutput(sessionId, cursor, 16_384, requestId);
      if (terminalOutputRequestTimeoutId !== null) {
        window.clearTimeout(terminalOutputRequestTimeoutId);
      }
      terminalOutputRequestTimeoutId = window.setTimeout(() => {
        if (
          requestSerial !== terminalOutputRequestSerial
          || terminalOutputRequestSessionId !== sessionId
          || !projectState.terminalOutputLoading
        ) return;
        projectState.terminalOutputLoading = false;
        projectState.terminalOutputError = "读取终端输出超时";
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }, 8000);
    } catch (err) {
      projectState.terminalOutputLoading = false;
      projectState.terminalOutputError = err instanceof Error ? err.message : String(err);
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
    }
  }

  function openTerminalDetails(sessionId: string): void {
    const session = projectState.terminalSessions.find((item) => item.session_id === sessionId);
    if (!session) return;
    terminalOutputRequestSerial += 1;
    if (terminalOutputRequestTimeoutId !== null) {
      window.clearTimeout(terminalOutputRequestTimeoutId);
      terminalOutputRequestTimeoutId = null;
    }
    terminalOutputRequestId = null;
    terminalOutputRequestSessionId = sessionId;
    projectState.terminalDetails = session;
    projectState.terminalOutput = "";
    projectState.terminalOutputCursor = 0;
    projectState.terminalOutputLoading = false;
    projectState.terminalOutputError = "";
    projectState.terminalOutputCursorReset = false;
    projectState.terminalOutputTruncated = false;
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
    requestTerminalOutput(sessionId, 0);
  }

  function closeTerminalDetails(): void {
    terminalOutputRequestSerial += 1;
    if (terminalOutputRequestTimeoutId !== null) {
      window.clearTimeout(terminalOutputRequestTimeoutId);
      terminalOutputRequestTimeoutId = null;
    }
    terminalOutputRequestId = null;
    terminalOutputRequestSessionId = null;
    projectState.terminalDetails = null;
    projectState.terminalOutput = "";
    projectState.terminalOutputCursor = 0;
    projectState.terminalOutputLoading = false;
    projectState.terminalOutputError = "";
    projectState.terminalOutputCursorReset = false;
    projectState.terminalOutputTruncated = false;
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
  }

  function scheduleTerminalPoll(): void {
    if (terminalPollTimerId !== null) window.clearTimeout(terminalPollTimerId);
    const delay = projectState.terminalsCollapsed ? 10_000 : 2_000;
    terminalPollTimerId = window.setTimeout(() => {
      terminalPollTimerId = null;
      refreshTerminals();
      scheduleTerminalPoll();
    }, delay);
  }

  // ---- chat rendering ----
  function isRecentTurnBlock(turnIndex: number): boolean {
    return turnIndex >= chat.currentTurnIndex() - (FOCUS_TURNS - 1);
  }

  /** One-line tool rows moved to activity-timeline.ts (UX-028 M0). */

  function findThinkEntryForDom(
    block: Extract<ChatBlock, { kind: "process" }>,
    thinkId: string,
  ): ThinkEntry | undefined {
    ensureProcessEntries(block);
    return block.entries?.find(
      (e): e is ThinkEntry => e.kind === "think" && e.id === thinkId,
    );
  }

  function isLiveProcessBlock(block: Extract<ChatBlock, { kind: "process" }>): boolean {
    return block.turnKey === chat.model.currentTurnKey;
  }

  function tickRunningToolElapsed(): void {
    const now = Date.now();
    chatEl.querySelectorAll<HTMLElement>(".unified-tool-line.is-running").forEach((el) => {
      const started = Number(el.dataset.startedAt || "0");
      if (!started) return;
      const span = el.querySelector<HTMLElement>(".unified-tool-elapsed");
      if (span) span.textContent = `运行中… ${formatToolElapsed(now - started)}`;
    });
  }

  function tickStreamingThinking(): void {
    chatEl.querySelectorAll<HTMLElement>(".unified-thinking.is-streaming, .unified-thinking.is-waiting").forEach((el) => {
      const turnKey = el.dataset.turn;
      const thinkId = el.dataset.thinkId;
      if (!turnKey) return;
      const blocks = chat.model.blocks;
      let block: Extract<ChatBlock, { kind: "process" }> | undefined;
      for (let i = blocks.length - 1; i >= 0; i--) {
        const candidate = blocks[i];
        if (candidate.kind === "process" && candidate.turnKey === turnKey) {
          block = candidate;
          break;
        }
      }
      if (!block) return;
      const waiting =
        isLiveProcessBlock(block) &&
        Boolean(block.llmPending) &&
        thinkId === "pending";
      if (waiting) {
        const waitStartedAt = block.llmWaitStartedAt ?? Date.now();
        const pendingThink: ThinkEntry = {
          kind: "think",
          id: "pending",
          text: "",
          phase: "streaming",
          startedAt: waitStartedAt,
        };
        syncThinkDom(el, pendingThink, { waiting: true, waitStartedAt });
        return;
      }
      if (!thinkId) return;
      const entry = findThinkEntryForDom(block, thinkId);
      if (!entry) return;
      syncThinkDom(el, entry, { waiting: false, liveTurn: isLiveProcessBlock(block) });
    });
  }

  function syncLiveThinkingDom(): void {
    for (const block of chat.model.blocks) {
      if (block.kind !== "process") continue;
      if (!isLiveProcessBlock(block)) continue;
      ensureProcessEntries(block);
      const pendingOnly =
        Boolean(block.llmPending) &&
        !block.entries?.some((e) => e.kind === "think" && e.phase === "streaming");
      if (pendingOnly) {
        const el = chatEl.querySelector<HTMLElement>(
          `.unified-thinking[data-turn="${block.turnKey}"][data-think-id="pending"]`,
        );
        if (el) {
          const waitStartedAt = block.llmWaitStartedAt ?? Date.now();
          const pendingThink: ThinkEntry = {
            kind: "think",
            id: "pending",
            text: "",
            phase: "streaming",
            startedAt: waitStartedAt,
          };
          syncThinkDom(el, pendingThink, { waiting: true, waitStartedAt });
        }
      }
      for (const entry of block.entries ?? []) {
        if (entry.kind !== "think") continue;
        if (entry.phase !== "streaming") continue;
        const el = chatEl.querySelector<HTMLElement>(
          `.unified-thinking[data-turn="${block.turnKey}"][data-think-id="${entry.id}"]`,
        );
        if (el) syncThinkDom(el, entry, { liveTurn: true });
      }
    }
  }

  function scrollStreamingThinkingBodies(): void {
    chatEl.querySelectorAll<HTMLElement>(
      ".unified-thinking.is-streaming .unified-thinking-body, .unified-thinking-stream-text",
    ).forEach((body) => {
      body.scrollTop = body.scrollHeight;
    });
  }

  let toolElapsedTimer: number | null = null;
  function syncToolElapsedTimer(): void {
    const hasRunning = chat.model.blocks.some(
      (b) => b.kind === "process" && getProcessTools(b).some((t) => t.status === "running"),
    );
    const hasStreamingThinking = chat.model.blocks.some(
      (b) => b.kind === "process" && isProcessThinkingLive(b, chat.model.currentTurnKey),
    );
    if ((hasRunning || hasStreamingThinking) && toolElapsedTimer === null) {
      const intervalMs = hasStreamingThinking ? 100 : 1000;
      toolElapsedTimer = window.setInterval(() => {
        tickRunningToolElapsed();
        tickStreamingThinking();
        syncLiveThinkingDom();
      }, intervalMs);
    } else if (!hasRunning && !hasStreamingThinking && toolElapsedTimer !== null) {
      window.clearInterval(toolElapsedTimer);
      toolElapsedTimer = null;
    }
  }

  function shouldLazyMarkdown(block: ChatBlock): boolean {
    if (block.kind === "assistant-streaming") return false;
    if (block.kind !== "assistant") return false;
    if (markdownRenderEager) return false;
    return block.turnIndex < chat.currentTurnIndex() - 1;
  }

  let activeProcessMeta: Map<string, { index: number; total: number }> | null = null;

  function renderBlocksFlat(blocks: ChatBlock[]): string {
    const parts: string[] = [];
    let chipBuf: string[] = [];
    const flushChips = () => {
      if (!chipBuf.length) return;
      parts.push(`<div class="unified-file-chips">${chipBuf.join("")}</div>`);
      chipBuf = [];
    };
    for (const block of blocks) {
      if (block.kind === "notice") {
        const path = adoptPathFromNotice(block.text);
        if (path) {
          chipBuf.push(renderAdoptChip(path));
          continue;
        }
      }
      flushChips();
      parts.push(renderBlock(block));
    }
    flushChips();
    return parts.join("");
  }

  function renderChatSegment(segment: ChatRenderSegment): string {
    if (segment.kind === "orphan") {
      return renderBlocksFlat(segment.blocks);
    }
    const live = segment.group.turnIndex === chat.currentTurnIndex() && chat.isWorking();
    const processBlocks = segment.group.blocks.filter(
      (block): block is Extract<ChatBlock, { kind: "process" }> => block.kind === "process",
    );
    const prevMeta = activeProcessMeta;
    activeProcessMeta = processBlocks.length > 1
      ? new Map(processBlocks.map((block, index) => [block.turnKey, { index: index + 1, total: processBlocks.length }]))
      : null;
    const inner = renderBlocksFlat(segment.group.blocks);
    activeProcessMeta = prevMeta;
    return renderTurnCardShell(segment.group, inner, { live });
  }

  function renderBlocksGrouped(blocks: ChatBlock[]): string {
    if (!useTurnCardLayout()) {
      return renderBlocksFlat(blocks);
    }
    return segmentChatBlocks(blocks).map(renderChatSegment).join("");
  }

  function renderBlock(block: ChatBlock): string {
    if (block.kind === "user") {
      let cls = "unified-turn unified-turn-user";
      if (perspective === "night") {
        if (!isRecentTurnBlock(block.turnIndex)) cls += " unified-turn-dim";
        if (recallHighlightTurns.has(block.turnIndex)) cls += " unified-turn-recall";
      }
      return `<article class="${cls}" data-turn-index="${block.turnIndex}">
        <div class="unified-turn-label">你</div>
        <div class="unified-turn-body">${formatUserMessageHtml(block.text)}</div>
      </article>`;
    }
    if (block.kind === "plan-subagent") {
      const title =
        block.status === "running"
          ? "计划搭档 · 调研中…"
          : block.proposalCount && block.proposalCount > 0
            ? `计划搭档 · ${block.proposalCount} 条待审阅`
            : "计划搭档 · 已整理";
      const detail =
        block.status === "running"
          ? escapeHtml(block.taskPreview || "正在整理计划域…")
          : escapeHtml(block.summary || block.taskPreview || "");
      const ready = block.status === "proposals_ready" && (block.proposalCount ?? 0) > 0;
      const hint = ready
        ? `<div class="unified-plan-subagent-hint">打开审阅</div>`
        : "";
      const interactive = ready ? " is-clickable" : "";
      const role = ready ? ' role="button" tabindex="0"' : "";
      return `<article class="unified-plan-subagent${interactive}" data-turn-index="${block.turnIndex}" data-status="${block.status}"${role}>
        <div class="unified-plan-subagent-title">${escapeHtml(title)}</div>
        <div class="unified-plan-subagent-body">${detail}</div>
        ${hint}
      </article>`;
    }
    if (block.kind === "review-subagent") {
      const title =
        block.status === "running"
          ? "交付审查"
          : block.verdict === "pass" ? "交付审查 · 已通过" : "交付审查";
      const detail =
        block.status === "running"
          ? block.taskPreview || "正在审查交付物…"
          : block.summary || block.taskPreview || "";
      return `<div data-turn-index="${block.turnIndex}">${renderReviewCallout(title, detail, block.status === "running")}</div>`;
    }
    if (block.kind === "assistant" || block.kind === "assistant-streaming") {
      let cls = "unified-turn unified-turn-assistant";
      if (perspective === "night") {
        if (!isRecentTurnBlock(block.turnIndex)) cls += " unified-turn-dim";
        if (recallHighlightTurns.has(block.turnIndex)) cls += " unified-turn-recall";
        if (block.kind === "assistant-streaming") cls += " unified-turn-streaming";
      }
      const lazy = shouldLazyMarkdown(block);
      const bodyCls = `unified-turn-body unified-markdown${lazy ? " unified-markdown-lazy" : ""}`;
      const bodyAttr = lazy ? ` data-lazy-md="${block.turnIndex}"` : "";
      const bodyHtml = lazy ? escapeHtml(block.text) : renderMarkdown(block.text);
      return `<article class="${cls}" data-turn-index="${block.turnIndex}">
        <div class="unified-turn-label">助手</div>
        <div class="${bodyCls}"${bodyAttr}>${bodyHtml}</div>
      </article>`;
    }
    if (block.kind === "notice") {
      const adoptPath = adoptPathFromNotice(block.text);
      if (adoptPath) {
        return `<div class="unified-file-chips">${renderAdoptChip(adoptPath)}</div>`;
      }
      if (/^已切换模型至\s+/i.test(block.text)) {
        return `<p class="unified-sys-line">${escapeHtml(block.text)}</p>`;
      }
      return `<p class="unified-sys-line text-muted">${escapeHtml(block.text)}</p>`;
    }
    // night perspective: skip process and confirm blocks (use overlay instead)
    if (block.kind === "process") {
      if (perspective === "night") return "";
      ensureProcessEntries(block);
      const liveTurn = isLiveProcessBlock(block);
      const timelineHtml = renderActivityTimeline(block, {
        liveTurn,
        toolsFoldOpen: toolsListExpanded.has(block.turnKey),
      });
      const tools = getProcessTools(block);
      const meta = activeProcessMeta?.get(block.turnKey);
      const pill = processPillLabel(block, {
        expanded: !block.collapsed,
        segmentIndex: meta?.index,
        segmentTotal: meta?.total,
      });
      const chevron = block.collapsed ? "▸" : "▾";
      const running = tools.some((t) => t.status === "running");
      const failAlert = renderToolFailAlert(lastFailedTool(tools));
      const thinkingLive = isProcessThinkingLive(block, chat.model.currentTurnKey);
      const processCls = [
        "unified-process",
        "unified-activity",
        block.collapsed ? "collapsed" : "",
        timelineHasThinking(block, liveTurn) ? "has-thinking" : "",
        thinkingLive ? "is-thinking-live" : "",
      ].filter(Boolean).join(" ");
      const showPillDot = running || thinkingLive;
      return `
        <div class="${processCls}" data-turn="${block.turnKey}">
          <button type="button" class="unified-process-pill" data-process-toggle="${block.turnKey}" aria-expanded="${block.collapsed ? "false" : "true"}">
            ${showPillDot ? '<span class="unified-process-pill-dot" aria-hidden="true"></span>' : ""}
            <span class="unified-process-pill-text">${escapeHtml(pill)} ${chevron}</span>
          </button>
          <div class="unified-process-body">
            ${timelineHtml}
          </div>
          ${failAlert}
        </div>`;
    }
    if (block.kind === "confirm") {
      if (perspective === "night") return ""; // shown in overlay
      const disabled = block.resolved ? "disabled" : "";
      const inProgress = isConfirmInProgressLabel(block.resolved);
      const isTerminal = Boolean(block.resolved) && !inProgress;
      // Resolved confirms are already reflected in the process tool lines (DESKTOP §3.2.2).
      if (isTerminal) return "";
      const actions = `
          <button type="button" class="unified-btn unified-btn-accent" data-confirm="y" data-id="${block.requestId}" ${disabled}>允许此次</button>
          <button type="button" class="unified-btn unified-btn-ghost" data-confirm="n" data-id="${block.requestId}" ${disabled}>拒绝</button>
          ${
            block.allowApproveAll
              ? `<button type="button" class="unified-btn unified-btn-ghost" data-confirm="a" data-id="${block.requestId}" ${disabled}>本会话均允许</button>`
              : ""
          }`;
      if (block.resolved) {
        return renderConfirmCardHtml(block.preview, "", { resolvedStatus: block.resolved });
      }
      return renderConfirmCardHtml(block.preview, actions);
    }
    return "";
  }

  function setupFocusObserver(): void {
    focusObserver?.disconnect();
    if (perspective !== "night") return;
    focusObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const el = entry.target as HTMLElement;
          if (entry.isIntersecting) {
            el.classList.add("in-view");
          }
        }
      },
      { root: chatEl, threshold: 0.15 },
    );
    chatEl.querySelectorAll<HTMLElement>("[data-turn-index]").forEach((el) => {
      focusObserver?.observe(el);
    });
  }

  function scrollToRecallTurns(): void {
    const first = chatEl.querySelector<HTMLElement>(".unified-turn-recall");
    first?.scrollIntoView({ behavior: motionQuery.matches ? "auto" : "smooth", block: "center" });
  }

  function bounceLatestUserTurn(): void {
    if (motionQuery.matches) return;
    const turns = chatEl.querySelectorAll<HTMLElement>(".unified-turn-user");
    const last = turns[turns.length - 1];
    if (!last) return;
    last.classList.add("unified-turn-enter");
    last.addEventListener("animationend", () => {
      last.classList.remove("unified-turn-enter");
    }, { once: true });
  }

  function blockPrint(block: ChatBlock): string {
    switch (block.kind) {
      case "user":
        return `U${block.turnIndex}:${block.text.length}`;
      case "plan-subagent":
        return `PS${block.turnIndex}:${block.status}:${block.proposalCount ?? 0}`;
      case "review-subagent":
        return `RS${block.turnIndex}:${block.status}:${block.verdict ?? ""}`;
      case "assistant":
        return `A${block.turnIndex}:${block.text.length}`;
      case "assistant-streaming":
        return `AS${block.turnIndex}:${block.turnKey}:${block.text.length}`;
      case "notice":
        return `N:${block.text.length}`;
      case "process": {
        ensureProcessEntries(block);
        const entriesSig = activityEntriesPrint(
          block.entries ?? [],
          Boolean(block.llmPending),
          block.llmWaitStartedAt,
        );
        return `P${block.turnKey}:${block.collapsed ? 1 : 0}:${entriesSig}`;
      }
      case "confirm":
        return `C${block.requestId}:${block.resolved ?? "_"}`;
    }
  }

  function bindNewProcessToggles(container: HTMLElement): void {
    container.querySelectorAll<HTMLButtonElement>("[data-process-toggle]:not([data-process-bound])").forEach((btn) => {
      btn.dataset.processBound = "1";
      btn.addEventListener("click", () => {
        const turnKey = btn.dataset.processToggle;
        if (turnKey) chat.toggleProcessCollapsed(turnKey);
      });
    });
    container.querySelectorAll<HTMLButtonElement>("[data-thinking-toggle]:not([data-thinking-bound])").forEach((btn) => {
      btn.dataset.thinkingBound = "1";
      btn.addEventListener("click", () => {
        const turnKey = btn.dataset.thinkingToggle;
        const thinkId = btn.dataset.thinkId;
        if (turnKey) chat.toggleThinkingOpen(turnKey, thinkId);
      });
    });
    container.querySelectorAll<HTMLDetailsElement>("[data-tools-fold]:not([data-tools-fold-bound])").forEach((el) => {
      el.dataset.toolsFoldBound = "1";
      el.addEventListener("toggle", () => {
        const turnKey = el.dataset.toolsFold;
        if (!turnKey) return;
        if (el.open) toolsListExpanded.add(turnKey);
        else toolsListExpanded.delete(turnKey);
      });
    });
    container.querySelectorAll<HTMLButtonElement>("[data-tool-rename]:not([data-tool-rename-bound])").forEach((btn) => {
      btn.dataset.toolRenameBound = "1";
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const toolId = btn.dataset.toolRename;
        if (toolId) openToolDisplayEditor(toolId);
      });
    });
  }

  /** UX-025 / UX-POLISH §5.2 A2: user scrolled up → stop yanking chat viewport. */
  let userScrollPinned = false;
  const SCROLL_PIN_THRESHOLD_PX = 80;
  const SCROLL_RELEASE_THRESHOLD_PX = 24;

  function resetUserScrollPin(): void {
    userScrollPinned = false;
  }

  chatEl.addEventListener(
    "scroll",
    () => {
      const distFromBottom = chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight;
      if (distFromBottom > SCROLL_PIN_THRESHOLD_PX) {
        userScrollPinned = true;
      } else if (distFromBottom <= SCROLL_RELEASE_THRESHOLD_PX) {
        userScrollPinned = false;
      }
    },
    { passive: true },
  );

  function scrollPendingConfirmIntoView(): void {
    const confirmEl = chatEl.querySelector<HTMLElement>(".unified-confirm:not(.resolved)");
    if (confirmEl) {
      confirmEl.scrollIntoView({
        behavior: motionQuery.matches ? "auto" : "smooth",
        block: "nearest",
      });
      return;
    }
    scrollUnlessPinned();
  }

  function scrollUnlessPinned(): void {
    if (userScrollPinned) return;
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  /** @deprecated alias */
  function scrollToBottomIfNear(): void {
    scrollUnlessPinned();
  }

  /** UX-025: after session.history — always land on latest messages. */
  function scrollChatToBottom(): void {
    resetUserScrollPin();
    requestAnimationFrame(() => {
      chatEl.scrollTop = chatEl.scrollHeight;
    });
  }

  function doRender(): void {
    markdownRenderEager = true;
    const curPrints = chat.model.blocks.map(blockPrint);

    if (useTurnCardLayout()) {
      const segments = segmentChatBlocks(chat.model.blocks);
      const curSegPrints = segments.map((seg) => segmentPrint(seg, blockPrint));

      if (
        renderedSegmentPrints.length > 0 &&
        curSegPrints.length === renderedSegmentPrints.length &&
        chatEl.children.length === curSegPrints.length
      ) {
        const last = curSegPrints.length - 1;
        let prefixOk = true;
        for (let i = 0; i < last; i++) {
          if (curSegPrints[i] !== renderedSegmentPrints[i]) {
            prefixOk = false;
            break;
          }
        }
        if (prefixOk && curSegPrints[last] !== renderedSegmentPrints[last]) {
          chatEl.children[last].outerHTML = renderChatSegment(segments[last]);
          bindNewProcessToggles(chatEl);
          setupFocusObserver();
          scrollToBottomIfNear();
          renderedSegmentPrints = curSegPrints;
          renderedPrints = curPrints;
          return;
        }
      }

      if (
        renderedSegmentPrints.length > 0 &&
        curSegPrints.length > renderedSegmentPrints.length
      ) {
        const prefixOk = renderedSegmentPrints.every((p, i) => p === curSegPrints[i]);
        if (prefixOk && chatEl.children.length === renderedSegmentPrints.length) {
          const newSegs = segments.slice(renderedSegmentPrints.length);
          chatEl.insertAdjacentHTML("beforeend", newSegs.map(renderChatSegment).join(""));
          bindNewProcessToggles(chatEl);
          setupFocusObserver();
          scrollToBottomIfNear();
          renderedSegmentPrints = curSegPrints;
          renderedPrints = curPrints;
          return;
        }
      }

      resetLazyMarkdownObserver();
      markdownRenderEager = false;
      chatEl.innerHTML = segments.map(renderChatSegment).join("");
      markdownRenderEager = true;
      bindNewProcessToggles(chatEl);
      setupFocusObserver();
      scrollToBottomIfNear();
      renderedSegmentPrints = curSegPrints;
      renderedPrints = curPrints;
      return;
    }

    // A3-2: pure tail-append — insertAdjacentHTML instead of full innerHTML
    if (renderedPrints.length > 0 && curPrints.length > renderedPrints.length) {
      const isTailAppend = renderedPrints.every(
        (fp, i) => fp === curPrints[i],
      );
      if (isTailAppend) {
        const newBlocks = chat.model.blocks.slice(renderedPrints.length);
        const newHtml = renderBlocksGrouped(newBlocks);
        chatEl.insertAdjacentHTML("beforeend", newHtml);
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
        renderedPrints = curPrints;
        return;
      }
    }

    // A3-3: same block count, only tail changed — replace just those elements
    if (renderedPrints.length > 0 && curPrints.length === renderedPrints.length) {
      let firstDiff = -1;
      for (let i = 0; i < curPrints.length; i++) {
        if (curPrints[i] !== renderedPrints[i]) { firstDiff = i; break; }
      }
      // Only use incremental path when changes are at the tail (last 2 blocks)
      // and DOM child count matches (falls back if night-perspective empty blocks)
      if (firstDiff >= 0 && firstDiff >= curPrints.length - 2 && chatEl.children.length === curPrints.length) {
        const children = chatEl.children;
        for (let i = firstDiff; i < curPrints.length; i++) {
          children[i].outerHTML = renderBlock(chat.model.blocks[i]);
        }
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
        renderedPrints = curPrints;
        return;
      }
    }

    // A3-4: tail-shrink — blocks removed from end (assistant.done with empty text)
    if (renderedPrints.length > 0 && curPrints.length < renderedPrints.length && chatEl.children.length === renderedPrints.length) {
      let firstDiff = -1;
      const prefixLen = Math.min(curPrints.length, renderedPrints.length);
      for (let i = 0; i < prefixLen; i++) {
        if (curPrints[i] !== renderedPrints[i]) { firstDiff = i; break; }
      }
      // All unchanged blocks match, and changes are near the tail
      const tailLen = renderedPrints.length - curPrints.length;
      if (firstDiff === -1 || firstDiff >= curPrints.length - 2) {
        // Remove extra trailing DOM children
        for (let r = 0; r < tailLen; r++) {
          chatEl.lastElementChild?.remove();
        }
        // Replace remaining changed tail elements
        if (firstDiff >= 0) {
          const children = chatEl.children;
          for (let i = firstDiff; i < curPrints.length; i++) {
            if (i < children.length) {
              children[i].outerHTML = renderBlock(chat.model.blocks[i]);
            }
          }
        }
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
        renderedPrints = curPrints;
        return;
      }
    }

    // A3-5: confirm resolved only — replace just the confirm elements at changed positions
    if (renderedPrints.length > 0 && curPrints.length === renderedPrints.length && chatEl.children.length === curPrints.length) {
      const diffs: number[] = [];
      for (let i = 0; i < curPrints.length; i++) {
        if (curPrints[i] !== renderedPrints[i]) diffs.push(i);
      }
      const allAreConfirm = diffs.length > 0 && diffs.every(i =>
        curPrints[i].startsWith("C") && renderedPrints[i].startsWith("C")
      );
      if (allAreConfirm) {
        const children = chatEl.children;
        for (const i of diffs) {
          if (i < children.length) {
            children[i].outerHTML = renderBlock(chat.model.blocks[i]);
          }
        }
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
        renderedPrints = curPrints;
        return;
      }
    }

    // A3-6: process collapsed toggle only — flip CSS class, skip full render
    if (renderedPrints.length > 0 && curPrints.length === renderedPrints.length && chatEl.children.length === curPrints.length) {
      const diffs: number[] = [];
      let collapseToggle = "";
      for (let i = 0; i < curPrints.length; i++) {
        if (curPrints[i] !== renderedPrints[i]) {
          diffs.push(i);
          // Detect process collapse toggle: P{key}:...:0 ↔ P{key}:...:1
          const curP = curPrints[i];
          const prevP = renderedPrints[i];
          if (curP.startsWith("P") && prevP.startsWith("P") &&
              curP.length === prevP.length &&
              curP.slice(0, -1) === prevP.slice(0, -1)) {
            collapseToggle = curP.split(":")[0].slice(1); // extract turnKey
          }
        }
      }
      if (collapseToggle && diffs.length === 1) {
        const el = chatEl.querySelector(`.unified-process[data-turn="${collapseToggle}"]`);
        if (el) {
          el.classList.toggle("collapsed");
          const btn = el.querySelector<HTMLButtonElement>("[data-process-toggle]");
          const block = chat.model.blocks.find(
            (b) => b.kind === "process" && b.turnKey === collapseToggle,
          );
          if (btn && block?.kind === "process") {
            const collapsed = el.classList.contains("collapsed");
            block.collapsed = collapsed;
            const chevron = collapsed ? "▸" : "▾";
            const textEl = btn.querySelector(".unified-process-pill-text");
            if (textEl) {
              const meta = processMetaForTurnKey(collapseToggle);
              textEl.textContent = `${processPillLabel(block, {
                expanded: !block.collapsed,
                segmentIndex: meta?.index,
                segmentTotal: meta?.total,
              })} ${chevron}`;
            }
            btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
          }
          renderedPrints = curPrints;
          return;
        }
      }
    }

    // full render (initial, or escape hatch for unhandled change patterns)
    resetLazyMarkdownObserver();
    markdownRenderEager = false;
    chatEl.innerHTML = renderBlocksGrouped(chat.model.blocks);
    markdownRenderEager = true;
    bindNewProcessToggles(chatEl);
    setupFocusObserver();
    scrollToBottomIfNear();
    renderedPrints = curPrints;
  }

  const RENDER_THROTTLE_MS = 100;

  function renderChat(): void {
    // Immediate paint; coalesce bursty follow-ups (streaming deltas) into one trailing pass.
    doRender();
    syncLiveThinkingDom();
    void hydrateMermaid(chatEl);
    observeLazyMarkdown(chatEl);
    scrollStreamingThinkingBodies();
    syncToolElapsedTimer();
    if (renderThrottleTimer !== null) return;
    renderThrottleTimer = window.setTimeout(() => {
      renderThrottleTimer = null;
      doRender();
      syncLiveThinkingDom();
      void hydrateMermaid(chatEl);
      observeLazyMarkdown(chatEl);
      scrollStreamingThinkingBodies();
      syncToolElapsedTimer();
    }, RENDER_THROTTLE_MS);
  }

  chatEl.addEventListener("click", (ev) => {
    const card = (ev.target as HTMLElement).closest<HTMLElement>(".unified-plan-subagent.is-clickable");
    if (!card || card.dataset.status !== "proposals_ready") return;
    openPlanReview();
  });

  function renderConfirmGlass(): void {
    if (perspective !== "night") {
      confirmGlass.classList.add("hidden");
      confirmGlass.innerHTML = "";
      return;
    }

    const confirm = chat.model.confirmOverlay;
    if (!confirm) {
      confirmGlass.classList.add("hidden");
      confirmGlass.innerHTML = "";
      return;
    }

    confirmGlass.classList.remove("hidden");
    const disabled = confirm.resolved ? "disabled" : "";
    const resolved = confirm.resolved
      ? `<div class="unified-confirm-resolved">${escapeHtml(confirm.resolved)}</div>`
      : `
        <div class="unified-confirm-glass-actions">
          <button type="button" class="unified-btn unified-btn-accent" data-confirm="y" data-id="${confirm.requestId}" ${disabled}>同意</button>
          <button type="button" class="unified-btn unified-btn-danger" data-confirm="n" data-id="${confirm.requestId}" ${disabled}>拒绝</button>
          ${
            confirm.allowApproveAll
              ? `<button type="button" class="unified-btn" data-confirm="a" data-id="${confirm.requestId}" ${disabled}>本会话均允许</button>`
              : ""
          }
        </div>`;

    const view = summarizeConfirmPreview(confirm.preview);
    const reasonBlock = view.reason
      ? `<p class="unified-confirm-reason">${escapeHtml(view.reason)}</p>`
      : "";
    confirmGlass.innerHTML = `
      <div class="unified-confirm-glass-card ${confirm.resolved ? "resolved" : ""}">
        <div class="unified-confirm-kicker">需要确认</div>
        <div class="unified-confirm-glass-title">${escapeHtml(view.headline)}</div>
        ${reasonBlock}
        ${view.compact
          ? `<details class="unified-confirm-details"><summary class="unified-confirm-details-toggle">查看详情</summary><pre class="unified-confirm-glass-preview">${escapeHtml(view.detail)}</pre></details>`
          : `<pre class="unified-confirm-glass-preview">${escapeHtml(view.detail)}</pre>`}
        ${resolved}
      </div>
    `;
  }

  // ---- input events ----
  function sendComposerMessage(ev?: KeyboardEvent): void {
    const text = input.value.trim();
    const attachmentIds = fileDrop.getAttachments().map((item) => item.id);
    composerWire.sendCurrentMessage();
    if (perspective === "night") bounceLatestUserTurn();
  }

  function composeDisplayMessageForSend(
    text: string,
    attachments: Array<{ name: string; ref: string; size: number; mime: string; readable_text: boolean }>,
  ): string {
    if (!attachments.length) return text;
    const lines = ["[附件]"];
    for (const item of attachments) {
      const size =
        item.size < 1024
          ? `${item.size} B`
          : item.size < 1024 * 1024
            ? `${(item.size / 1024).toFixed(1)} KB`
            : `${(item.size / (1024 * 1024)).toFixed(1)} MB`;
      lines.push(`- ${item.name} (${size})`);
    }
    if (text) lines.push("", text);
    return lines.join("\n");
  }

  sendBtn.addEventListener("click", (ev) => {
    sendComposerMessage(ev);
  });
  stopBtn.addEventListener("click", () => {
    sendStopRequest();
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.shiftKey) {
      ev.preventDefault();
      sendComposerMessage(ev);
    }
  });

  // auto-expand textarea on input
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  });

  // Escape key to stop anywhere in the shell
  document.addEventListener("keydown", (ev) => {
    if (destroyed) return;
    if (ev.key !== "Escape") return;
    if (!(chat.isWorking() || chat.model.confirmPending || projectState.runawayCancelAvailable)) return;
    sendStopRequest();
  });

  // ---- confirm keyboard shortcuts ----
  document.addEventListener("keydown", (ev) => {
    if (destroyed) return;
    if (!chat.model.confirmPending) return;
    // don't steal keys from the input field
    if (document.activeElement === input) return;
    const key = ev.key.toLowerCase();
    if (!["y", "n", "a"].includes(key)) return;

    const overlay = chat.model.confirmOverlay;
    let requestId: string | null = null;
    let allowApproveAll = false;
    if (overlay) {
      requestId = overlay.requestId;
      allowApproveAll = overlay.allowApproveAll;
    } else {
      // find most recent unresolved inline confirm block
      const blocks = chat.model.blocks;
      for (let i = blocks.length - 1; i >= 0; i--) {
        const b = blocks[i];
        if (b.kind === "confirm" && !b.resolved) {
          requestId = b.requestId;
          allowApproveAll = b.allowApproveAll;
          break;
        }
      }
    }
    if (!requestId) return;
    if (key === "a" && !allowApproveAll) return;

    ev.preventDefault();
    submitConfirmById(requestId, key as "y" | "n" | "a");
  });

  // ---- confirm submission helper (shared between click and keyboard) ----
  function submitConfirmById(id: string, choice: "y" | "n" | "a"): void {
    if (!chat.submitConfirm(id, choice)) {
      setStatus("请点最新一张工具确认卡");
      return;
    }
    try {
      client.sendConfirm(id, choice);
      setStatus(choice === "n" ? "已提交拒绝…" : "确认中…");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setStatus(`确认发送失败：${message}`);
      chat.model.confirmSubmitting = false;
      const block = chat.model.blocks.find((b) => b.kind === "confirm" && b.requestId === id);
      if (block?.kind === "confirm") {
        block.resolved = undefined;
      }
      if (chat.model.confirmOverlay?.requestId === id) {
        chat.model.confirmOverlay = { ...chat.model.confirmOverlay, resolved: undefined };
      }
      renderChat();
      renderConfirmGlass();
      syncWorkingVisual();
    }
  }

  // ---- confirm clicks ----
  chatEl.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement | null;
    const btn = target?.closest<HTMLButtonElement>("[data-confirm]");
    if (!btn || btn.disabled) return;
    const id = btn.dataset.id;
    const choice = btn.dataset.confirm as "y" | "n" | "a" | undefined;
    if (!id || !choice) return;
    submitConfirmById(id, choice);
  });

  // ---- confirm glass clicks (night perspective) ----
  confirmGlass.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement | null;
    const btn = target?.closest<HTMLButtonElement>("[data-confirm]");
    if (!btn || btn.disabled) return;
    const id = btn.dataset.id;
    const choice = btn.dataset.confirm as "y" | "n" | "a" | undefined;
    if (!id || !choice) return;
    submitConfirmById(id, choice);
    syncWorkingVisual();
  });

  // ---- project sidebar events ----

  root.addEventListener("click", handleReviewSuggestionClick, true);

  // Icon bar: switch overlay panel
  projectEls.iconBar.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>(".sidebar-icon-btn");
    if (!btn?.dataset.panel) return;
    const panel = btn.dataset.panel;
    if (panel === "tasks") {
      projectState.overlayPanel = null;
      setMainFocus("chat");
    } else {
      projectState.switchConfirmTarget = null;
      projectState.projectSearchQuery = "";
      if (panel === "plan") {
        openPlanFull();
        return;
      }
      projectState.overlayPanel = panel as OverlayPanel;
      // Auto-fetch docs list when entering docs panel
      if (panel === "docs") {
        try { client.listDocs(); } catch { /* ignore */ }
      }
      if (panel === "threads") {
        refreshProjectThreads();
      }
    }
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
  });

  // Phase 27 — Services panel actions
  projectEls.servicesPanel.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    const action = btn.dataset.action;
    if (action === "toggle-services") {
      projectState.servicesCollapsed = !projectState.servicesCollapsed;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      return;
    }
    if (action === "services-refresh") {
      refreshServices();
      return;
    }
    if (action === "service-logs") {
      const name = btn.dataset.serviceName;
      if (!name) return;
      projectState.servicesLogName = name;
      projectState.servicesLogText = "";
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      try {
        client.fetchServiceLogs(name, 40);
      } catch (err) {
        projectState.servicesError = err instanceof Error ? err.message : String(err);
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }
    }
  });

  projectEls.terminalsPanel.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    if (btn.dataset.action === "terminal-open") {
      const sessionId = btn.dataset.terminalId;
      if (sessionId) openTerminalDetails(sessionId);
      return;
    }
    if (btn.dataset.action === "terminal-details-close") {
      closeTerminalDetails();
      return;
    }
    if (btn.dataset.action === "terminal-output-refresh") {
      const sessionId = btn.dataset.terminalId;
      if (sessionId && projectState.terminalDetails?.session_id === sessionId) {
        requestTerminalOutput(sessionId, projectState.terminalOutputCursor);
      }
      return;
    }
    if (btn.dataset.action === "terminal-copy-output") {
      const sessionId = btn.dataset.terminalId;
      if (!sessionId || sessionId !== projectState.terminalDetails?.session_id) return;
      const text = projectState.terminalOutput;
      if (!text) return;
      const write = navigator.clipboard?.writeText(text);
      if (!write) {
        setStatus("当前环境不支持复制");
        return;
      }
      void write.then(
        () => setStatus("已复制终端输出"),
        () => setStatus("复制终端输出失败"),
      );
      return;
    }
    if (btn.dataset.action === "terminal-close") {
      const sessionId = btn.dataset.terminalId;
      if (!sessionId) return;
      try {
        setStatus("等待终端关闭确认…");
        client.closeTerminal(sessionId, `terminal-close-${Date.now()}`);
      } catch (err) {
        projectState.terminalOutputError = err instanceof Error ? err.message : String(err);
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }
      return;
    }
    if (btn.dataset.action === "toggle-terminals") {
      projectState.terminalsCollapsed = !projectState.terminalsCollapsed;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      scheduleTerminalPoll();
      return;
    }
    if (btn.dataset.action === "terminals-refresh") {
      refreshTerminals();
    }
  });

  // Overlay back button
  projectEls.overlayBackBtn.addEventListener("click", () => {
    projectState.overlayPanel = null;
    projectState.switchConfirmTarget = null;
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
  });

  // Decision surface: plan overlay + suggestion stack + turn summary
  sidebarEl.addEventListener("click", (ev) => {
    const target = ev.target;
    const el = target instanceof Element ? target : target instanceof Node ? target.parentElement : null;
    const btn = el?.closest<HTMLButtonElement>('[data-action="jump-turn-process"]');
    if (!btn || !sidebarEl.contains(btn)) return;
    ev.preventDefault();
    jumpToCurrentTurnProcess();
  });

  projectEls.taskFlow.addEventListener("click", (ev) => {
    const target = ev.target;
    const btn = target instanceof Element
      ? target.closest<HTMLButtonElement>('[data-action="open-suggestion-review-new"]')
      : null;
    if (!btn) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    setStatus("正在打开方案变更…");
    openPlanReview(btn.dataset.suggestionId);
  }, true);

  projectEls.taskFlow.addEventListener("click", (ev) => {
    const target = ev.target;
    const btn = target instanceof Element
      ? target.closest<HTMLButtonElement>('[data-action="open-suggestion-review"]')
      : null;
    if (!btn) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    openPlanReview(btn.dataset.suggestionId);
  }, true);

  projectEls.taskFlow.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const flowBtn = target.closest<HTMLButtonElement>("[data-flow-stage]");
    if (flowBtn?.dataset.flowStage) {
      projectState.flowPreviewStage = flowBtn.dataset.flowStage as FlowStage;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      return;
    }
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (btn?.dataset.action) {
      switch (btn.dataset.action) {
        case "open-artifact-doc": {
          const path = btn.dataset.artifactPath;
          if (!path) return;
          openDocument(path);
          return;
        }
        case "open-full-plan":
          openPlanFull();
          return;
        case "start-task": {
          const taskId = btn.dataset.taskId?.trim();
          if (!taskId) return;
          startProjectTaskFromUi(taskId);
          return;
        }
        case "direct-implement":
          startDirectImplementFromUi();
          return;
        case "run-verify":
          runProjectVerifyFromUi();
          return;
        case "confirm-plan":
          void projectCallbacks.onPlanConfirm();
          return;
        case "flow-return":
          projectState.flowPreviewStage = null;
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
          return;
        case "confirm-scope":
          projectCallbacks.onScopeConfirm();
          return;
        case "confirm-design":
          projectCallbacks.onDesignConfirm();
          return;
        case "open-projects":
          projectCallbacks.onOpenProjects();
          return;
        case "new-project":
          projectCallbacks.onNewProject();
          return;
        case "accept-milestone":
          projectCallbacks.onMilestoneAccept();
          return;
        case "stop-turn":
          projectCallbacks.onStopTurn();
          return;
        case "open-plan-review": {
          const sid = btn.dataset.suggestionId;
          openPlanReview(sid);
          return;
        }
        case "confirm-drop-while-working":
          projectState.dropTaskPendingWorking = false;
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
          return;
        case "drop-policy": {
          const sid = btn.dataset.suggestionId;
          const policy = btn.dataset.policy as "plan_only" | "agent_cleanup" | "git_guide" | undefined;
          if (sid && policy) acceptSuggestionById(sid, policy);
          return;
        }
        case "jump-review-summary":
          jumpToReviewSummary();
          return;
        case "resume-runaway":
          resumeRunawayFromUi();
          return;
        case "accept-suggestion": {
          const sid = btn.dataset.suggestionId;
          if (sid) acceptSuggestionById(sid);
          return;
        }
        case "review-suggestion": {
          const sid = btn.dataset.suggestionId;
          openPlanReview(sid);
          return;
        }
        case "ignore-suggestion": {
          const sid = btn.dataset.suggestionId;
          if (sid) ignoreSuggestionById(sid);
          return;
        }
        default:
          break;
      }
    }
  });

  projectEls.goalCard.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    switch (btn.dataset.action) {
      case "open-projects":
        projectCallbacks.onOpenProjects();
        return;
      case "new-project":
        projectCallbacks.onNewProject();
        return;
      case "confirm-scope":
        projectCallbacks.onScopeConfirm();
        return;
      case "confirm-design":
        projectCallbacks.onDesignConfirm();
        return;
      case "open-scope-doc":
        openDocument("SCOPE.md");
        return;
      case "confirm-plan":
        void projectCallbacks.onPlanConfirm();
        return;
      case "direct-implement":
        startDirectImplementFromUi();
        return;
      case "run-verify":
        runProjectVerifyFromUi();
        return;
      case "start-task": {
        const taskId = btn.dataset.taskId?.trim();
        if (taskId) {
          startProjectTaskFromUi(taskId);
          return;
        }
        openPlanFull();
        return;
      }
      case "open-plan-review":
        openPlanReview();
        return;
      case "open-full-plan":
        openPlanFull();
        return;
      case "jump-turn-process":
        jumpToCurrentTurnProcess();
        return;
      case "jump-review-summary":
        jumpToReviewSummary();
        return;
        case "stop-turn":
          projectCallbacks.onStopTurn();
          return;
        case "resume-runaway":
          resumeRunawayFromUi();
          return;
        default:
        return;
    }
  });

  // Overlay: project search + list + switch confirm
  projectEls.overlayBody.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;

    if (target.closest("#overlay-new-thread-btn")) {
      projectCallbacks.onNewThread();
      return;
    }
    if (target.closest("[data-action='refresh-threads']")) {
      refreshProjectThreads();
      return;
    }

    const threadBtn = target.closest<HTMLButtonElement>(".overlay-thread-item");
    if (threadBtn?.dataset.threadId && !threadBtn.disabled) {
      projectCallbacks.onOpenThread(threadBtn.dataset.threadId);
      projectState.overlayPanel = null;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      return;
    }

    // Project item click
    const projectBtn = target.closest<HTMLButtonElement>(".overlay-project-item");
    if (projectBtn?.dataset.projectId && !projectBtn.disabled) {
      const pid = projectBtn.dataset.projectId;
      const item = projectState.projects.find((p) => p.id === pid);
      if (item && item.sessionId && !item.isCurrent) {
        // needs confirm: show inline switch confirm
        projectState.switchConfirmTarget = item;
      } else {
        // no session or current: switch directly
        projectCallbacks.onProjectSwitch(pid);
        projectState.overlayPanel = null;
      }
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      return;
    }

    // Switch confirm actions (inside overlay body)
    if (target.closest("#overlay-switch-confirm")) {
      const confirmBtn = target.closest<HTMLButtonElement>("#overlay-switch-confirm-btn");
      const cancelBtn = target.closest<HTMLButtonElement>("#overlay-switch-cancel-btn");
      if (confirmBtn && projectState.switchConfirmTarget) {
        projectCallbacks.onProjectSwitch(projectState.switchConfirmTarget.id);
        projectState.overlayPanel = null;
        projectState.switchConfirmTarget = null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      } else if (cancelBtn) {
        projectState.switchConfirmTarget = null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }
      return;
    }

    // Document item click
    const docBtn = target.closest<HTMLButtonElement>(".overlay-doc-item");
    if (docBtn?.dataset.docPath) {
      openDocument(docBtn.dataset.docPath);
      return;
    }

    // New doc button
    if (target.closest("#overlay-new-doc-btn")) {
      const name = projectState.newDocName.trim();
      if (name) {
        try { client.createDoc(name); } catch { /* ignore */ }
        projectState.newDocName = "";
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }
      return;
    }
  });

  documentEl.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    if (btn.dataset.action === "document-back") {
      setMainFocus("chat");
      return;
    }
    if (btn.dataset.action === "document-list") {
      openDocumentList();
    }
  });

  // Overlay: search input + new-doc input
  projectEls.overlayBody.addEventListener("input", (ev) => {
    const input = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-project-search");
    if (input) {
      projectState.projectSearchQuery = input.value;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      return;
    }
    const docInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-new-doc-input");
    if (docInput) {
      projectState.newDocName = docInput.value;
      return;
    }
  });

  // Overlay: new-doc input Enter key
  projectEls.overlayBody.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      const docInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-new-doc-input");
      if (docInput) {
        const name = projectState.newDocName.trim();
        if (name) {
          try { client.createDoc(name); } catch { /* ignore */ }
          projectState.newDocName = "";
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
      }
    }
  });

  // Change banner actions
  projectEls.changeBanner.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    const action = btn.dataset.action;
    switch (action) {
      case "toggle-change-timeline":
        projectState.changeTimelineExpanded = !projectState.changeTimelineExpanded;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "open-plan-review": {
        const sid = btn.dataset.suggestionId;
        openPlanReview(sid);
        return;
      }
      case "confirm-scope":
        projectCallbacks.onScopeConfirm();
        return;
      case "stop-turn":
        projectCallbacks.onStopTurn();
        return;
      case "open-code-followup":
        if (projectState.codeFollowup?.prefill) {
          input.value = projectState.codeFollowup.prefill;
          input.focus();
          input.style.height = "auto";
          input.style.height = Math.min(input.scrollHeight, 200) + "px";
          setMainFocus("chat");
        }
        projectState.codeFollowup = null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "dismiss-code-followup":
        projectState.codeFollowup = null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "confirm-plan":
        void projectCallbacks.onPlanConfirm();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;
      case "confirm-changes":
        if (projectState.autoConfirmTimerId !== null) {
          window.clearInterval(projectState.autoConfirmTimerId);
          projectState.autoConfirmTimerId = null;
        }
        projectState.changesLevel = null;
        projectState.dismissedTaskChangeFingerprint = "";
        try { client.planConfirmChanges(); } catch { /* ignore */ }
        break;
      case "edit-plan":
        void projectCallbacks.onPlanEdit();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;
      case "toggle-highlight":
        projectState.highlightChanges = !projectState.highlightChanges;
        if (projectState.highlightChanges) {
          openPlanFull();
        }
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.highlightChanges) {
          requestAnimationFrame(() => {
            const first = planFullEl.querySelector<HTMLElement>(".is-highlighted");
            first?.scrollIntoView({ behavior: "smooth", block: "center" });
          });
        }
        return;
      case "collapse-banner":
        projectState.planBannerCollapsed = true;
        projectState.highlightChanges = false;
        if (projectState.changesLevel === "task") {
          projectState.dismissedTaskChangeFingerprint = getTaskChangeFingerprint(projectState);
        }
        if (projectState.autoConfirmTimerId !== null) {
          window.clearInterval(projectState.autoConfirmTimerId);
          projectState.autoConfirmTimerId = null;
        }
        break;
      case "detect-switch":
        {
          const pid = btn.dataset.projectId;
          if (pid) {
            projectState.detectedProject = null;
            projectCallbacks.onProjectSwitch(pid);
          }
        }
        return;
      case "detect-dismiss":
        projectState.detectedProject = null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "undo-last":
        projectState.undoDescription = "";
        if (projectState.undoTimerId) { window.clearTimeout(projectState.undoTimerId); projectState.undoTimerId = null; }
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        try { client.undoLastPlanOp(); } catch { /* ignore */ }
        return;
      case "toggle-degrade-info":
        setStatus(`项目管理器状态: ${projectState.degradationLabel} (${projectState.degradationLevel})`);
        return;
      case "dismiss-degrade":
        projectState.degradationLevel = "L1"; // visually dismiss; real level restored on next state
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "dismiss-external":
        projectState.externalChanges = false;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "dismiss-suggestions":
        projectState.suggestions = [];
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "accept-suggestion": {
        const sid = btn.dataset.suggestionId;
        if (sid) acceptSuggestionById(sid);
        return;
      }
      case "review-suggestion": {
        const sid = btn.dataset.suggestionId;
        openPlanReview(sid);
        return;
      }
      case "ignore-suggestion": {
        const sid = btn.dataset.suggestionId;
        if (sid) ignoreSuggestionById(sid);
        return;
      }
      case "dismiss-auto-fix":
        projectState.autoFixNotices = [];
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "dismiss-operational-notices":
        projectState.dismissedOperationalNoticeFingerprint = projectState.operationalNotices.join("\u001f");
        projectState.operationalNotices = [];
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "dismiss-partner-notice":
        projectState.partnerNotices = [];
        projectState.adoptedFooterMessage = null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "dismiss-warnings":
        projectState.dismissedWarningFingerprint = projectState.planWarnings.join("\u001f");
        projectState.planWarnings = [];
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "jump-review-summary":
        jumpToReviewSummary();
        return;
      case "expand-banner":
        projectState.planBannerCollapsed = false;
        break;
    }
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
  });

  // Task checkbox click — optimistic update + WS toggle (decision surface or plan overlay)
  const onTaskCheckboxClick = (ev: Event) => {
    const cb = (ev.target as HTMLElement).closest<HTMLInputElement>(".task-checkbox");
    if (!cb?.dataset.line) return;
    const line = parseInt(cb.dataset.line, 10);
    if (isNaN(line)) return;
    const done = cb.checked;

    // Capture pre-toggle state for rollback
    const prevPhases: Array<{ title: string; tasks: Array<{ line: number; done: boolean; status: string }> }> = [];
    for (const phase of projectState.taskPhases) {
      prevPhases.push({
        title: phase.title,
        tasks: phase.tasks.map((t) => ({ line: t.line, done: t.done, status: t.status })),
      });
    }

    // Optimistic local update
    for (const phase of projectState.taskPhases) {
      for (const task of phase.tasks) {
        if (task.line === line) {
          task.done = done;
          task.status = done ? "done" : "pending";
        }
      }
    }
    // reassign current task
    let found = false;
    for (const phase of projectState.taskPhases) {
      for (const task of phase.tasks) {
        if (!task.done && !found) { task.status = "current"; found = true; }
        else if (!task.done) { task.status = "pending"; }
        else { task.status = "done"; }
      }
    }
    renderProjectSidebar(projectEls, projectState, projectCallbacks);

    // Send WS
    try {
      client.toggleTask(line, done);
    } catch (err) {
      // Rollback on send failure
      for (const phase of projectState.taskPhases) {
        for (const task of phase.tasks) {
          const prev = prevPhases.flatMap((p) => p.tasks).find((t) => t.line === task.line);
          if (prev) { task.done = prev.done; task.status = prev.status as TaskItem["status"]; }
        }
      }
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      setStatus(`任务更新失败：${err instanceof Error ? err.message : String(err)}`);
    }
  };
  projectEls.taskFlow.addEventListener("click", onTaskCheckboxClick);
  projectEls.overlayBody.addEventListener("click", onTaskCheckboxClick);
  planFullEl.addEventListener("click", onTaskCheckboxClick);

  // ---- context menu ----
  let contextMenuEl: HTMLElement | null = null;
  let contextMenuTask: TaskItem | null = null;

  function destroyContextMenu(): void {
    if (contextMenuEl) {
      contextMenuEl.remove();
      contextMenuEl = null;
      contextMenuTask = null;
    }
  }

  function showContextMenu(task: TaskItem, x: number, y: number): void {
    destroyContextMenu();

    const menu = document.createElement("div");
    menu.className = "context-menu";
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;

    const items: Array<{ label: string; action: string; danger?: boolean; disabled?: boolean; shortcut?: string }> = [
      { label: task.done ? "标记未完成" : "标记完成", action: "toggle", shortcut: "点击 checkbox" },
      { label: "上移", action: "move-up", disabled: !canMoveTaskUp(task) },
      { label: "下移", action: "move-down", disabled: !canMoveTaskDown(task) },
      null as unknown as { label: string; action: string }, // separator
      { label: "跳过（暂缓）", action: "skip" },
      { label: "拆分…", action: "split", shortcut: "Plan Agent" },
      null as unknown as { label: string; action: string }, // separator
      { label: "删除", action: "delete", danger: true },
    ];

    for (const item of items) {
      if (item === null) {
        const sep = document.createElement("div");
        sep.className = "context-menu-sep";
        menu.appendChild(sep);
        continue;
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `context-menu-item${item.danger ? " is-danger" : ""}`;
      btn.textContent = item.label;
      btn.dataset.action = item.action;
      if (item.disabled) btn.disabled = true;
      if (item.shortcut) {
        const lbl = document.createElement("span");
        lbl.className = "context-menu-label";
        lbl.textContent = item.shortcut;
        btn.appendChild(lbl);
      }
      menu.appendChild(btn);
    }

    document.body.appendChild(menu);
    contextMenuEl = menu;
    contextMenuTask = task;

    // Click handler on menu
    menu.addEventListener("click", (ev) => {
      const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>(".context-menu-item");
      if (!btn?.dataset.action || btn.disabled) return;
      const action = btn.dataset.action;
      handleContextMenuAction(action);
    });

    // Close on outside click
    setTimeout(() => {
      document.addEventListener("click", destroyContextMenu, { once: true });
      document.addEventListener("contextmenu", destroyContextMenu, { once: true });
    }, 0);
  }

  function findTaskByLine(line: number): { phaseIdx: number; taskIdx: number } | null {
    for (let pi = 0; pi < projectState.taskPhases.length; pi++) {
      const tasks = projectState.taskPhases[pi].tasks;
      for (let ti = 0; ti < tasks.length; ti++) {
        if (tasks[ti].line === line) return { phaseIdx: pi, taskIdx: ti };
      }
    }
    return null;
  }

  function canMoveTaskUp(task: TaskItem): boolean {
    const pos = findTaskByLine(task.line);
    if (!pos) return false;
    return pos.taskIdx > 0;
  }

  function canMoveTaskDown(task: TaskItem): boolean {
    const pos = findTaskByLine(task.line);
    if (!pos) return false;
    return pos.taskIdx < projectState.taskPhases[pos.phaseIdx].tasks.length - 1;
  }

  function handleContextMenuAction(action: string): void {
    destroyContextMenu();
    if (!contextMenuTask) return;
    const task = contextMenuTask;

    try {
      switch (action) {
        case "toggle":
          client.planToggleTask(task.line, !task.done);
          break;
        case "move-up":
          client.planReorderTask(task.line, "up");
          break;
        case "move-down":
          client.planReorderTask(task.line, "down");
          break;
        case "skip":
          client.planSkipTask(task.line);
          break;
        case "delete":
          client.planDropTask(task.line);
          break;
        case "split":
          setStatus("项目管理器分析中…");
          try { client.splitPlanTask(task.line); } catch (err) {
            setStatus(`拆分失败：${err instanceof Error ? err.message : String(err)}`);
          }
          return;
      }
    } catch (err) {
      setStatus(`操作失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // contextmenu on task flow + plan overlay
  const onTaskContextMenu = (ev: MouseEvent) => {
    const taskEl = (ev.target as HTMLElement).closest<HTMLElement>(".task-item");
    if (!taskEl?.dataset.line) return;
    ev.preventDefault();
    const line = parseInt(taskEl.dataset.line, 10);
    if (isNaN(line)) return;
    for (const phase of projectState.taskPhases) {
      const t = phase.tasks.find((tk) => tk.line === line);
      if (t) { showContextMenu(t, ev.clientX, ev.clientY); return; }
    }
  };
  projectEls.taskFlow.addEventListener("contextmenu", onTaskContextMenu);
  projectEls.overlayBody.addEventListener("contextmenu", onTaskContextMenu);

  // compat: keep old event wiring working for elements hidden in DOM
  projectEls.pickerRefreshBtn.addEventListener("click", () => projectCallbacks.onRefreshProjects());
  projectEls.switchConfirmBtn.addEventListener("click", () => projectCallbacks.onProjectSwitchConfirm());
  projectEls.switchCancelBtn.addEventListener("click", () => projectCallbacks.onProjectSwitchCancel());
  projectEls.planConfirmBtn.addEventListener("click", () => { void projectCallbacks.onPlanConfirm(); });
  projectEls.planEditBtn.addEventListener("click", () => { void projectCallbacks.onPlanEdit(); });

  // ---- server events ----
  const off = client.onEvent((event: ServerEvent) => {
    switch (event.type) {
      case "session.banner":
        if (!shouldApplyProjectEvent(projectState, event.project_id)) break;
        if ((event.project_id || "") !== projectState.projectId) {
          resetProjectScopedState(projectState);
        }
        if (event.session_id !== chat.model.sessionId) {
          renderedPrints = [];
          renderedSegmentPrints = [];
        }
        chat.handleEvent(event);
        projectState.currentSessionId = event.session_id;
        if (hydrationSessionId && event.session_id === hydrationSessionId) {
          setStatus(`加载聊天 ${shortSessionId(hydrationSessionId)}…`);
        }
        if (event.project_id) {
          projectState.projectId = event.project_id;
          projectState.planStatus = event.project_plan_status ?? "draft";
          projectState.tasksDone = event.project_tasks_done ?? 0;
          projectState.tasksTotal = event.project_tasks_total ?? 0;
          syncFreeChatFromSession(true, event.session_id);
          setPerspective("project", "session");
        } else {
          projectState.projectId = "";
          syncFreeChatFromSession(false, event.session_id);
          setPerspective("project", "session");
        }
        updatePlaceholder();
        updateWorkbenchEmpty();
        syncArchivedViewUi();
        setStatus(`会话 ${event.session_id} · ${event.llm_model_label || "Flash"} · ${event.turn_mode_label}`);
        refreshTopbar();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        debouncedListSessions();
        if (event.project_id) {
          refreshProjectThreads();
        }
        break;

      case "session.list":
        sessionsDropdown = event.sessions;
        topbarState.sessionCount = event.sessions.length;
        refreshTopbar();
        if (sessionsOpen) renderSessionsDropdown();
        break;

      case "session.memory":
        topbarState.memoryLabel = `${event.message_count} 条 · ${event.memory_mode_label}`;
        break;
        updateTokenBar(event.token_usage, event.token_limit);
        break;

      case "llm.usage":
        recordLlmUsage(event.prompt_tokens, event.cached_tokens, event.cache_ratio);
        break;

      case "evolve.proposals":
        handleProposalsEvent(event.items);
        break;

      case "context.switch.request":
        projectState.switchInProgress = true;
        projectState.pendingPickerId = (event.project_id || "").trim();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "context.switch.done":
        if (event.applied === false || event.choice !== "y") {
          projectState.switchInProgress = false;
          projectState.pendingPickerId = "";
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
          break;
        }
        resetProjectScopedState(projectState);
        projectState.switchInProgress = false;
        projectState.switchOverlay = null;
        projectState.pendingPickerId = "";
        projectState.projectId = (event.project_id || "").trim();
        projectState.currentSessionId = event.session_id || "";
        freeChatActive = !projectState.projectId;
        client.listProjects();
        if (projectState.projectId) refreshProjectThreads();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        updatePlaceholder();
        updateWorkbenchEmpty();
        composerWire.syncSendEnabled();
        refreshTopbar();
        setStatus(event.message || "已切换工作上下文");
        break;

      case "project.release.accepted":
        projectState.milestoneAccepted = Boolean(event.release_acceptance.accepted);
        projectState.milestoneAcceptedAt = event.release_acceptance.accepted_at ?? null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        setStatus("发布确认已保存");
        break;

      case "project.state":
        if (!applyProjectStateEvent(projectState, event)) break;
        if (projectState.projectId) {
          freeChatActive = false;
        }
        if (!perspectiveLocked) setPerspective("project", "session");
        updatePlaceholder();
        updateWorkbenchEmpty();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          refreshTopbar();
          if (!projectState.adoptPendingId) {
            refreshServices();
            refreshProjectThreads();
          }
        }
        break;

      case "project.plan.state":
        if (!applyProjectPlanState(projectState, event)) break;
        resolvePendingAdopt();
        projectState.partnerBusy = false;
        if (!perspectiveLocked) setPerspective("project", "session");
        updatePlaceholder();
        if (projectState.mainFocus === "plan_review") {
          if (getActionableQueue().length === 0) {
            closePlanMainFocus();
            setStatus("当前没有待处理方案");
          } else {
            renderPlanReviewPane();
          }
        } else if (projectState.mainFocus === "plan_full") {
          renderPlanFullPane();
        }
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          refreshTopbar();
        }

        // Auto-confirm timer for task-level changes
        if (projectState.autoConfirmTimerId !== null) {
          window.clearInterval(projectState.autoConfirmTimerId);
          projectState.autoConfirmTimerId = null;
        }
        if (
          projectState.changesLevel === "task"
          && getTaskChangeFingerprint(projectState) !== projectState.dismissedTaskChangeFingerprint
        ) {
          let remaining = 30;
          const countdownInterval = window.setInterval(() => {
            remaining--;
            const el = sidebarEl.querySelector<HTMLElement>("[data-countdown]");
            if (el) el.textContent = String(remaining);
            if (remaining <= 0) {
              window.clearInterval(countdownInterval);
              if (
                projectState.autoConfirmTimerId === countdownInterval as unknown as number
                && projectState.changesLevel === "task"
                && getTaskChangeFingerprint(projectState) !== projectState.dismissedTaskChangeFingerprint
              ) {
                projectState.changesLevel = null;
                projectState.dismissedTaskChangeFingerprint = "";
                try { client.planConfirmChanges(); } catch { /* */ }
                renderProjectSidebar(projectEls, projectState, projectCallbacks);
              }
            }
          }, 1000);
          projectState.autoConfirmTimerId = countdownInterval as unknown as number;
        }
        break;

      case "project.code_followup":
        projectState.codeFollowup = {
          mode: event.mode,
          prefill: event.prefill,
          paths: event.paths,
          droppedBody: event.dropped_body,
          droppedId: event.dropped_id,
          guide: event.guide,
        };
        if (event.mode === "agent_cleanup" && event.prefill) {
          input.value = event.prefill;
          input.style.height = "auto";
          input.style.height = Math.min(input.scrollHeight, 200) + "px";
          setMainFocus("chat");
          setStatus("已准备清理请求；请确认内容后发送");
        } else {
          setStatus("已准备 git 清理指引；不会自动 revert 或提交");
        }
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "plan.subagent.start":
        projectState.partnerBusy = true;
        if (!projectState.runawayEnabled) {
          chat.pushPlanSubagentCard("running", { taskPreview: event.task_preview });
        }
        setStatus("计划搭档整理中…");
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "session.history":
        if (
          event.session_id
          && hydrationSessionId
          && event.session_id !== hydrationSessionId
        ) break;
        if (
          event.session_id
          && !hydrationSessionId
          && projectState.currentSessionId
          && event.session_id !== projectState.currentSessionId
        ) break;
        chat.handleEvent(event);
        if (pendingSwitchBatch || hydrationSessionId) {
          completeHydrationWait(event.session_id);
          setStatus("就绪");
        }
        break;

      case "plan.subagent.done":
        projectState.partnerBusy = false;
        if (!projectState.runawayEnabled) {
          chat.updatePlanSubagentCard("proposals_ready", {
            summary: event.summary,
            proposalCount: event.proposal_count,
          });
        }
        setStatus("就绪");
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "review.subagent.start":
        chat.pushReviewSubagentCard("running", { taskPreview: event.task_preview });
        setStatus("交付审查中…");
        break;

      case "review.subagent.done":
        chat.updateReviewSubagentCard("done", {
          summary: event.summary_preview || event.summary,
          verdict: event.verdict ?? undefined,
          blockersCount: event.blockers_count,
        });
        if (event.verdict) {
          projectState.reviewVerdict = event.verdict;
        }
        if (typeof event.blockers_count === "number") {
          projectState.reviewBlockersCount = event.blockers_count;
        }
        setStatus("就绪");
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.plan.transcript.clear":
        projectState.partnerBusy = false;
        projectState.partnerNotices = [];
        break;

      case "project.list":
        applyProjectListEvent(projectState, event);
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.threads":
        if (!applyProjectThreadsEvent(projectState, event)) break;
        projectState.currentSessionId = projectState.currentSessionId || event.active_session_id || "";
        syncArchivedViewUi();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.thread.new.done":
        projectState.currentSessionId = event.session_id;
        projectState.activeSessionId = event.session_id;
        client.listProjects();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          refreshTopbar();
        }
        syncArchivedViewUi();
        updatePlaceholder();
        updateWorkbenchEmpty();
        composerWire.syncSendEnabled();
        setStatus(event.message);
        break;

      case "services.list.done":
        projectState.servicesLoading = false;
        projectState.servicesError = "";
        projectState.services = Array.isArray(event.services) ? event.services : [];
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "services.state":
        projectState.servicesLoading = false;
        projectState.servicesError = "";
        projectState.services = Array.isArray(event.services) ? event.services : [];
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "services.logs.done":
        projectState.servicesLogName = event.name;
        projectState.servicesLogText = event.text || "";
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "terminal.list.done":
        if (event.request_id && event.request_id !== terminalRequestId) break;
        if (terminalRequestTimeoutId !== null) {
          window.clearTimeout(terminalRequestTimeoutId);
          terminalRequestTimeoutId = null;
        }
        projectState.terminalsLoading = false;
        projectState.terminalsError = event.ok ? "" : (event.error || "读取终端状态失败");
        projectState.terminalSessions = Array.isArray(event.sessions) ? event.sessions : [];
        if (projectState.terminalDetails) {
          const current = projectState.terminalSessions.find(
            (session) => session.session_id === projectState.terminalDetails?.session_id,
          );
          if (current) {
            projectState.terminalDetails = current;
          } else {
            closeTerminalDetails();
            break;
          }
        }
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "terminal.output.done":
        if (
          event.request_id
          && event.request_id !== terminalOutputRequestId
        ) break;
        if (
          event.session_id !== terminalOutputRequestSessionId
          || event.session_id !== projectState.terminalDetails?.session_id
        ) break;
        if (terminalOutputRequestTimeoutId !== null) {
          window.clearTimeout(terminalOutputRequestTimeoutId);
          terminalOutputRequestTimeoutId = null;
        }
        projectState.terminalOutputLoading = false;
        projectState.terminalOutputError = event.ok ? "" : (event.error || "输出暂时不可用");
        projectState.terminalOutputCursorReset = Boolean(event.cursor_reset);
        projectState.terminalOutputTruncated = Boolean(event.truncated);
        if (event.ok) {
          projectState.terminalOutput = event.cursor_reset
            ? event.output
            : projectState.terminalOutput + event.output;
          projectState.terminalOutputCursor = event.next_cursor;
          if (event.session) projectState.terminalDetails = event.session;
        }
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "terminal.close.done":
        if (event.session_id === projectState.terminalDetails?.session_id && !event.ok) {
          projectState.terminalOutputError = event.error || "关闭终端失败";
        }
        if (event.ok) {
          setStatus("终端关闭请求已提交");
          refreshTerminals();
        }
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "turn.evidence":
        projectState.turnArmedId = event.armed_task_id || "";
        projectState.turnArmedText = event.armed_task_text || "";
        projectState.turnEvidence = Array.isArray(event.items) ? event.items : [];
        projectState.turnGateNotice =
          typeof event.gate_notice === "string" ? event.gate_notice : "";
        {
          const rel = event.reliability;
          projectState.turnPostcondition =
            rel && typeof rel.postcondition === "string" ? rel.postcondition : "none";
          projectState.turnCircuitOpen = Array.isArray(rel?.circuit_open)
            ? rel.circuit_open.filter((x): x is string => typeof x === "string")
            : [];
          projectState.turnPlaybookId =
            rel && typeof rel.playbook_id === "string" ? rel.playbook_id : "";
          projectState.turnFailureClass =
            rel && typeof rel.failure_class === "string" ? rel.failure_class : "";
        }
        if (perspective === "project") {
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
        break;

      case "project.switch.request":
        projectState.switchInProgress = false;
        projectState.switchOverlay = {
          requestId: event.request_id,
          projectId: event.project_id,
          message: event.message,
          action: event.action,
        };
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.switch.done":
        if (
          projectState.pendingPickerId
          && event.project_id !== projectState.pendingPickerId
        ) break;
        resetProjectScopedState(projectState);
        if (event.session_replaced) {
          startHydrationWait(event.session_id, "切换项目");
        } else {
          completeHydrationWait();
          debouncedListProjects();
          debouncedListSessions();
        }
        projectState.switchOverlay = null;
        projectState.pendingPickerId = "";
        projectState.projectId = event.project_id;
        projectState.currentSessionId = event.session_id;
        if (event.project_id) {
          freeChatActive = false;
        }
        // perform_project_switch already emits project.state, session.banner,
        // session.memory, session.history — avoid duplicate refresh round-trips.
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          refreshTopbar();
        }
        updatePlaceholder();
        updateWorkbenchEmpty();
        composerWire.syncSendEnabled();
        if (!event.session_replaced) {
          setStatus(event.message);
        }
        break;

      case "plan.request":
        projectState.planOverlay = {
          requestId: event.request_id,
          title: event.title,
          summary: event.summary,
          tasksPreview: event.tasks_preview,
          planStatus: event.plan_status,
        };
        projectState.planStatus = event.plan_status;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "plan.done":
        if (event.choice === "confirm") {
          projectState.planStatus = "confirmed";
          projectState.planOverlay = null;
          setStatus("计划已确认，可以开始写代码。");
        } else {
          projectState.planOverlay = null;
        }
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          refreshTopbar();
        }
        client.refreshProject();
        break;

      case "project.task.toggle.error":
        // Rollback optimistic toggle
        projectState.taskPhases = parseTasksMarkdown(projectState.tasksMarkdown);
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        setStatus(`任务更新失败：${event.message}`);
        break;

      case "project.task.toggle.done":
        // Confirmed; project.state will follow with authoritative data
        break;

      case "project.plan.confirm_changes.done":
        projectState.planBannerCollapsed = true;
        projectState.changesLevel = null;
        projectState.dismissedTaskChangeFingerprint = "";
        client.refreshProject();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.doc.list.done":
        projectState.projectDocs = event.docs;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.doc.read.done":
        projectState.currentDocContent = event.content;
        if (projectState.mainFocus === "document") renderDocumentPane();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.doc.create.done":
        projectState.newDocName = "";
        client.listDocs();
        break;

      case "project.task.add.done":
        // Refresh tasks from server to replace optimistic placeholder
        client.refreshProject();
        break;

      case "project.undo.available":
        projectState.undoDescription = event.description;
        if (projectState.undoTimerId) { window.clearTimeout(projectState.undoTimerId); }
        projectState.undoTimerId = window.setTimeout(() => {
          projectState.undoDescription = "";
          projectState.undoTimerId = null;
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }, 3000) as unknown as number;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.detect":
        // Auto-detect: workspace directory looks like a project
        projectState.detectedProject = { id: event.project_id, reason: event.reason };
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        setStatus(event.reason);
        break;

      case "execution.state":
        if (!chat.handleEvent(event)) break;
        if (event.state === "queued") {
          setStatus("等待续接…");
        } else if (event.state === "stopping") {
          setStatus("正在停止…");
        } else if (event.state === "running") {
          setStatus("处理中…");
        } else if (event.finish_reason) {
          setTurnEndStatus(event.finish_reason);
        }
        syncWorkingVisual();
        break;

      case "tool.start":
        if (!chat.handleEvent(event)) break;
        setStatus(`· ${event.tool}`);
        break;

      case "llm.pending":
        if (!chat.handleEvent(event)) break;
        if (!chat.model.cancelRequested) {
          setStatus("处理中…");
        }
        syncToolElapsedTimer();
        break;

      case "tool.progress":
        if (!chat.handleEvent(event)) break;
        if (typeof event.text === "string" && event.text.trim()) {
          setStatus(event.text.trim());
        }
        break;

      case "tool.end":
        if (!chat.handleEvent(event)) break;
        if (event.tool === "run_evolved" || /interactive_terminal/.test(event.summary || "")) {
          refreshTerminals();
        }
        if (!chat.model.confirmPending) {
          setStatus(event.ok ? (chat.isWorking() ? "处理中…" : "就绪") : "工具失败");
        }
        break;

      case "reasoning.delta":
        if (!chat.handleEvent(event)) break;
        if (!chat.model.cancelRequested && !statusText.startsWith("·")) {
          setStatus("处理中…");
        }
        break;

      case "assistant.done":
        if (!chat.handleEvent(event)) break;
        if (perspective === "project") {
          client.refreshProject();
        }
        if (!chat.model.cancelRequested && !chat.model.confirmPending) {
          setStatus(chat.isWorking() ? "处理中…" : "就绪");
        }
        syncWorkingVisual();
        break;

      case "turn.notice":
        if (!chat.handleEvent(event)) break;
        if (event.text) {
          setStatus(event.text.length > 48 ? `${event.text.slice(0, 48)}…` : event.text);
        }
        syncWorkingVisual();
        break;

      case "turn.end":
        if (!chat.handleEvent(event)) break;
        setTurnEndStatus(event.finish_reason);
        syncWorkingVisual();
        scheduleRunawayIdleResume();
        break;

      case "notice":
        chat.model.blocks.push({ kind: "notice", text: event.text });
        renderChat();
        if (event.runaway_cancel_available !== undefined) {
          projectState.runawayCancelAvailable = event.runaway_cancel_available;
          syncWorkingVisual();
        }
        if (/狂奔|恢复|租约|续接|占用|启动狂奔/.test(event.text)) {
          setStatus(event.text.length > 48 ? `${event.text.slice(0, 48)}…` : event.text);
          if (/已在执行|占用|超时|租约/.test(event.text)) {
            clearRunawayResumePending();
            syncWorkingVisual();
          }
        }
        if (
          projectState.planStatus !== "confirmed" &&
          /计划已确认|可以开始写代码/.test(event.text)
        ) {
          projectState.planStatus = "confirmed";
          projectState.planOverlay = null;
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
          if (projectState.projectId) {
            refreshTopbar();
          }
          client.refreshProject();
        }
        break;

      case "confirm.request":
      case "confirm.done":
        if (!chat.handleEvent(event)) break;
        if (event.type === "confirm.request") {
          requestAnimationFrame(() => {
            requestAnimationFrame(() => scrollPendingConfirmIntoView());
          });
        }
        break;

      case "error":
        if (pendingAdoptAccept) {
          const adoptError = String(event.message || "unknown error").trim();
          clearPendingAdopt();
          projectState.partnerBusy = false;
          setStatus(`閲囩撼澶辫触锛�${adoptError}`);
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
          if (projectState.mainFocus === "plan_review") {
            renderPlanReviewPane();
          }
        }
        if (projectState.servicesLoading) {
          projectState.servicesLoading = false;
          projectState.servicesError = event.message;
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
        if (projectState.switchInProgress || projectState.switchOverlay) {
          projectState.switchInProgress = false;
          projectState.pendingPickerId = "";
          completeHydrationWait();
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
        chat.handleEvent(event);
        setStatus("错误");
        break;

      default:
        chat.handleEvent(event);
        break;
    }
  });

  function planStatusLabel(): string {
    if (projectState.runawayEnabled) {
      if (projectState.runawayVersion >= 2) {
        if (chat.isWorking()) return "狂奔进行中";
        if (projectState.runawayBlocked) {
          return projectState.runawayUserLine || projectState.runawayStatus || "需要处理";
        }
        if (projectState.runawayUserLine) return projectState.runawayUserLine;
        return "狂奔待命";
      }
      if (projectState.runawayStatus) return projectState.runawayStatus;
    }
    if (projectState.planStatus === "confirmed") {
      if (projectState.tasksAllDone && projectState.tasksTotal > 0) {
        if (projectState.executionStage === "release" && projectState.milestoneAccepted) return "项目已完成";
        return "交付检查";
      }
      return "项目进行中";
    }
    if (projectState.planStatus === "plan_dirty") return "方案有变更";
    return "等待方案确认";
  }

  // ---- initial render ----
  mountToolDisplayEditor();
  onToolDisplayOverridesChange(() => renderChat());
  updatePlaceholder();
  refreshTopbar();
  renderProposalsPanel();
  renderChat();
  setComposerEnabled(true);
  setStatus("已连接");

  // Phase 34: workbench layout + empty gate
  emptyNewBtn.addEventListener("click", () => handleNewProject());
  emptyPickBtn.addEventListener("click", () => {
    projectState.overlayPanel = "projects";
    projectState.switchConfirmTarget = null;
    projectState.projectSearchQuery = "";
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
    try {
      client.listProjects();
    } catch {
      /* ignore */
    }
  });
  emptyFreeChatBtn.addEventListener("click", () => {
    void handleFreeChat();
  });
  setPerspective("project", "auto");
  updateWorkbenchEmpty();
  composerWire.syncSendEnabled();
  try {
    client.listProjects();
  } catch {
    /* ignore */
  }
  refreshTerminals();
  scheduleTerminalPoll();

  return () => {
    destroyed = true;
    if (cancelledStatusTimer !== null) window.clearTimeout(cancelledStatusTimer);
    clearCancelSafety();
    if (toolElapsedTimer !== null) window.clearInterval(toolElapsedTimer);
    if (renderThrottleTimer !== null) window.clearTimeout(renderThrottleTimer);
    if (terminalPollTimerId !== null) window.clearTimeout(terminalPollTimerId);
    if (terminalRequestTimeoutId !== null) window.clearTimeout(terminalRequestTimeoutId);
    if (terminalOutputRequestTimeoutId !== null) window.clearTimeout(terminalOutputRequestTimeoutId);
    terminalOutputRequestSerial += 1;
    renderedPrints = [];
    renderedSegmentPrints = [];
    focusObserver?.disconnect();
    fileDrop.destroy();
    off();
    shellEl.classList.remove("is-working");
    setAgentBusy(false);
    root.innerHTML = "";
  };
}
