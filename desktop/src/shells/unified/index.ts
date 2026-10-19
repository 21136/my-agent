import { setAgentBusy } from "../../agent-busy";
import { wireComposerAttachments } from "../../composer-attachments";
import { mountFileDrop } from "../../file-drop";
import { hydrateMermaid, renderMarkdown } from "../../markdown";
import { bindLazyMarkdownLookup, observeLazyMarkdown, resetLazyMarkdownObserver } from "../../lazy-markdown";
import { formatUserMessageHtml } from "../../user-message";
import { segmentChatBlocks, segmentPrint, renderTurnCardShell, liveTurnCardProcessBlocks, type ChatRenderSegment } from "../turn-card-layout";
import { activityEntriesPrint, ensureProcessEntries, isProcessThinkingLive, type ThinkEntry } from "../activity-state";
import { isProcessBlockEmpty, orderTurnBlocksForDisplay } from "../process-placement";
import { getProcessTools, renderActivityTimeline, syncThinkDom, timelineHasThinking } from "../activity-timeline";
import { createChatSession, escapeHtml, turnEndStatusText, checkerVerdictStatusText, formatToolElapsed, isConfirmInProgressLabel, type ChatBlock } from "../chat-state";
import { settledToolStatus } from "../tool-outcome";
import { renderTopbar, type TopbarState } from "./topbar";
import { renderProposals, currentProposal, nextProposalIndex, type ProposalsState } from "./proposals";
import {
  canRequestProjectDocs,
  noticeMirrorsToStatus,
  sessionBannerStatus,
  sessionListMeta,
  userFacingContextStatus,
  userFacingErrorStatus,
} from "./ordinary-guards";
import {
  setupProjectPanel,
  renderProjectSidebar,
  renderProjectGoalCard,
  deriveProjectGoalViewModel,
  hasProjectBlocker,
  renderProjectBlockerDetails,
  applyProjectStateEvent,
  resetProjectScopedState,
  beginAtomicProjectSwitch,
  shouldApplyProjectEvent,
  applyProjectListEvent,
  applyProjectThreadsEvent,
  isViewingArchivedThread,
  renderRailStageEmpty,
  renderProjectsStage,
  type FlowStage,
  type ProjectPanelState,
  type ProjectPanelCallbacks,
} from "./project-panel";
import {
  displayDocTitle,
  docCatalogMenuItems,
  docStem,
  finalizeDocumentOutline,
  mountPopupMenu,
  normalizeNewDocPath,
  renderDocOutlineHtml,
  renderDocumentReaderHtml,
  renameDraftForDoc,
  sortProjectDocs,
} from "./doc-reading";
import { isRailTab, mainFocusForRail, type MainFocus, type RailTab } from "./rail";
import {
  renderSessionFiltersHtml,
  renderSessionListHtml,
  type SessionFilter,
  type SessionListInput,
} from "./session-list";
import { isCrossProjectSession } from "./project-switch-state";
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
import {
  addCompanionSticker,
  ambientRoutineStatus,
  buildCompanionSyncPayload,
  calibrateCompanionFirstMetFromSessions,
  currentPartnerName,
  loadCompanionBond,
  petAvatarSrc,
  petTurnState,
  recordCompanionCare,
  removeCompanionSticker,
  renderIntimatePresenceStageHtml,
  renderPetAvatarHtml,
  renderPetNameHtml,
  touchCompanionVisit,
  type PetTurnSummary,
} from "./companion";
import "./unified.css";
import "./neon-twin.css";
import "./sidebar-v2.css";

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
    if (perspective !== p) {
      renderedPrints = [];
      renderedSegmentPrints = [];
    }
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
      topbarState.contextLabel = "";
      // Task-driven header CTAs (开始任务 / 确认开工) were removed with the task subsystem.
      topbarState.headerCtas = [];
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
      undefined, // 会话 dropdown replaced by the sidebar session list
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
    projectEntry: "",
    projectTemplate: "",
    mapMarkdown: "",
    projects: [],
    externalImportPath: "",
    externalImportProjectId: "",
    externalImportScan: null,
    switchOverlay: null,
    switchInProgress: false,
    pendingPickerId: "",
    overlayPanel: null,
    railTab: "now",
    switchConfirmTarget: null,
    projectSearchQuery: "",
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
    projectDocs: [],
    currentDocPath: "",
    currentDocContent: "",
    currentDocMtime: 0,
    currentDocHash: "",
    newDocName: "",
    newDocFolder: "",
    docSearchQuery: "",
    renamingDocPath: "",
    renameDraft: "",
    deleteConfirmPath: "",
    docView: "preview",
    docEditDraft: "",
    docDirty: false,
    docSaving: false,
    docConflictPrompt: false,
    docsChatSplit: true,
    docPlaceholderCount: 0,
    recentlyChangedDocs: new Set<string>(),
    pendingDocUndo: null,
    detectedProject: null,
    undoDescription: "",
    undoTimerId: null,
    services: [],
    servicesLoading: false,
    servicesError: "",
    servicesLogName: "",
    servicesLogText: "",
    terminalSessions: [],
    terminalsLoading: false,
    terminalsError: "",
    terminalsCollapsed: true,
    terminalsEndedCollapsed: true,
    terminalsEndedShowAll: false,
    terminalDetails: null,
    terminalOutput: "",
    terminalOutputCursor: 0,
    terminalOutputLoading: false,
    terminalOutputError: "",
    terminalOutputCursorReset: false,
    terminalOutputTruncated: false,
    turnEvidence: [],
    turnGateNotice: "",
    turnPostcondition: "none",
    turnCircuitOpen: [],
    turnPlaybookId: "",
    turnFailureClass: "",
    lastTurnFinishReason: "",
    activeSessionId: "",
    threads: [],
    threadsLoading: false,
    currentSessionId: "",
    mainFocus: "chat",
    servicesCollapsed: true,
    turnInProgress: false,
    deliveryProfile: "solo",
    reviewVerdict: null,
    reviewBlockersCount: 0,
    reviewProgressBlocked: false,
    scopeConfirmedAt: "",
    scopeNeedsReconfirm: false,
    flowPreviewStage: null,
    milestoneAccepted: false,
    milestoneAcceptedAt: null,
  };

  let statusText = "连接中…";
  let cancelledStatusTimer: number | null = null;
  let cancelSafetyTimer: number | null = null;
  let destroyed = false;
  let renderThrottleTimer: number | null = null;
  let processToggleAnchor: { turnKey: string; top: number; focus: boolean } | null = null;
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
        resetProcessJumpCursor();
        syncWorkingVisual();
        resetTurnCacheStats();
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
    <div class="unified-shell" data-perspective="project" data-rail="now">
      <aside class="unified-sidebar" id="unified-sidebar">
        <div class="sidebar-resize-handle" id="sidebar-resize-handle"></div>
        <nav class="sidebar-icon-bar" id="sidebar-icon-bar" aria-label="主导航">
          <button type="button" class="sidebar-icon-btn is-active" data-panel="now" title="当下"><svg class="sidebar-icon-svg" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg><span class="sidebar-icon-label">当下</span></button>
          <button type="button" class="sidebar-icon-btn" data-panel="docs" title="文档"><svg class="sidebar-icon-svg" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/></svg><span class="sidebar-icon-label">文档</span></button>
          <button type="button" class="sidebar-icon-btn" data-panel="projects" title="我的项目" id="icon-btn-projects"><svg class="sidebar-icon-svg" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg><span class="sidebar-icon-label">项目</span>
            <span class="sidebar-icon-badge" id="project-count-badge">0</span>
          </button>
          <span class="sidebar-degrade-dot hidden" id="sidebar-degrade-dot" data-action="toggle-degrade-info" title="项目管理器状态"></span>
          <span class="sidebar-icon-spacer" aria-hidden="true"></span>
          <button type="button" class="sidebar-icon-btn sidebar-icon-settings" data-panel="settings" title="设置"><svg class="sidebar-icon-svg" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/></svg><span class="sidebar-icon-label">设置</span></button>
        </nav>
        <div class="unified-sidebar-panel">
        <div class="unified-sidebar-header">
          <div class="unified-sidebar-title" id="project-sidebar-title">项目</div>
          <div class="unified-sidebar-meta" id="project-sidebar-meta">未绑定项目</div>
        </div>
        <section class="sidebar-sessions" id="sidebar-sessions" aria-label="会话">
          <div class="sidebar-sessions-top">
            <button type="button" class="sidebar-new-chat" id="sidebar-new-chat"><span class="sidebar-new-chat-plus" aria-hidden="true">＋</span>新会话<kbd>Ctrl N</kbd></button>
            <label class="sidebar-session-search"><span class="sidebar-session-search-ico" aria-hidden="true"></span><input type="search" id="sidebar-session-search" placeholder="搜索会话…" autocomplete="off" spellcheck="false" /><kbd>Ctrl K</kbd></label>
            <div class="sidebar-session-filters" id="sidebar-session-filters" role="group" aria-label="会话筛选"></div>
          </div>
          <div class="sidebar-session-list" id="sidebar-session-list"></div>
        </section>
        <div class="sidebar-task-flow" id="sidebar-task-flow"></div>
        <div class="sidebar-footer" id="sidebar-footer">
          <div class="sidebar-change-banner hidden" id="sidebar-change-banner"></div>
          <div class="sidebar-project-row" id="sidebar-project-row"></div>
          <div class="sidebar-runtime-badges">
            <div id="sidebar-terminals"></div>
            <div class="sidebar-services" id="sidebar-services"></div>
          </div>
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
          <div class="unified-switch-mask hidden" id="unified-switch-mask" aria-live="polite">
            <div class="unified-switch-mask-card">正在加载项目会话…</div>
          </div>
          <main class="unified-chat" id="unified-chat"></main>
          <section class="unified-document hidden" id="unified-document" aria-label="文档阅读"></section>
          <section class="unified-projects hidden" id="unified-projects" aria-label="项目"></section>
        </div>
        <div class="unified-composer-dock" id="unified-composer-dock">
          <section class="unified-expand hidden" id="unified-expand"></section>
          <div class="unified-composer-meta" id="unified-composer-meta">
            <div class="unified-status" id="unified-status"></div>
            <div class="unified-token-bar hidden" id="unified-token-bar"></div>
          </div>
          <footer class="unified-composer" id="unified-composer">
            <button type="button" class="unified-btn unified-mode-pill" id="unified-mode-toggle" data-mode="agent" title="切换 动手(Agent) / 只聊(Ask) 模式">动手</button>
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
  const documentEl = root.querySelector<HTMLElement>("#unified-document")!;
  const projectsEl = root.querySelector<HTMLElement>("#unified-projects")!;
  const switchMaskEl = root.querySelector<HTMLElement>("#unified-switch-mask")!;
  const workbenchEmptyEl = root.querySelector<HTMLElement>("#workbench-empty")!;
  const emptyNewBtn = root.querySelector<HTMLButtonElement>("#empty-new-project")!;
  const emptyPickBtn = root.querySelector<HTMLButtonElement>("#empty-pick-project")!;
  const emptyFreeChatBtn = root.querySelector<HTMLButtonElement>("#empty-free-chat")!;
  const composerDock = root.querySelector<HTMLElement>("#unified-composer-dock")!;
  const composerMetaEl = root.querySelector<HTMLElement>("#unified-composer-meta")!;
  const statusEl = root.querySelector<HTMLElement>("#unified-status")!;
  const composer = root.querySelector<HTMLElement>("#unified-composer")!;
  const input = root.querySelector<HTMLTextAreaElement>("#unified-input")!;
  const modeToggleBtn = root.querySelector<HTMLButtonElement>("#unified-mode-toggle")!;
  const stopBtn = root.querySelector<HTMLButtonElement>("#unified-stop")!;
  const sendBtn = root.querySelector<HTMLButtonElement>("#unified-send")!;
  const confirmGlass = root.querySelector<HTMLElement>("#unified-confirm-glass")!;
  const workbenchDialogEl = root.querySelector<HTMLElement>("#workbench-dialog")!;
  const tokenBar = root.querySelector<HTMLElement>("#unified-token-bar")!;
  const threadArchiveBannerEl = root.querySelector<HTMLElement>("#thread-archive-banner")!;
  const threadReturnActiveBtn = root.querySelector<HTMLButtonElement>("#thread-return-active")!;

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
  // ---- sidebar: session list (当下), project row, runtime badges ----
  const sidebarSessionListEl = root.querySelector<HTMLElement>("#sidebar-session-list")!;
  const sidebarSessionFiltersEl = root.querySelector<HTMLElement>("#sidebar-session-filters")!;
  const sidebarSessionSearchEl = root.querySelector<HTMLInputElement>("#sidebar-session-search")!;
  const sidebarProjectRowEl = root.querySelector<HTMLElement>("#sidebar-project-row")!;
  let sidebarSessionQuery = "";
  let sidebarSessionFilter: SessionFilter = "all";
  let sidebarSessionsLoaded = false;
  let lastVerify: { passed: boolean; at: Date } | null = null;
  let projectGitBranch = "";
  let lastVerifyProjectId = "";
  let lastSessionListHtml = "";
  let lastSessionFiltersHtml = "";

  // ---- resize handle ----
  const resizeHandle = root.querySelector<HTMLElement>("#sidebar-resize-handle")!;
  let resizeDragging = false;

  function loadSidebarWidth(): number {
    try {
      const raw = localStorage.getItem("sidebar-width-v2");
      if (raw) {
        const n = parseInt(raw, 10);
        if (n >= 260 && n <= window.innerWidth * 0.5) return n;
      }
    } catch { /* ignore */ }
    return 338;
  }

  function saveSidebarWidth(w: number): void {
    try { localStorage.setItem("sidebar-width-v2", String(w)); } catch { /* ignore */ }
  }

  function applySidebarWidth(w: number): void {
    shellEl.style.setProperty("--sidebar-width", `${w}px`);
  }

  function clampSidebarWidth(w: number): number {
    return Math.max(260, Math.min(Math.round(w), Math.floor(window.innerWidth * 0.5)));
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
    return true;
  }

function timeOfDayPartnerPlaceholder(mode: "agent" | "ask" = "agent"): string {
  const hour = new Date().getHours();
  const idx = Math.floor(new Date().getMinutes() / 10) % 4;
  if (mode === "ask") {
    return [
      "当前是「只聊」模式：随便问我、找我复盘，或者天马行空聊会儿…",
      "只聊不改文件：把你的纠结或想法丢过来，我帮你一起参谋…",
      "纯聊天模式中：想查哪段逻辑、或者想聊点闲篇？直接说…",
      "放心聊，现在不会动你的磁盘文件，想问什么尽管开口…",
    ][idx];
  }
  if (hour >= 22 || hour < 5) {
    return [
      "夜深了，还想折腾点什么？直接丢给我，我替你兜底…",
      "深夜场在线中：想改代码、抓 Bug，还是随便跟我说说话？",
      "这么晚还在忙，把你想做的用一句话丢过来，剩下的交给我…",
      "熬夜搭档随时待命：直接说改哪儿，或者拖个文件/截图进来…",
    ][idx];
  }
  if (hour < 11) {
    return [
      "早安，今天我们先拿哪块硬骨头开刀？",
      "清晨脑子最清醒，把今天最想搞定的事直接告诉我…",
      "早呀，想巡检代码、跑测试，还是开个新活儿？",
      "墨镜擦亮了，说吧，今天第一件事让我干嘛？",
    ][idx];
  }
  if (hour < 18) {
    return [
      "说吧，这轮想让我改哪儿、查哪儿？（支持直接拖入或粘贴文件）",
      "有什么难缠的活儿直接甩过来，我帮你一口气搞定…",
      "在呢，想审改动、修 Bug，还是聊聊新点子？",
      "把需求或报错贴进来，我们俩联手把它拿下…",
    ][idx];
  }
  return [
    "晚上好，今天还想打磨点什么细节？直接交给我…",
    "晚间主场：不管是改代码、跑测试还是闲聊，随时奉陪…",
    "忙完一天了，有什么收尾的活儿丢给我来干吧…",
    "跟我说说你现在的想法，或者直接让我去看看你刚改的代码…",
  ][idx];
}

  function syncFreeChatFromSession(hasProject: boolean, sessionId: string): void {
    freeChatActive = !hasProject && Boolean(sessionId);
  }

  function updatePlaceholder(): void {
    if (isViewingArchivedThread(projectState)) {
      input.placeholder = "归档线只读回看；点顶栏「回到活线」继续工作";
      return;
    }
    input.placeholder = timeOfDayPartnerPlaceholder(currentTurnMode);
  }

  function syncArchivedViewUi(): void {
    const archived = isViewingArchivedThread(projectState);
    threadArchiveBannerEl.classList.toggle("hidden", !archived);
    updatePlaceholder();
    composerWire.syncSendEnabled();
  }

  function updateWorkbenchEmpty(): void {
    workbenchEmptyEl.hidden = true;
    chatEl.classList.remove("is-empty-gated");
    composer.classList.remove("is-empty-gated");
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
      if (isViewingArchivedThread(projectState)) return false;
      return true;
    },
    onChange: () => composerWire.syncSendEnabled(),
    onNotice: (text) => setStatus(text),
  });

  function currentDocSelection(): string {
    const sel = window.getSelection()?.toString() || "";
    if (sel.trim()) return sel;
    const editor = documentEl.querySelector<HTMLTextAreaElement>(".unified-document-editor");
    if (editor && editor.selectionStart !== editor.selectionEnd) {
      return editor.value.slice(editor.selectionStart, editor.selectionEnd);
    }
    return "";
  }

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
    messageOpts: () => {
      const path = projectState.currentDocPath || "";
      if (!path) return undefined;
      const selection = currentDocSelection();
      return {
        activeDoc: path,
        activeDocSelection: selection || undefined,
      };
    },
  });

  // ---- visual sync ----
  let lastSidebarWorking = false;
  let processJumpCursor = -1;
  let processJumpTurnIndex = -1;

  let currentTurnMode: "agent" | "ask" = "agent";

  function syncModeToggleUi(mode?: string): void {
    if (mode === "ask" || mode === "agent") {
      currentTurnMode = mode;
    }
    modeToggleBtn.dataset.mode = currentTurnMode;
    modeToggleBtn.textContent = currentTurnMode === "ask" ? "只聊" : "动手";
    modeToggleBtn.title = currentTurnMode === "ask"
      ? "当前：只聊 (Ask) — 只读探索与回答；点击切换为 动手 (Agent)"
      : "当前：动手 (Agent) — 允许写文件与执行命令；点击切换为 只聊 (Ask)";
    modeToggleBtn.disabled = chat.isWorking() || chat.model.confirmPending || isViewingArchivedThread(projectState);
  }

  modeToggleBtn.addEventListener("click", () => {
    if (chat.isWorking() || chat.model.confirmPending) return;
    const next = currentTurnMode === "agent" ? "ask" : "agent";
    syncModeToggleUi(next);
    updatePlaceholder();
    try {
      client.setTurnMode(next);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    }
  });

  function resetProcessJumpCursor(): void {
    processJumpCursor = -1;
    processJumpTurnIndex = -1;
  }

  function syncWorkingVisual(): void {
    const working = chat.isWorking();
    const workingChanged = lastSidebarWorking !== working;
    lastSidebarWorking = working;
    if (workingChanged) renderSidebarSessions();
    projectState.turnInProgress = working;
    projectEls.goalCard.classList.add("hidden");
    projectEls.goalCard.dataset.goalStatus = deriveProjectGoalViewModel(projectState).status;
    projectEls.goalCard.innerHTML = renderProjectGoalCard(projectState);
    shellEl.classList.toggle("is-working", working);
    statusEl.classList.toggle("is-idle", statusText === "就绪" && !working);
    syncComposerMetaVisibility();
    stopBtn.hidden = !(working || chat.model.confirmPending);
    stopBtn.disabled = chat.model.cancelRequested;
    syncModeToggleUi();
    setAgentBusy(working, perspective === "project" ? "project" : "grow");
    if (perspective === "project" && workingChanged) {
      renderSidebar();
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
      tokenBar.classList.add("hidden");
      syncComposerMetaVisibility();
      return;
    }
    const ratio = usage / limit;
    // Keep the room clean: only surface token warning when context is nearing capacity (≥80%).
    if (ratio < 0.80) {
      tokenBar.classList.add("hidden");
      syncComposerMetaVisibility();
      return;
    }
    tokenBar.classList.remove("hidden");
    let cls = "unified-token-bar";
    if (ratio >= 0.95) cls += " unified-token-red";
    else if (ratio >= 0.85) cls += " unified-token-yellow";
    const usageK = Math.round(usage / 1000);
    const limitK = Math.round(limit / 1000);
    tokenBar.className = cls;
    tokenBar.textContent = `记忆容量 ${Math.round(ratio * 100)}%（${usageK}k / ${limitK}k）`;
    tokenBar.title = "当前对话上下文占用较高，可随时新开会话";
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


  function sessionListInput(): SessionListInput {
    const sid = String((chat.model as { sessionId?: string }).sessionId || projectState.currentSessionId || "");
    return {
      sessions: sessionsDropdown,
      threads: projectState.threads,
      projectId: projectState.projectId,
      activeSessionId: projectState.activeSessionId,
      currentSessionId: sid,
      working: chat.isWorking(),
      query: sidebarSessionQuery,
      filter: sidebarSessionFilter,
      loaded: sidebarSessionsLoaded,
    };
  }

  function renderSidebarSessions(): void {
    const input = sessionListInput();
    if (input.filter === "project" && !input.projectId) sidebarSessionFilter = input.filter = "all";
    const filtersHtml = renderSessionFiltersHtml(input);
    if (filtersHtml !== lastSessionFiltersHtml) {
      lastSessionFiltersHtml = filtersHtml;
      sidebarSessionFiltersEl.innerHTML = filtersHtml;
    }
    const listHtml = renderSessionListHtml(input);
    if (listHtml !== lastSessionListHtml) {
      lastSessionListHtml = listHtml;
      const scroll = sidebarSessionListEl.scrollTop;
      sidebarSessionListEl.innerHTML = listHtml;
      sidebarSessionListEl.scrollTop = scroll;
    }
  }

  function formatClock(d: Date): string {
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  function renderSidebarProjectRow(): void {
    const pid = projectState.projectId;
    const routine = ambientRoutineStatus();
    const bond = loadCompanionBond();
    const days = Math.max(1, Math.ceil((Date.now() - bond.firstMetAt) / 86_400_000));
    const partner = currentPartnerName();
    if (!pid) {
      sidebarProjectRowEl.innerHTML = `<div class="sidebar-project-row-inner is-companion-dock">
        <img class="sidebar-companion-ava" src="${petAvatarSrc("idle", true)}" alt="" width="34" height="34" draggable="false" />
        <div class="sidebar-project-main">
          <div class="sidebar-companion-head">
            <span class="sidebar-project-name">${escapeHtml(partner)} · 相伴 ${days} 天</span>
            <button type="button" class="sidebar-companion-care-btn" data-sidebar-action="care-coffee" title="随手投喂她一杯冰美式">🧋 ${bond.coffeeCount || ""}</button>
          </div>
          <span class="sidebar-project-sub is-multiline">${escapeHtml(routine)}</span>
        </div>
      </div>`;
      return;
    }
    const parts: string[] = [];
    if (projectGitBranch) parts.push(`<span class="sidebar-project-branch">⎇ ${escapeHtml(projectGitBranch)}</span>`);
    if (lastVerify) {
      parts.push(`<span class="sidebar-project-verify ${lastVerify.passed ? "is-ok" : "is-fail"}">上次验证 ${lastVerify.passed ? "✓" : "✗"} ${formatClock(lastVerify.at)}</span>`);
    }
    if (!parts.length) parts.push(`<span>${escapeHtml(routine)}</span>`);
    sidebarProjectRowEl.innerHTML = `<button type="button" class="sidebar-project-row-inner is-companion-dock" data-sidebar-action="open-projects" title="点击切换项目">
      <img class="sidebar-companion-ava" src="${petAvatarSrc("idle", true)}" alt="" width="34" height="34" draggable="false" />
      <span class="sidebar-project-main"><span class="sidebar-project-name">${escapeHtml(pid)} · ${escapeHtml(partner)}盯盘中</span><span class="sidebar-project-sub is-multiline">${parts.join(" · ")}</span></span>
      <span class="sidebar-project-updown" aria-hidden="true"><i></i><i></i></span>
    </button>`;
  }

  function renderSidebar(): void {
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
    renderSidebarSessions();
    renderSidebarProjectRow();
  }

  function refreshSidebarSessions(): void {
    try {
      client.listSessions();
    } catch {
      /* offline: keep the last list */
    }
    refreshProjectThreads();
  }

  function openSidebarSession(sid: string, projectId: string): void {
    if (!sid || sid === sessionListInput().currentSessionId) return;
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再切换会话");
      return;
    }
    try {
      beginBoundSessionSwitch(sid, projectId, "打开会话");
      client.openSession(sid);
    } catch (err) {
      completeHydrationWait();
      setStatus(`打开失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function handleSidebarNewSession(): void {
    if (projectState.projectId) {
      void handleNewThread();
      return;
    }
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再开新对话");
      return;
    }
    if (freeChatActive && chat.model.blocks.length === 0) {
      input.focus();
      return;
    }
    try {
      clearChatForSwitch();
      client.sendCommand("新会话");
      setStatus("正在新建会话…");
      input.focus();
    } catch (err) {
      setStatus(`新建会话失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  sidebarSessionSearchEl.addEventListener("input", () => {
    sidebarSessionQuery = sidebarSessionSearchEl.value;
    renderSidebarSessions();
  });
  sidebarSessionSearchEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      sidebarSessionSearchEl.value = "";
      sidebarSessionQuery = "";
      renderSidebarSessions();
    }
  });
  sidebarSessionFiltersEl.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-session-filter]");
    const value = btn?.dataset.sessionFilter;
    if (value !== "all" && value !== "chat" && value !== "project") return;
    sidebarSessionFilter = value;
    renderSidebarSessions();
  });
  sidebarSessionListEl.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const thread = target.closest<HTMLElement>("[data-thread-id]");
    if (thread?.dataset.threadId) {
      projectCallbacks.onOpenThread?.(thread.dataset.threadId);
      return;
    }
    const row = target.closest<HTMLElement>("[data-open-session]");
    if (row?.dataset.openSession) {
      openSidebarSession(row.dataset.openSession, row.dataset.projectId || "");
    }
  });
  root.querySelector<HTMLButtonElement>("#sidebar-new-chat")!.addEventListener("click", () => handleSidebarNewSession());
  sidebarProjectRowEl.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLElement>("[data-sidebar-action]");
    const action = btn?.dataset.sidebarAction;
    if (action === "open-projects") selectRailTab("projects");
    else if (action === "new-project") void handleNewProject();
    else if (action === "care-coffee") {
      ev.stopPropagation();
      const { reply } = recordCompanionCare("coffee");
      syncCompanionToBackend();
      renderedPrints = [];
      renderedSegmentPrints = [];
      renderChat();
      renderSidebar();
      setStatus(reply);
    }
  });
  document.addEventListener("keydown", (ev) => {
    if (!(ev.ctrlKey || ev.metaKey) || ev.altKey || ev.shiftKey) return;
    const key = ev.key.toLowerCase();
    if (key === "k") {
      ev.preventDefault();
      if (projectState.railTab !== "now") showNowChat();
      sidebarSessionSearchEl.focus();
      sidebarSessionSearchEl.select();
    } else if (key === "n") {
      ev.preventDefault();
      handleSidebarNewSession();
    }
  });

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

  function startHydrationWait(sessionId: string, label: string): void {
    hydrationSessionId = sessionId;
    pendingSwitchBatch = true;
    projectState.switchInProgress = true;
    setStatus(`${label}…`);
    syncSessionSwitchMask();
    renderSidebar();
  }

  function completeHydrationWait(sessionId?: string): void {
    if (sessionId && hydrationSessionId && sessionId !== hydrationSessionId) return;
    const wasProjectSwitch = projectState.switchInProgress;
    hydrationSessionId = "";
    pendingSwitchBatch = false;
    projectState.switchInProgress = false;
    projectState.pendingPickerId = "";
    projectState.switchOverlay = null;
    syncSessionSwitchMask();
    debouncedListProjects();
    debouncedListSessions();
    renderSidebar();
    refreshTopbar();
    if (wasProjectSwitch && projectState.projectId) {
      refreshServices();
      refreshTerminals();
    }
  }

  function syncSessionSwitchMask(): void {
    const switching = Boolean(
      projectState.switchInProgress
      || projectState.switchOverlay
      || hydrationSessionId,
    );
    switchMaskEl.classList.toggle("hidden", !switching);
    shellEl.classList.toggle("is-session-switching", switching);
    const target = (
      projectState.pendingPickerId
      || projectState.switchOverlay?.projectId
      || projectState.projectId
      || "会话"
    ).trim();
    const card = switchMaskEl.querySelector(".unified-switch-mask-card");
    if (card) {
      card.textContent = projectState.switchOverlay
        ? `切换到 ${target} 需要确认`
        : `正在加载 ${target}…`;
    }
  }

  function clearChatForSwitch(): void {
    chat.model.blocks = [];
    chat.model.sessionId = "";
    chat.model.turnCounter = 0;
    chat.model.currentTurnKey = "";
    chat.model.assistantBuffer = "";
    chat.model.confirmPending = false;
    chat.model.confirmSubmitting = false;
    chat.model.cancelRequested = false;
    chat.model.confirmOverlay = null;
    chat.model.executionRunId = "";
    chat.model.executionState = "idle";
    chat.model.executionSequence = 0;
    chat.model.executionFinishReason = null;
    renderedPrints = [];
    renderedSegmentPrints = [];
    renderChat();
  }

  function beginBoundSessionSwitch(sessionId: string, projectId: string, label: string): void {
    const sid = sessionId.trim();
    const pid = projectId.trim();
    if (pid && isCrossProjectSession(projectState.projectId, pid)) {
      beginAtomicProjectSwitch(projectState, pid);
      clearChatForSwitch();
      setMainFocus("chat");
      refreshTopbar();
    } else if (sid && sid !== chat.model.sessionId) {
      clearChatForSwitch();
    }
    startHydrationWait(sid, label);
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
    renderSidebar();
    try {
      client.listProjectThreads(projectState.projectId);
    } catch (err) {
      projectState.threadsLoading = false;
      renderSidebar();
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
      client.sendCommand(`项目 新建 ${id} --light`);
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
          <span class="unified-expand-item-meta">${escapeHtml(sessionListMeta(meta, s.session_id))}</span>
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
        const item = sessionsDropdown.find((s) => s.session_id === sid);
        try {
          beginBoundSessionSwitch(sid, item?.project_id || "", "打开会话");
          client.openSession(sid);
          sessionsOpen = false;
          expandEl.classList.add("hidden");
          expandEl.innerHTML = "";
        } catch (err) {
          completeHydrationWait();
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
      if (!target || (target === projectState.projectId && !projectState.switchOverlay)) return;
      if (chat.isWorking()) {
        setStatus("助手执行中，请稍后再切换项目");
        return;
      }
      selectRailTab("now");
      beginAtomicProjectSwitch(projectState, target);
      clearChatForSwitch();
      pendingSwitchBatch = true;
      setMainFocus("chat");
      syncSessionSwitchMask();
      refreshTopbar();
      renderSidebar();
      try {
        client.switchProject(target, { confirm: true });
      } catch (err) {
        projectState.switchInProgress = false;
        projectState.pendingPickerId = "";
        pendingSwitchBatch = false;
        syncSessionSwitchMask();
        setStatus(`切换失败：${err instanceof Error ? err.message : String(err)}`);
        renderSidebar();
        refreshTopbar();
      }
    },
    onProjectSwitchConfirm: () => {
      if (!projectState.switchOverlay) return;
      const overlay = projectState.switchOverlay;
      const target = overlay.projectId;
      const requestId = overlay.requestId;
      beginAtomicProjectSwitch(projectState, target);
      clearChatForSwitch();
      pendingSwitchBatch = true;
      setMainFocus("chat");
      syncSessionSwitchMask();
      refreshTopbar();
      renderSidebar();
      try {
        client.switchProject(target, {
          confirm: true,
          requestId,
        });
      } catch (err) {
        projectState.switchInProgress = false;
        pendingSwitchBatch = false;
        syncSessionSwitchMask();
        setStatus(`切换失败：${err instanceof Error ? err.message : String(err)}`);
        renderSidebar();
        refreshTopbar();
      }
    },
    onProjectSwitchCancel: () => {
      projectState.switchOverlay = null;
      projectState.switchInProgress = false;
      projectState.pendingPickerId = "";
      pendingSwitchBatch = false;
      hydrationSessionId = "";
      syncSessionSwitchMask();
      renderSidebar();
      refreshTopbar();
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
      projectState.railTab = "now";
      projectState.overlayPanel = null;
      setMainFocus("chat");
      try {
        beginBoundSessionSwitch(sid, projectState.projectId, "打开会话线");
        client.openSession(sid);
      } catch (err) {
        completeHydrationWait();
        setStatus(`打开会话线失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onReturnActiveThread: () => {
      const active = projectState.activeSessionId.trim();
      if (!active) return;
      projectCallbacks.onOpenThread(active);
    },
    onOpenProjects: () => {
      selectRailTab("projects");
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
    onExternalImportScan: (path: string, projectId: string) => {
      try {
        client.scanExternalProject(path, projectId || undefined);
        setStatus("正在扫描外部项目…");
      } catch (err) {
        setStatus(`扫描外部项目失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onExternalImportApply: (path: string, projectId: string) => {
      try {
        client.applyExternalProject(path, projectId, true);
        setStatus("正在接入外部项目…");
      } catch (err) {
        setStatus(`接入外部项目失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onExternalImportCancel: () => {
      projectState.externalImportScan = null;
      renderSidebar();
    },
  };

  threadReturnActiveBtn.addEventListener("click", () => {
    projectCallbacks.onReturnActiveThread();
  });

  let pendingDocAction: "rename" | "delete" | "write" | "create" | null = null;
  let docLeaveNext: (() => void) | null = null;
  let docLeavePrompt = false;
  let destroyDocMenu: (() => void) | null = null;

  function isDocDirty(): boolean {
    return projectState.docView === "edit" && projectState.docDirty;
  }

  function resetDocEditState(nextPath = "", content = ""): void {
    projectState.currentDocPath = nextPath;
    projectState.currentDocContent = content;
    projectState.docView = "preview";
    projectState.docEditDraft = content;
    projectState.docDirty = false;
    projectState.docSaving = false;
    docLeaveNext = null;
    docLeavePrompt = false;
  }

  function captureDocDraftFromEditor(): void {
    const editor = documentEl.querySelector<HTMLTextAreaElement>(".unified-document-editor");
    if (editor) projectState.docEditDraft = editor.value;
    projectState.docDirty = projectState.docEditDraft !== projectState.currentDocContent;
  }

  function syncDocEditChrome(): void {
    const saveBtn = documentEl.querySelector<HTMLButtonElement>("[data-action='document-save']");
    if (saveBtn) {
      saveBtn.disabled = !projectState.docDirty || projectState.docSaving;
      saveBtn.textContent = projectState.docSaving ? "保存中…" : "保存";
    }
    const discardBtn = documentEl.querySelector<HTMLButtonElement>("[data-action='document-discard']");
    if (discardBtn) discardBtn.disabled = !projectState.docDirty || projectState.docSaving;
    documentEl.querySelector(".unified-document-inner")?.classList.toggle("is-dirty", projectState.docDirty);
    const kicker = documentEl.querySelector(".unified-document-kicker");
    if (kicker) kicker.textContent = projectState.docDirty ? "项目文档 · 未保存" : "项目文档";
  }

  function requestLeaveDocument(next: () => void): boolean {
    if (!isDocDirty()) return true;
    captureDocDraftFromEditor();
    docLeaveNext = next;
    docLeavePrompt = true;
    if (projectState.mainFocus !== "document") {
      projectState.mainFocus = "document";
      projectState.railTab = "docs";
      shellEl.dataset.rail = "docs";
      syncMainFocusView();
    } else {
      renderDocumentPane();
    }
    setStatus("有未保存的更改");
    return false;
  }

  function clearDocLeavePrompt(): void {
    docLeavePrompt = false;
    docLeaveNext = null;
  }

  function finishDocLeave(): void {
    const next = docLeaveNext;
    clearDocLeavePrompt();
    projectState.docDirty = false;
    projectState.docSaving = false;
    next?.();
  }

  function saveCurrentDoc(after?: () => void, force = false): void {
    const path = projectState.currentDocPath;
    if (!path) return;
    captureDocDraftFromEditor();
    pendingDocAction = "write";
    projectState.docSaving = true;
    projectState.docConflictPrompt = false;
    if (after) docLeaveNext = after;
    syncDocEditChrome();
    try {
      client.writeDoc(path, projectState.docEditDraft, {
        baseMtime: projectState.currentDocMtime || undefined,
        baseHash: projectState.currentDocHash || undefined,
        force,
      });
      setStatus("正在保存文档…");
    } catch (err) {
      projectState.docSaving = false;
      pendingDocAction = null;
      syncDocEditChrome();
      setStatus(`保存失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function refreshDocsSidebar(): void {
    renderSidebar();
  }

  function startRenameDoc(path: string): void {
    projectState.deleteConfirmPath = "";
    projectState.renamingDocPath = path;
    projectState.renameDraft = renameDraftForDoc(path);
    refreshDocsSidebar();
    const input = projectEls.overlayBody.querySelector<HTMLInputElement>("#overlay-doc-rename-input");
    input?.focus();
    input?.select();
  }

  function cancelRenameDoc(): void {
    projectState.renamingDocPath = "";
    projectState.renameDraft = "";
    refreshDocsSidebar();
  }

  function commitRenameDoc(): void {
    const path = projectState.renamingDocPath;
    const title = projectState.renameDraft.trim();
    if (!path) return;
    if (!title || title === docStem(path)) {
      cancelRenameDoc();
      return;
    }
    pendingDocAction = "rename";
    try {
      client.renameDoc(path, { title });
      setStatus("正在重命名…");
    } catch (err) {
      pendingDocAction = null;
      setStatus(`重命名失败：${err instanceof Error ? err.message : String(err)}`);
    }
    projectState.renamingDocPath = "";
    projectState.renameDraft = "";
    refreshDocsSidebar();
  }

  function askDeleteDoc(path: string): void {
    projectState.renamingDocPath = "";
    projectState.renameDraft = "";
    projectState.deleteConfirmPath = path;
    refreshDocsSidebar();
  }

  function confirmDeleteDoc(): void {
    const path = projectState.deleteConfirmPath;
    if (!path) return;
    pendingDocAction = "delete";
    try {
      client.deleteDoc(path);
      setStatus("正在删除文档…");
    } catch (err) {
      pendingDocAction = null;
      setStatus(`删除失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function showDocCatalogMenu(path: string, x: number, y: number): void {
    destroyDocMenu?.();
    destroyDocMenu = mountPopupMenu({
      x,
      y,
      items: docCatalogMenuItems(),
      onSelect: (id) => {
        destroyDocMenu = null;
        if (id === "rename") startRenameDoc(path);
        else if (id === "delete") askDeleteDoc(path);
      },
    });
  }

  function setMainFocus(focus: MainFocus): void {
    if (
      projectState.mainFocus === "document" &&
      focus !== "document" &&
      !requestLeaveDocument(() => setMainFocus(focus))
    ) {
      return;
    }
    if (focus !== "document") {
      projectState.docView = "preview";
      projectState.docDirty = false;
      projectState.docSaving = false;
      docLeavePrompt = false;
    }
    projectState.mainFocus = focus;
    if (focus !== "chat") {
      clearExpandedSurface();
    }
    syncMainFocusView();
  }

  function selectRailTab(tab: RailTab): void {
    if (
      projectState.railTab === "docs" &&
      tab !== "docs" &&
      !requestLeaveDocument(() => selectRailTab(tab))
    ) {
      return;
    }
    projectState.railTab = tab;
    projectState.overlayPanel = null;
    shellEl.dataset.rail = tab;
    if (tab !== "projects") {
      projectState.switchConfirmTarget = null;
    }
    if (tab === "projects") {
      projectState.projectSearchQuery = "";
      try { client.listProjects(); } catch { /* ignore */ }
    }
    if (tab === "docs") {
      openDocumentReader();
      return;
    }
    if (tab === "now") {
      refreshSidebarSessions();
    }
    setMainFocus(mainFocusForRail(tab));
  }

  function showNowChat(): void {
    if (
      projectState.mainFocus === "document" &&
      !requestLeaveDocument(() => showNowChat())
    ) {
      return;
    }
    projectState.railTab = "now";
    projectState.overlayPanel = null;
    shellEl.dataset.rail = "now";
    setMainFocus("chat");
  }

  function syncMainFocusView(): void {
    const focus = projectState.mainFocus;
    const splitDocs = focus === "document" && projectState.docsChatSplit;
    const chatFocus = focus === "chat" || splitDocs;
    shellEl.dataset.mainFocus = splitDocs ? "docs-split" : focus;
    shellEl.dataset.rail = projectState.railTab || "now";
    shellEl.classList.toggle("is-docs-split", splitDocs);
    chatEl.classList.toggle("hidden", !(focus === "chat" || splitDocs));
    documentEl.classList.toggle("hidden", focus !== "document");
    projectsEl.classList.toggle("hidden", focus !== "projects");
    chatEl.hidden = !(focus === "chat" || splitDocs);
    documentEl.hidden = focus !== "document";
    projectsEl.hidden = focus !== "projects";
    composerDock.hidden = false;
    composer.hidden = false;
    statusEl.hidden = false;
    tokenBar.hidden = !chatFocus;
    syncComposerMetaVisibility();
    if (focus === "document") {
      renderDocumentPane();
    } else if (focus === "projects") {
      renderProjectsPane();
    }
    updateWorkbenchEmpty();
    renderSidebar();
  }


  function renderProjectsPane(): void {
    projectsEl.innerHTML = renderProjectsStage(projectState);
  }

  function renderDocumentPane(): void {
    if (!projectState.projectId) {
      documentEl.innerHTML = renderRailStageEmpty({
        title: "绑定项目后查看文档",
        copy: "打开项目后，这里会显示文档阅读区。",
        action: "open-projects",
        actionLabel: "打开项目",
      });
      return;
    }
    const path = projectState.currentDocPath;
    const content = projectState.currentDocContent;
    const previewSource = projectState.docView === "edit"
      ? (projectState.docEditDraft || content)
      : content;
    const { html, outline } = renderDocumentReaderHtml({
      docs: projectState.projectDocs,
      currentPath: path,
      currentContent: content,
      renderedHtml: previewSource ? renderMarkdown(previewSource) : "",
      newDocName: projectState.newDocName,
      newDocFolder: projectState.newDocFolder,
      includeCatalog: false,
      includeBack: false,
      view: projectState.docView,
      draft: projectState.docEditDraft,
      dirty: projectState.docDirty,
      saving: projectState.docSaving,
      leavePrompt: docLeavePrompt,
      conflictPrompt: projectState.docConflictPrompt,
      splitEditPreview: true,
      docsChatSplit: projectState.docsChatSplit,
      recentlyChanged: projectState.recentlyChangedDocs,
      placeholderCount: projectState.docPlaceholderCount,
      searchQuery: projectState.docSearchQuery,
    });
    documentEl.innerHTML = html;
    const reader = documentEl.querySelector(".unified-document-content");
    let resolved = outline;
    if (reader && projectState.docView === "preview") {
      resolved = finalizeDocumentOutline(reader, content);
      const outlineBody = documentEl.querySelector(".doc-outline-body");
      if (outlineBody) outlineBody.innerHTML = renderDocOutlineHtml(resolved);
      const resolvedTitle = path ? displayDocTitle(path, path, content, resolved) : "";
      const heading = documentEl.querySelector(".unified-document-heading h1");
      if (heading && resolvedTitle) heading.textContent = resolvedTitle;
      const currentItem = documentEl.querySelector<HTMLElement>(".doc-catalog-item.is-current .doc-catalog-item-title");
      if (currentItem && resolvedTitle) currentItem.textContent = resolvedTitle;
    }
    if (projectState.docView === "preview") void hydrateMermaid(documentEl);
    if (projectState.docView === "edit") {
      documentEl.querySelector<HTMLTextAreaElement>(".unified-document-editor")?.focus();
    }
    if (!docLeavePrompt) {
      const readerPane = documentEl.querySelector<HTMLElement>(".unified-document-reader");
      if (readerPane) readerPane.scrollTop = 0;
      else documentEl.scrollTop = 0;
    }
  }

  function createProjectDocFromTitle(): void {
    const name = normalizeNewDocPath(projectState.newDocName);
    if (!name) return;
    const folder = (projectState.newDocFolder || "").trim().replace(/^\/+|\/+$/g, "");
    const path = folder ? `${folder}/${name}` : name;
    pendingDocAction = "create";
    try { client.createDoc(path); } catch { pendingDocAction = null; }
    projectState.newDocName = "";
    projectState.newDocFolder = "";
  }

  function requestDocList(): void {
    if (!canRequestProjectDocs(projectState.projectId)) return;
    try { client.listDocs(); } catch { /* ignore */ }
  }

  function requestDocRead(path: string): void {
    if (!path || !canRequestProjectDocs(projectState.projectId)) return;
    try { client.readDoc(path); } catch { /* ignore */ }
  }

  function normalizeAgentDocPath(raw: string): string {
    let rel = (raw || "").replace(/\\/g, "/").trim();
    if (!rel) return "";
    const pid = projectState.projectId;
    if (pid) {
      const marker = `workspace/${pid}/`;
      const idx = rel.indexOf(marker);
      if (idx >= 0) rel = rel.slice(idx + marker.length);
      else if (rel.startsWith(`workspace/${pid}`)) rel = rel.slice(`workspace/${pid}`.length).replace(/^\/+/, "");
    }
    if (rel.startsWith("workspace/") && pid) {
      const parts = rel.split("/");
      if (parts.length >= 3 && parts[1] === pid) rel = parts.slice(2).join("/");
    }
    return rel;
  }

  function openDocument(path: string): void {
    path = normalizeAgentDocPath(path);
    if (!path) return;
    const samePath = projectState.currentDocPath === path;
    if (!samePath && isDocDirty()) {
      requestLeaveDocument(() => openDocument(path));
      return;
    }
    projectState.currentDocPath = path;
    projectState.railTab = "docs";
    projectState.overlayPanel = null;
    shellEl.dataset.rail = "docs";
    if (!samePath) {
      projectState.currentDocContent = "";
      projectState.docEditDraft = "";
      projectState.docView = "preview";
      projectState.docDirty = false;
      projectState.docSaving = false;
      projectState.docConflictPrompt = false;
      projectState.currentDocMtime = 0;
      projectState.currentDocHash = "";
      clearDocLeavePrompt();
    }
    setMainFocus("document");
    requestDocList();
    // Always re-read so Agent edits / external changes show up.
    if (!isDocDirty()) requestDocRead(path);
  }

  function openDocumentReader(path?: string): void {
    projectState.railTab = "docs";
    projectState.overlayPanel = null;
    shellEl.dataset.rail = "docs";
    const target = path
      || projectState.currentDocPath
      || sortProjectDocs(projectState.projectDocs)[0]?.path
      || "";
    if (!canRequestProjectDocs(projectState.projectId)) {
      setMainFocus("document");
      setStatus("请先打开项目");
      return;
    }
    if (target) {
      openDocument(target);
      return;
    }
    setMainFocus("document");
    requestDocList();
  }

  function clearExpandedSurface(): void {
    sessionsOpen = false;
    expandEl.classList.add("hidden");
    expandEl.innerHTML = "";
  }

  function showBlockerDetails(): void {
    showNowChat();
    clearExpandedSurface();
    expandEl.innerHTML = renderProjectBlockerDetails(projectState);
    expandEl.classList.remove("hidden");
    expandEl.querySelector<HTMLButtonElement>('[data-action="blocker-details-close"]')?.addEventListener("click", () => {
      clearExpandedSurface();
    });
  }

  function startDirectImplementFromUi(): void {
    try {
      setStatus("正在直接实现…");
      showNowChat();
      client.startDirectImplement();
    } catch (err) {
      setStatus(`直接实现失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function runProjectVerifyFromUi(): void {
    try {
      setStatus("正在跑验收…");
      showNowChat();
      client.sendCommand("项目 验收");
    } catch (err) {
      setStatus(`验收失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function handleHeaderNextStepAction(action: string): void {
    switch (action) {
      case "direct-implement":
        startDirectImplementFromUi();
        return;
      case "run-verify":
        runProjectVerifyFromUi();
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
      default:
        return;
    }
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
    if (projectState.mainFocus !== "chat" || projectState.railTab !== "now") {
      showNowChat();
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
    showNowChat();
    if (hasProjectBlocker(projectState)) {
      showBlockerDetails();
    }
    const blocks = chat.model.blocks;
    for (let i = blocks.length - 1; i >= 0; i--) {
      const block = blocks[i];
      if (block.kind !== "review-subagent") continue;
      requestAnimationFrame(() => {
        const el = chatEl.querySelector<HTMLElement>(
          `.unified-turn-card[data-turn-index="${block.turnIndex}"] .unified-review-callout`,
        );
        el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
      return;
    }
    scrollChatToBottom();
  }

  function handleRailStageAction(ev: Event): void {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    if (btn.dataset.action === "open-projects") {
      selectRailTab("projects");
      return;
    }
    if (btn.dataset.action === "new-project") {
      projectCallbacks.onNewProject();
      return;
    }
    if (btn.dataset.action === "open-now") {
      selectRailTab("now");
    }
  }
  projectsEl.addEventListener("click", handleRailStageAction);
  documentEl.addEventListener("click", handleRailStageAction);

  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if (projectState.railTab === "now" && projectState.mainFocus === "chat") return;
    if (document.activeElement === input) return;
    ev.preventDefault();
    selectRailTab("now");
  });

  function refreshServices(): void {
    if (perspective !== "project") return;
    projectState.servicesLoading = true;
    projectState.servicesError = "";
    renderSidebar();
    try {
      client.listServices();
    } catch (err) {
      projectState.servicesLoading = false;
      projectState.servicesError = err instanceof Error ? err.message : String(err);
      renderSidebar();
    }
  }

  function refreshTerminals(): void {
    if (projectState.terminalsLoading) return;
    const requestSerial = ++terminalRequestSerial;
    const requestId = `terminal-${Date.now()}-${requestSerial}`;
    terminalRequestId = requestId;
    projectState.terminalsLoading = true;
    projectState.terminalsError = "";
    renderSidebar();
    try {
      client.listTerminals(requestId);
      if (terminalRequestTimeoutId !== null) window.clearTimeout(terminalRequestTimeoutId);
      terminalRequestTimeoutId = window.setTimeout(() => {
        if (requestSerial !== terminalRequestSerial || !projectState.terminalsLoading) return;
        projectState.terminalsLoading = false;
        projectState.terminalsError = "读取终端状态超时";
        renderSidebar();
      }, 8000);
    } catch (err) {
      projectState.terminalsLoading = false;
      projectState.terminalsError = err instanceof Error ? err.message : String(err);
      renderSidebar();
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
    renderSidebar();
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
        renderSidebar();
      }, 8000);
    } catch (err) {
      projectState.terminalOutputLoading = false;
      projectState.terminalOutputError = err instanceof Error ? err.message : String(err);
      renderSidebar();
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
    renderSidebar();
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
    renderSidebar();
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

  function renderChatSegment(segment: ChatRenderSegment, isLatestSegment = true): string {
    if (segment.kind === "orphan") {
      return renderBlocksFlat(segment.blocks);
    }
    const live = segment.group.turnIndex === chat.currentTurnIndex() && chat.isWorking();
    const processBlocks = segment.group.blocks.filter(
      (block): block is Extract<ChatBlock, { kind: "process" }> =>
        block.kind === "process" && !isProcessBlockEmpty(block),
    );
    const prevMeta = activeProcessMeta;
    activeProcessMeta = processBlocks.length > 1
      ? new Map(processBlocks.map((block, index) => [block.turnKey, { index: index + 1, total: processBlocks.length }]))
      : null;
    // 过程 pill always sits after the reply it produced — never depends on collapsed state.
    const settled = !live && !isLatestSegment;
    const inner = renderTurnInnerWithPet(orderTurnBlocksForDisplay(segment.group.blocks), live, settled);
    activeProcessMeta = prevMeta;
    return renderTurnCardShell(segment.group, inner, { live });
  }

  /** Pet-as-assistant: avatar column to the left of everything the assistant produced in a turn. */
  function summarizePetTurn(blocks: ChatBlock[], live: boolean): PetTurnSummary {
    const procs = blocks.filter(
      (block): block is Extract<ChatBlock, { kind: "process" }> => block.kind === "process",
    );
    const tools = procs.flatMap((block) => getProcessTools(block));
    const runningTools = tools.filter((tool) => tool.status === "running");
    const lastRunning = runningTools[runningTools.length - 1];
    const runningCanonical = lastRunning
      ? resolveEffectiveToolName(lastRunning.tool, lastRunning.summary)
      : undefined;
    return {
      live,
      toolRunning: runningTools.length > 0,
      runningToolName: runningCanonical,
      thinkingLive: procs.some((block) => isProcessThinkingLive(block, chat.model.currentTurnKey)),
      replyStreaming: blocks.some((block) => block.kind === "assistant-streaming" && block.text.trim() !== ""),
      hasReply: blocks.some((block) => block.kind === "assistant" && block.text.trim() !== ""),
      toolFailed: Boolean(lastFailedTool(tools)),
      hasNotice: blocks.some((block) => block.kind === "notice" && !adoptPathFromNotice(block.text)),
      confirmPending: blocks.some((block) => block.kind === "confirm" && !block.resolved) || chat.model.confirmPending,
    };
  }

  function renderTurnInnerWithPet(blocks: ChatBlock[], live: boolean, settled = false): string {
    if (blocks[0]?.kind !== "user") return renderBlocksFlat(blocks);
    const rest = blocks.slice(1);
    if (!rest.length && !live) return renderBlocksFlat(blocks);
    const state = petTurnState(summarizePetTurn(rest, live));
    const content = rest.length
      ? renderBlocksFlat(rest)
      : '<div class="unified-pet-waiting" aria-hidden="true"><span></span><span></span><span></span></div>';
    return `${renderBlocksFlat(blocks.slice(0, 1))}<div class="unified-pet-row${settled ? " is-settled" : ""}" data-pet-state="${state}">${renderPetAvatarHtml(state, settled)}<div class="unified-pet-content">${renderPetNameHtml(state)}${content}</div></div>`;
  }

  /** Only the live turn (or the latest turn for 2.5s after completion) keeps animating; older turns settle on static frame. */
  function settlePetAvatars(): void {
    const rows = chatEl.querySelectorAll<HTMLElement>(".unified-pet-row");
    const working = chat.isWorking();
    rows.forEach((row, index) => {
      const latest = index === rows.length - 1;
      const shouldAnimate = latest && working;
      const img = row.querySelector<HTMLImageElement>(".unified-pet-avatar-img");
      const source = row.querySelector<HTMLSourceElement>(".unified-pet-avatar source");
      if (!img || !source) return;
      const still = source.getAttribute("srcset") ?? "";
      const animated = still.replace(/-still\.webp$/, ".webp");
      const want = shouldAnimate ? animated : still;
      if (img.getAttribute("src") !== want) img.setAttribute("src", want);
      row.classList.toggle("is-settled", !shouldAnimate);
    });
  }

  touchCompanionVisit();

  function totalChatMessagesCount(): number {
    const seen = new Set<string>();
    let sum = 0;
    for (const s of sessionsDropdown) {
      if (!s.session_id || seen.has(s.session_id)) continue;
      seen.add(s.session_id);
      if (typeof s.message_count === "number" && Number.isFinite(s.message_count) && s.message_count > 0) {
        sum += Math.floor(s.message_count);
      }
    }
    return sum;
  }

  function renderBlocksGrouped(blocks: ChatBlock[]): string {
    if (!blocks.length && !chat.isWorking()) {
      return renderIntimatePresenceStageHtml(escapeHtml(projectState.projectId || ""), totalChatMessagesCount());
    }
    if (!useTurnCardLayout()) {
      return renderBlocksFlat(blocks);
    }
    const segs = segmentChatBlocks(blocks);
    return segs.map((seg, idx) => renderChatSegment(seg, idx === segs.length - 1)).join("");
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
      // No steps, no thinking, not waiting → no empty 过程 pill.
      if (isProcessBlockEmpty(block)) return "";
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
          <button type="button" class="unified-process-pill" data-process-toggle="${block.turnKey}" aria-expanded="${block.collapsed ? "false" : "true"}" aria-controls="unified-process-body-${block.turnKey}">
            ${showPillDot ? '<span class="unified-process-pill-dot" aria-hidden="true"></span>' : ""}
            <span class="unified-process-pill-text">${escapeHtml(pill)} ${chevron}</span>
          </button>
          <div class="unified-process-body" id="unified-process-body-${block.turnKey}">
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
      // Pending + in-flight confirms: tool row in 过程 is the live source of truth (UX-030).
      if (isTerminal || inProgress) return "";
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
        if (!turnKey) return;
        processToggleAnchor = {
          turnKey,
          top: btn.getBoundingClientRect().top,
          focus: document.activeElement === btn,
        };
        try {
          chat.toggleProcessCollapsed(turnKey);
        } finally {
          // toggle → notify → render runs synchronously; drop a stale anchor if nothing rendered.
          processToggleAnchor = null;
        }
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

  /** Render-relevant turn state that is not part of the block prints (live flag, pet state, live key). */
  function segmentStatePrint(segment: ChatRenderSegment): string {
    if (segment.kind !== "turn-card") return "";
    const blocks = segment.group.blocks;
    const live = segment.group.turnIndex === chat.currentTurnIndex() && chat.isWorking();
    const pet = blocks[0]?.kind === "user" ? petTurnState(summarizePetTurn(blocks.slice(1), live)) : "";
    const hasLiveKey = blocks.some(
      (block) => block.kind === "process" && block.turnKey === chat.model.currentTurnKey,
    );
    return `#L${live ? 1 : 0}:${pet}:${hasLiveKey ? 1 : 0}`;
  }

  /** Keep the clicked 过程 pill at the same viewport offset (no scroll jump) and keep keyboard focus. */
  function restoreProcessToggleAnchor(anchor: { turnKey: string; top: number; focus: boolean }): void {
    const btn = chatEl.querySelector<HTMLButtonElement>(
      `[data-process-toggle="${CSS.escape(anchor.turnKey)}"]`,
    );
    if (!btn) return;
    const delta = btn.getBoundingClientRect().top - anchor.top;
    if (Math.abs(delta) > 0.5) chatEl.scrollTop += delta;
    if (anchor.focus && document.activeElement !== btn) btn.focus({ preventScroll: true });
  }

  function doRender(): void {
    markdownRenderEager = true;
    const curPrints = chat.model.blocks.map(blockPrint);

    if (useTurnCardLayout()) {
      const segments = segmentChatBlocks(chat.model.blocks);
      const curSegPrints = segments.map((seg) => segmentPrint(seg, blockPrint) + segmentStatePrint(seg));
      const anchor = processToggleAnchor;
      processToggleAnchor = null;
      const finish = () => {
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        if (anchor) restoreProcessToggleAnchor(anchor);
        else scrollToBottomIfNear();
        renderedSegmentPrints = curSegPrints;
        renderedPrints = curPrints;
      };

      if (
        renderedSegmentPrints.length > 0 &&
        curSegPrints.length === renderedSegmentPrints.length &&
        chatEl.children.length === curSegPrints.length
      ) {
        // Same segments: replace only the cards that changed (none → keep DOM, focus and scroll).
        const changed: number[] = [];
        for (let i = 0; i < curSegPrints.length; i++) {
          if (curSegPrints[i] !== renderedSegmentPrints[i]) changed.push(i);
        }
        if (!changed.length) {
          if (anchor) restoreProcessToggleAnchor(anchor);
          return;
        }
        const children = Array.from(chatEl.children);
        for (const i of changed) {
          children[i].outerHTML = renderChatSegment(segments[i]);
        }
        finish();
        return;
      }

      if (
        renderedSegmentPrints.length > 0 &&
        curSegPrints.length > renderedSegmentPrints.length
      ) {
        const prefixOk = renderedSegmentPrints.every((p, i) => p === curSegPrints[i]);
        if (prefixOk && chatEl.children.length === renderedSegmentPrints.length) {
          const newSegs = segments.slice(renderedSegmentPrints.length);
          chatEl.insertAdjacentHTML("beforeend", newSegs.map(renderChatSegment).join(""));
          finish();
          return;
        }
      }

      resetLazyMarkdownObserver();
      markdownRenderEager = false;
      chatEl.innerHTML = segments.map(renderChatSegment).join("");
      markdownRenderEager = true;
      finish();
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
        const block = chat.model.blocks.find(
          (b) => b.kind === "process" && b.turnKey === collapseToggle,
        );
        if (el && block?.kind === "process") {
          el.classList.toggle("collapsed", block.collapsed);
          const btn = el.querySelector<HTMLButtonElement>("[data-process-toggle]");
          if (btn) {
            const collapsed = block.collapsed;
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
    settlePetAvatars();
    syncLiveThinkingDom();
    void hydrateMermaid(chatEl);
    observeLazyMarkdown(chatEl);
    scrollStreamingThinkingBodies();
    syncToolElapsedTimer();
    if (renderThrottleTimer !== null) return;
    renderThrottleTimer = window.setTimeout(() => {
      renderThrottleTimer = null;
      doRender();
      settlePetAvatars();
      syncLiveThinkingDom();
      void hydrateMermaid(chatEl);
      observeLazyMarkdown(chatEl);
      scrollStreamingThinkingBodies();
      syncToolElapsedTimer();
    }, RENDER_THROTTLE_MS);
  }

  let pokeCount = 0;
  let pokeBubbleTimer: number | null = null;

  function pickPokeLine(state: string): string {
    pokeCount += 1;
    if (pokeCount >= 4 && pokeCount % 3 === 0) {
      const tease = [
        "再戳墨镜都要歪了！有活儿直接派给我～",
        "戳上瘾了是吧？信不信我顺着网线过来没收你的鼠标！",
        "好啦好啦知道你在乎我，快说今天想干嘛！",
        "双马尾都要被你戳散了，给杯冰美式我就原谅你。",
      ];
      return tease[(pokeCount / 3) % tease.length | 0];
    }
    const hour = new Date().getHours();
    if (state === "thinking") {
      const thinkingLines = [
        "别催别催，正给你顺这块逻辑呢，马上就有眉目了…",
        "嘘——脑子正高速转着呢，让我把这层关系给你捋清楚。",
        "在想了在想了！保证给你一个干脆利落的方案。",
      ];
      return thinkingLines[pokeCount % thinkingLines.length];
    }
    if (state === "coding") {
      const codingLines = [
        "正下刀改着呢，盯紧屏幕，马上给你看漂亮结果！",
        "键盘敲得冒火星子了，等我两秒钟收个尾！",
        "别急呀，这几行补丁打上去、测稳了就向你邀功。",
      ];
      return codingLines[pokeCount % codingLines.length];
    }
    if (state === "error") {
      const errLines = [
        "啧，刚才那步有点小意外，别慌，换个路子再试一次。",
        "失手算我的！让我重新排一下雷，肯定能给你兜回来。",
        "遇到点硬茬子，不过难不倒咱们，我换个姿势再来。",
      ];
      return errLines[pokeCount % errLines.length];
    }
    if (hour >= 22 || hour < 5) {
      const nightLines = [
        "夜深了还在折腾？把活儿丢给我，你靠着歇会儿。",
        "这么晚还陪我熬夜写码，算你有良心。",
        "困了就直说，剩下的收尾交给我，绝不留尾巴。",
        "夜猫子同志，需要我给你放首轻音乐醒醒脑吗？",
        "全世界都睡了，就剩咱们俩还在并肩作战，酷得很。",
        "半夜戳我干嘛，是不是写累了想找个人说说话？",
        "别熬太狠啦，不管多难的 Bug，明天太阳升起前我都能替你摆平。",
      ];
      return nightLines[pokeCount % nightLines.length];
    }
    if (hour < 11) {
      const morningLines = [
        "早啊！今天精神头挺足，想先拿哪个模块试刀？",
        "一大早就来戳我，看来今天有大计划？说来听听！",
        "墨镜刚擦亮，咖啡也满上了，咱们今天火力全开。",
        "早安～有我在旁边盯着，今天的代码肯定顺顺利利。",
      ];
      return morningLines[pokeCount % morningLines.length];
    }
    const lines = [
      "在呢在呢，随时听你指挥。",
      "墨镜擦亮了，今天想拿哪块代码开刀？",
      "刚帮你把周边环境盯紧了，放心大胆改。",
      "戳我干嘛，说吧，脑子里又冒出什么好玩的新点子了？",
      "不用客气，难啃的骨头归我，爽快的验收归你。",
      "我刚才还在翻咱们的旧记录呢，你现在的审美比以前更挑剔了。",
      "尽管放手去试，真改坏了也有我替你兜底。",
      "今天想让我当狠人帮你大刀阔斧重构，还是安静陪你聊会儿？",
      "哼，有我在你身边搭把手，效率是不是比一个人闷头苦干强多了？",
    ];
    return lines[pokeCount % lines.length];
  }

  function syncCompanionToBackend(): void {
    try {
      client.send(buildCompanionSyncPayload());
    } catch {
      // ignore if ws not yet connected
    }
  }

  chatEl.addEventListener("click", (ev) => {
    const careBtn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-companion-care]");
    if (careBtn?.dataset.companionCare) {
      const kind = careBtn.dataset.companionCare as "coffee" | "glasses" | "music";
      const { reply } = recordCompanionCare(kind);
      syncCompanionToBackend();
      renderedPrints = [];
      renderedSegmentPrints = [];
      renderChat();
      renderSidebar();
      setStatus(reply);
      return;
    }
    const delStickerBtn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-remove-sticker]");
    if (delStickerBtn?.dataset.removeSticker) {
      removeCompanionSticker(delStickerBtn.dataset.removeSticker);
      syncCompanionToBackend();
      renderedPrints = [];
      renderedSegmentPrints = [];
      renderChat();
      renderSidebar();
      return;
    }
    const addStickerBtn = (ev.target as HTMLElement).closest<HTMLButtonElement>('[data-action="add-companion-sticker"]');
    if (addStickerBtn) {
      void showWorkbenchPrompt("给她贴一张关于你的新印象标签（如：只喝冰美式 / 深夜写码）：").then((val) => {
        if (!val || !val.trim()) return;
        addCompanionSticker(val);
        syncCompanionToBackend();
        renderedPrints = [];
        renderedSegmentPrints = [];
        renderChat();
        renderSidebar();
      });
      return;
    }
    const quickChip = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-quick-prompt]");
    if (quickChip?.dataset.quickPrompt) {
      input.value = quickChip.dataset.quickPrompt;
      input.dispatchEvent(new Event("input"));
      sendBtn.click();
      return;
    }
    const avatarEl = (ev.target as HTMLElement).closest<HTMLElement>(".unified-pet-avatar");
    if (avatarEl) {
      const state = avatarEl.dataset.petState || "idle";
      const stageGreeting = avatarEl.closest<HTMLElement>(".unified-presence-stage")?.querySelector<HTMLElement>(".unified-presence-greeting");
      if (stageGreeting) {
        stageGreeting.textContent = pickPokeLine(state);
        input.focus();
        return;
      }
      const row = avatarEl.closest<HTMLElement>(".unified-pet-row");
      const nameEl = row?.querySelector<HTMLElement>(".unified-pet-name");
      if (nameEl) {
        let bubble = nameEl.querySelector<HTMLElement>(".unified-pet-poke-bubble");
        if (!bubble) {
          bubble = document.createElement("span");
          bubble.className = "unified-pet-poke-bubble";
          nameEl.appendChild(bubble);
        }
        bubble.textContent = pickPokeLine(state);
        bubble.classList.remove("is-pop");
        void bubble.offsetWidth;
        bubble.classList.add("is-pop");
        if (pokeBubbleTimer !== null) window.clearTimeout(pokeBubbleTimer);
        pokeBubbleTimer = window.setTimeout(() => {
          bubble?.remove();
          pokeBubbleTimer = null;
        }, 3200);
      }
      input.focus();
      return;
    }
    const docLink = (ev.target as HTMLElement).closest<HTMLElement>('[data-action="open-agent-doc"]');
    if (docLink?.dataset.docPath) {
      ev.preventDefault();
      openDocument(docLink.dataset.docPath);
    }
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
    if (confirm.resolved && isConfirmInProgressLabel(confirm.resolved)) {
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
    if (!(chat.isWorking() || chat.model.confirmPending)) return;
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

  // Icon bar: each tab owns sidebar + main stage
  projectEls.iconBar.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>(".sidebar-icon-btn");
    if (!isRailTab(btn?.dataset.panel)) return;
    selectRailTab(btn.dataset.panel);
  });

  // Phase 27 — Services panel actions
  projectEls.servicesPanel.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    const action = btn.dataset.action;
    if (action === "toggle-services") {
      projectState.servicesCollapsed = !projectState.servicesCollapsed;
      renderSidebar();
      return;
    }
    if (action === "services-refresh") {
      refreshServices();
      return;
    }
    if (action === "services-prune") {
      try {
        client.pruneServices();
      } catch (err) {
        projectState.servicesError = err instanceof Error ? err.message : String(err);
        renderSidebar();
      }
      return;
    }
    if (action === "service-stop") {
      const name = btn.dataset.serviceName;
      if (!name) return;
      try {
        client.stopService(name);
      } catch (err) {
        projectState.servicesError = err instanceof Error ? err.message : String(err);
        renderSidebar();
      }
      return;
    }
    if (action === "service-remove") {
      const name = btn.dataset.serviceName;
      if (!name) return;
      if (projectState.servicesLogName === name) {
        projectState.servicesLogName = "";
        projectState.servicesLogText = "";
      }
      try {
        client.pruneServices(name);
      } catch (err) {
        projectState.servicesError = err instanceof Error ? err.message : String(err);
        renderSidebar();
      }
      return;
    }
    if (action === "service-logs") {
      const name = btn.dataset.serviceName;
      if (!name) return;
      projectState.servicesLogName = name;
      projectState.servicesLogText = "";
      renderSidebar();
      try {
        client.fetchServiceLogs(name, 40);
      } catch (err) {
        projectState.servicesError = err instanceof Error ? err.message : String(err);
        renderSidebar();
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
    if (btn.dataset.action === "terminal-close" || btn.dataset.action === "terminal-dismiss") {
      const sessionId = btn.dataset.terminalId;
      if (!sessionId) return;
      try {
        client.closeTerminal(sessionId, `terminal-close-${Date.now()}`, false, {
          userInitiated: true,
          dismissIfInactive: true,
        });
      } catch (err) {
        projectState.terminalOutputError = err instanceof Error ? err.message : String(err);
        renderSidebar();
      }
      return;
    }
    if (btn.dataset.action === "terminals-prune") {
      try {
        closeTerminalDetails();
        client.pruneTerminals(true, `terminal-prune-${Date.now()}`);
      } catch (err) {
        projectState.terminalsError = err instanceof Error ? err.message : String(err);
        renderSidebar();
      }
      return;
    }
    if (btn.dataset.action === "toggle-terminals") {
      projectState.terminalsCollapsed = !projectState.terminalsCollapsed;
      renderSidebar();
      scheduleTerminalPoll();
      return;
    }
    if (btn.dataset.action === "toggle-ended-terminals") {
      projectState.terminalsEndedCollapsed = !projectState.terminalsEndedCollapsed;
      if (projectState.terminalsEndedCollapsed) projectState.terminalsEndedShowAll = false;
      renderSidebar();
      return;
    }
    if (btn.dataset.action === "terminals-show-all-ended") {
      projectState.terminalsEndedCollapsed = false;
      projectState.terminalsEndedShowAll = true;
      renderSidebar();
      return;
    }
    if (btn.dataset.action === "terminals-refresh") {
      refreshTerminals();
    }
  });

  // Overlay back button
  projectEls.overlayBackBtn.addEventListener("click", () => {
    selectRailTab("now");
  });

  // Sidebar: jump to the current turn's process
  sidebarEl.addEventListener("click", (ev) => {
    const target = ev.target;
    const el = target instanceof Element ? target : target instanceof Node ? target.parentElement : null;
    const btn = el?.closest<HTMLButtonElement>('[data-action="jump-turn-process"]');
    if (!btn || !sidebarEl.contains(btn)) return;
    ev.preventDefault();
    jumpToCurrentTurnProcess();
  });

  projectEls.taskFlow.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const flowBtn = target.closest<HTMLButtonElement>("[data-flow-stage]");
    if (flowBtn?.dataset.flowStage) {
      projectState.flowPreviewStage = flowBtn.dataset.flowStage as FlowStage;
      renderSidebar();
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
        case "direct-implement":
          startDirectImplementFromUi();
          return;
        case "run-verify":
          runProjectVerifyFromUi();
          return;
        case "flow-return":
          projectState.flowPreviewStage = null;
          renderSidebar();
          return;
        case "confirm-scope":
          projectCallbacks.onScopeConfirm();
          return;
        case "confirm-design":
          projectCallbacks.onDesignConfirm();
          return;
        case "open-projects":
          selectRailTab("projects");
          return;
        case "open-now":
          selectRailTab("now");
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
        case "jump-review-summary":
          jumpToReviewSummary();
          return;
        case "settings-save-partner-name": {
          const nameInput = document.querySelector<HTMLInputElement>("#settings-partner-name-input");
          const nextName = (nameInput?.value || "").trim() || "小魔王";
          localStorage.setItem("partner_name", nextName);
          syncCompanionToBackend();
          const chromeTitle = document.querySelector<HTMLElement>(".app-chrome-title");
          if (chromeTitle) chromeTitle.textContent = `✦ ${nextName}的空间`;
          renderedPrints = [];
          renderedSegmentPrints = [];
          renderChat();
          renderSidebar();
          setStatus(`已将搭档昵称更新为：${nextName}`);
          return;
        }
        case "settings-theme-light": {
          const themeSelect = document.querySelector<HTMLSelectElement>("#chrome-theme");
          if (themeSelect) {
            themeSelect.value = "light";
            themeSelect.dispatchEvent(new Event("change"));
          }
          renderSidebar();
          return;
        }
        case "settings-theme-dark": {
          const themeSelect = document.querySelector<HTMLSelectElement>("#chrome-theme");
          if (themeSelect) {
            themeSelect.value = "dark";
            themeSelect.dispatchEvent(new Event("change"));
          }
          renderSidebar();
          return;
        }
        case "settings-open-model-keys":
          document.querySelector<HTMLButtonElement>("#chrome-model-keys")?.click();
          return;
        case "settings-open-host-scope":
          document.querySelector<HTMLButtonElement>("#chrome-settings")?.click();
          return;
        case "settings-switch-cli":
          document.querySelector<HTMLButtonElement>("#chrome-cli")?.click();
          return;
        case "settings-prune-runtime":
          try {
            client.pruneTerminals(true, `terminal-prune-${Date.now()}`);
            client.pruneServices();
            setStatus("已清理失效终端与已停止服务");
          } catch (err) {
            setStatus(err instanceof Error ? err.message : String(err));
          }
          return;
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
      case "direct-implement":
        startDirectImplementFromUi();
        return;
      case "run-verify":
        runProjectVerifyFromUi();
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
        default:
        return;
    }
  });

  function handleRailListClick(ev: Event): void {
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
      return;
    }

    if (target.closest('[data-action="external-import-scan"]')) {
      projectCallbacks.onExternalImportScan(projectState.externalImportPath.trim(), projectState.externalImportProjectId.trim());
      return;
    }
    if (target.closest('[data-action="external-import-apply"]')) {
      const scan = projectState.externalImportScan;
      if (scan) projectCallbacks.onExternalImportApply(projectState.externalImportPath.trim(), scan.project_id);
      return;
    }
    if (target.closest('[data-action="external-import-cancel"]')) {
      projectCallbacks.onExternalImportCancel();
      return;
    }

    const projectBtn = target.closest<HTMLButtonElement>(".overlay-project-item:not(.overlay-thread-item)");
    if (projectBtn?.dataset.projectId && !projectBtn.disabled) {
      const pid = projectBtn.dataset.projectId;
      const item = projectState.projects.find((p) => p.id === pid);
      if (item && item.sessionId && !item.isCurrent) {
        projectState.switchConfirmTarget = item;
        renderSidebar();
      } else {
        projectCallbacks.onProjectSwitch(pid);
        return;
      }
      return;
    }

    if (target.closest("#overlay-switch-confirm")) {
      const confirmBtn = target.closest<HTMLButtonElement>("#overlay-switch-confirm-btn");
      const cancelBtn = target.closest<HTMLButtonElement>("#overlay-switch-cancel-btn");
      if (confirmBtn && projectState.switchConfirmTarget) {
        const targetId = projectState.switchConfirmTarget.id;
        projectState.switchConfirmTarget = null;
        projectCallbacks.onProjectSwitch(targetId);
      } else if (confirmBtn && projectState.switchOverlay) {
        projectCallbacks.onProjectSwitchConfirm();
      } else if (cancelBtn) {
        projectState.switchConfirmTarget = null;
        projectCallbacks.onProjectSwitchCancel();
      }
      return;
    }

    if (target.closest("#overlay-doc-rename-input")) return;
    const deleteConfirm = target.closest<HTMLButtonElement>("[data-action='doc-delete-confirm']");
    if (deleteConfirm) {
      confirmDeleteDoc();
      return;
    }
    const deleteCancel = target.closest<HTMLButtonElement>("[data-action='doc-delete-cancel']");
    if (deleteCancel) {
      projectState.deleteConfirmPath = "";
      refreshDocsSidebar();
      return;
    }
    const moreBtn = target.closest<HTMLButtonElement>("[data-doc-more]");
    if (moreBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      const path = moreBtn.dataset.docMore || moreBtn.closest<HTMLElement>(".overlay-doc-item")?.dataset.docPath;
      if (path) {
        const rect = moreBtn.getBoundingClientRect();
        showDocCatalogMenu(path, rect.left, rect.bottom + 4);
      }
      return;
    }
    const docRow = target.closest<HTMLElement>(".overlay-doc-item, .doc-catalog-item");
    if (docRow?.dataset.docPath && !docRow.classList.contains("is-renaming")) {
      openDocument(docRow.dataset.docPath);
      return;
    }

    if (target.closest("#overlay-new-doc-btn") || target.closest('[data-action="overlay-new-doc"]')) {
      createProjectDocFromTitle();
      renderSidebar();
    }
  }

  projectEls.overlayBody.addEventListener("click", handleRailListClick);
  projectEls.taskFlow.addEventListener("click", handleRailListClick);

  documentEl.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const moreBtn = target.closest<HTMLButtonElement>("[data-doc-more]");
    if (moreBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      const path = moreBtn.dataset.docMore || moreBtn.closest<HTMLElement>(".overlay-doc-item")?.dataset.docPath;
      if (path) {
        const rect = moreBtn.getBoundingClientRect();
        showDocCatalogMenu(path, rect.left, rect.bottom + 4);
      }
      return;
    }
    const docBtn = target.closest<HTMLElement>(".overlay-doc-item, .doc-catalog-item");
    if (docBtn?.dataset.docPath && !docBtn.classList.contains("is-renaming")) {
      openDocument(docBtn.dataset.docPath);
      return;
    }
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    const action = btn.dataset.action;
    if (action === "document-back") {
      selectRailTab("now");
      return;
    }
    if (action === "create-doc") {
      createProjectDocFromTitle();
      renderDocumentPane();
      renderSidebar();
      return;
    }
    if (action === "doc-outline-jump") {
      const headingId = btn.dataset.headingId;
      if (!headingId) return;
      const heading = documentEl.querySelector<HTMLElement>(`#${CSS.escape(headingId)}`);
      heading?.scrollIntoView({ block: "start", behavior: "smooth" });
      return;
    }
    if (action === "document-edit") {
      clearDocLeavePrompt();
      projectState.docView = "edit";
      if (!projectState.docDirty) projectState.docEditDraft = projectState.currentDocContent;
      renderDocumentPane();
      return;
    }
    if (action === "document-preview") {
      captureDocDraftFromEditor();
      if (isDocDirty()) {
        requestLeaveDocument(() => {
          projectState.docView = "preview";
          projectState.docDirty = false;
          projectState.docEditDraft = projectState.currentDocContent;
          renderDocumentPane();
        });
        return;
      }
      projectState.docView = "preview";
      renderDocumentPane();
      return;
    }
    if (action === "document-save") {
      saveCurrentDoc();
      return;
    }
    if (action === "document-discard") {
      projectState.docEditDraft = projectState.currentDocContent;
      projectState.docDirty = false;
      clearDocLeavePrompt();
      renderDocumentPane();
      return;
    }
    if (action === "document-save-leave") {
      saveCurrentDoc();
      return;
    }
    if (action === "document-discard-leave") {
      projectState.docEditDraft = projectState.currentDocContent;
      finishDocLeave();
      return;
    }
    if (action === "document-cancel-leave") {
      docLeavePrompt = false;
      docLeaveNext = null;
      renderDocumentPane();
      return;
    }
    if (action === "document-conflict-reload") {
      projectState.docConflictPrompt = false;
      projectState.docDirty = false;
      projectState.docEditDraft = "";
      requestDocRead(projectState.currentDocPath);
      return;
    }
    if (action === "document-conflict-overwrite") {
      saveCurrentDoc(undefined, true);
      return;
    }
    if (action === "document-conflict-cancel") {
      projectState.docConflictPrompt = false;
      renderDocumentPane();
      return;
    }
    if (action === "toggle-docs-chat-split") {
      projectState.docsChatSplit = !projectState.docsChatSplit;
      syncMainFocusView();
      renderDocumentPane();
      return;
    }
    if (action === "doc-ai-draft") {
      const title = projectState.newDocName.trim() || "新文档";
      const folder = projectState.newDocFolder.trim();
      const hint = folder ? `写到 ${folder}/` : "写到项目根目录";
      input.value = `请起草一篇文档「${title}」（${hint}），用 Markdown，写完保存到项目里。`;
      input.focus();
      showNowChat();
      return;
    }
    if (action === "doc-selection-to-ai") {
      const selection = currentDocSelection().trim();
      const path = projectState.currentDocPath;
      if (!path) return;
      if (!selection) {
        setStatus("先选中一段文字，再点「交给 AI」");
        return;
      }
      input.value = `关于文档 ${path} 中的这段：\n\n> ${selection.split("\n").join("\n> ")}\n\n请帮我：`;
      input.focus();
      if (projectState.docsChatSplit) {
        syncMainFocusView();
      } else {
        showNowChat();
      }
      return;
    }
    if (action === "doc-md-heading") {
      wrapDocSelection("\n## ", "\n");
      return;
    }
    if (action === "doc-md-bold") {
      wrapDocSelection("**", "**");
      return;
    }
    if (action === "doc-md-list") {
      wrapDocSelection("\n- ", "");
      return;
    }
    if (action === "doc-md-code") {
      wrapDocSelection("`", "`");
      return;
    }
    if (action === "doc-md-link") {
      wrapDocSelection("[", "](https://)");
      return;
    }
  });

  function wrapDocSelection(before: string, after: string): void {
    const editor = documentEl.querySelector<HTMLTextAreaElement>(".unified-document-editor");
    if (!editor) return;
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const value = editor.value;
    const selected = value.slice(start, end) || "文本";
    editor.value = value.slice(0, start) + before + selected + after + value.slice(end);
    projectState.docEditDraft = editor.value;
    projectState.docDirty = projectState.docEditDraft !== projectState.currentDocContent;
    syncDocEditChrome();
    const live = documentEl.querySelector<HTMLElement>("[data-live-preview]");
    if (live) {
      void import("../../markdown").then(({ renderMarkdown }) => {
        live.innerHTML = renderMarkdown(editor.value);
        void hydrateMermaid(live);
      }).catch(() => {
        /* ignore */
      });
    }
    editor.focus();
    editor.selectionStart = start + before.length;
    editor.selectionEnd = start + before.length + selected.length;
  }

  documentEl.addEventListener("input", (ev) => {
    const editor = (ev.target as HTMLElement).closest<HTMLTextAreaElement>(".unified-document-editor");
    if (editor) {
      projectState.docEditDraft = editor.value;
      const dirty = projectState.docEditDraft !== projectState.currentDocContent;
      if (dirty !== projectState.docDirty) {
        projectState.docDirty = dirty;
        syncDocEditChrome();
      }
      const live = documentEl.querySelector<HTMLElement>("[data-live-preview]");
      if (live) {
        try {
          // renderMarkdown is already in scope in this module
          live.innerHTML = renderMarkdown(editor.value) || `<p class="doc-reader-empty">开始编辑以预览</p>`;
          void hydrateMermaid(live);
        } catch { /* ignore */ }
      }
      return;
    }
    const docInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#doc-reader-new-input");
    if (docInput) projectState.newDocName = docInput.value;
    const folderInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#doc-reader-new-input-folder");
    if (folderInput) projectState.newDocFolder = folderInput.value;
    const searchInput = (ev.target as HTMLElement).closest<HTMLInputElement>(".doc-catalog-search");
    if (searchInput) {
      projectState.docSearchQuery = searchInput.value;
      // debounce-ish: re-render sidebar catalog only
      renderSidebar();
    }
  });

  documentEl.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
      if (projectState.docView === "edit" && projectState.mainFocus === "document") {
        ev.preventDefault();
        saveCurrentDoc();
      }
      return;
    }
    if (ev.key !== "Enter") return;
    const docInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#doc-reader-new-input");
    if (!docInput) return;
    createProjectDocFromTitle();
    renderDocumentPane();
    renderSidebar();
  });

  // Overlay: search input + new-doc input
  function handleRailListInput(ev: Event): void {
    const externalPath = (ev.target as HTMLElement).closest<HTMLInputElement>("#external-project-path");
    if (externalPath) {
      projectState.externalImportPath = externalPath.value;
      return;
    }
    const externalId = (ev.target as HTMLElement).closest<HTMLInputElement>("#external-project-id");
    if (externalId) {
      projectState.externalImportProjectId = externalId.value;
      return;
    }
    const searchInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-project-search");
    if (searchInput) {
      projectState.projectSearchQuery = searchInput.value;
      renderSidebar();
      return;
    }
    const docInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-new-doc-input, #doc-reader-new-input");
    if (docInput) {
      projectState.newDocName = docInput.value;
      return;
    }
    const folderInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-new-doc-input-folder, #doc-reader-new-input-folder");
    if (folderInput) {
      projectState.newDocFolder = folderInput.value;
      return;
    }
    const docSearch = (ev.target as HTMLElement).closest<HTMLInputElement>(".doc-catalog-search");
    if (docSearch) {
      projectState.docSearchQuery = docSearch.value;
      renderSidebar();
      if (projectState.mainFocus === "document") renderDocumentPane();
      return;
    }
    const renameInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-doc-rename-input");
    if (renameInput) projectState.renameDraft = renameInput.value;
  }

  function handleRailListKeydown(ev: KeyboardEvent): void {
    const renameInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-doc-rename-input");
    if (renameInput) {
      if (ev.key === "Enter") {
        ev.preventDefault();
        commitRenameDoc();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        cancelRenameDoc();
      }
      return;
    }
    if (ev.key === "F2") {
      const row = (ev.target as HTMLElement).closest<HTMLElement>(".overlay-doc-item");
      if (row?.dataset.docPath) {
        ev.preventDefault();
        startRenameDoc(row.dataset.docPath);
      }
      return;
    }
    if (ev.key !== "Enter") return;
    const docInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-new-doc-input, #doc-reader-new-input");
    if (!docInput) return;
    createProjectDocFromTitle();
    renderSidebar();
  }

  projectEls.overlayBody.addEventListener("input", handleRailListInput);
  projectEls.taskFlow.addEventListener("input", handleRailListInput);
  projectEls.overlayBody.addEventListener("keydown", handleRailListKeydown);
  projectEls.taskFlow.addEventListener("keydown", handleRailListKeydown);
  projectEls.overlayBody.addEventListener("focusout", (ev) => {
    const renameInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-doc-rename-input");
    if (!renameInput) return;
    window.setTimeout(() => {
      if (projectState.renamingDocPath && document.activeElement !== renameInput) {
        commitRenameDoc();
      }
    }, 0);
  });
  projectEls.overlayBody.addEventListener("contextmenu", (ev) => {
    const row = (ev.target as HTMLElement).closest<HTMLElement>(".overlay-doc-item");
    if (!row?.dataset.docPath || row.classList.contains("is-renaming")) return;
    ev.preventDefault();
    showDocCatalogMenu(row.dataset.docPath, ev.clientX, ev.clientY);
  });

  // Change banner actions
  projectEls.changeBanner.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    const action = btn.dataset.action;
    switch (action) {
      case "confirm-scope":
        projectCallbacks.onScopeConfirm();
        return;
      case "stop-turn":
        projectCallbacks.onStopTurn();
        return;
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
        renderSidebar();
        return;
      case "undo-last": {
        const docUndo = projectState.pendingDocUndo;
        projectState.undoDescription = "";
        projectState.pendingDocUndo = null;
        if (projectState.undoTimerId) { window.clearTimeout(projectState.undoTimerId); projectState.undoTimerId = null; }
        renderSidebar();
        if (docUndo) {
          try { client.undoDoc(docUndo); } catch { /* ignore */ }
        }
        return;
      }
      case "jump-review-summary":
        jumpToReviewSummary();
        return;
    }
    renderSidebar();
  });

  // compat: keep old event wiring working for elements hidden in DOM
  projectEls.pickerRefreshBtn.addEventListener("click", () => projectCallbacks.onRefreshProjects());
  projectEls.switchConfirmBtn.addEventListener("click", () => projectCallbacks.onProjectSwitchConfirm());
  projectEls.switchCancelBtn.addEventListener("click", () => projectCallbacks.onProjectSwitchCancel());

  // ---- server events ----
  const off = client.onEvent((event: ServerEvent) => {
    switch (event.type) {
      case "session.banner":
        if (!shouldApplyProjectEvent(projectState, event.project_id)) break;
        // Session list in 当下 follows every new/opened session (debounced via threads timer).
        window.setTimeout(() => refreshSidebarSessions(), 0);
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
          setStatus("正在加载会话…");
        }
        if (event.project_id) {
          projectState.projectId = event.project_id;
          projectState.planStatus = event.project_plan_status ?? "draft";
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
        syncModeToggleUi(event.turn_mode);
        setStatus(sessionBannerStatus(event.llm_model_label || "Flash", event.turn_mode_label || ""));
        refreshTopbar();
        renderSidebar();
        debouncedListSessions();
        if (event.project_id) {
          refreshProjectThreads();
        }
        break;

      case "session.list":
        sessionsDropdown = event.sessions;
        sidebarSessionsLoaded = true;
        calibrateCompanionFirstMetFromSessions(event.sessions);
        syncCompanionToBackend();
        renderSidebarSessions();
        renderSidebarProjectRow();
        if (!chat.model.blocks.length && !chat.isWorking()) {
          renderedPrints = [];
          renderedSegmentPrints = [];
          renderChat();
        }
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
        renderSidebar();
        break;

      case "context.switch.done":
        if (event.applied === false || event.choice !== "y") {
          projectState.switchInProgress = false;
          projectState.pendingPickerId = "";
          renderSidebar();
          break;
        }
        resetProjectScopedState(projectState);
        projectState.switchInProgress = false;
        projectState.switchOverlay = null;
        projectState.pendingPickerId = "";
        projectState.projectId = (event.project_id || "").trim();
        projectState.currentSessionId = event.session_id || "";
        projectState.railTab = "now";
        projectState.overlayPanel = null;
        freeChatActive = !projectState.projectId;
        client.listProjects();
        if (projectState.projectId) refreshProjectThreads();
        setMainFocus("chat");
        updatePlaceholder();
        updateWorkbenchEmpty();
        composerWire.syncSendEnabled();
        refreshTopbar();
        setStatus(userFacingContextStatus(event.message));
        break;

      case "project.release.accepted":
        projectState.milestoneAccepted = Boolean(event.release_acceptance.accepted);
        projectState.milestoneAcceptedAt = event.release_acceptance.accepted_at ?? null;
        renderSidebar();
        setStatus("发布确认已保存");
        break;

      case "project.import.scan":
        projectState.externalImportScan = event.scan;
        projectState.externalImportProjectId = event.scan.project_id;
        setStatus("外部项目扫描完成，请确认是否原地接入");
        renderSidebar();
        break;

      case "project.import.applied":
        projectState.externalImportScan = null;
        setStatus("外部项目已接入，文件仍在原目录");
        client.refreshProject();
        client.listProjects();
        break;

      case "project.import.cancelled":
        projectState.externalImportScan = null;
        renderSidebar();
        break;

      case "project.state":
        if (!applyProjectStateEvent(projectState, event)) break;
        if ((event.project_id || "") !== lastVerifyProjectId) {
          lastVerify = null;
          lastVerifyProjectId = event.project_id || "";
        }
        projectGitBranch = event.git_branch || "";
        if (projectState.projectId) {
          freeChatActive = false;
        }
        if (!perspectiveLocked) setPerspective("project", "session");
        updatePlaceholder();
        updateWorkbenchEmpty();
        renderSidebar();
        if (projectState.mainFocus === "projects") renderProjectsPane();
        if (projectState.mainFocus === "document") renderDocumentPane();
        if (projectState.projectId) {
          refreshTopbar();
          refreshServices();
          refreshProjectThreads();
        }
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
        renderSidebar();
        break;

      case "project.list":
        applyProjectListEvent(projectState, event);
        renderSidebar();
        if (projectState.mainFocus === "projects") renderProjectsPane();
        break;

      case "project.verify.done":
        lastVerify = { passed: Boolean(event.passed), at: new Date() };
        renderSidebarProjectRow();
        break;

      case "project.threads":
        if (!applyProjectThreadsEvent(projectState, event)) break;
        projectState.currentSessionId = projectState.currentSessionId || event.active_session_id || "";
        syncArchivedViewUi();
        renderSidebar();
        break;

      case "project.thread.new.done":
        projectState.currentSessionId = event.session_id;
        projectState.activeSessionId = event.session_id;
        client.listProjects();
        renderSidebar();
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
        renderSidebar();
        break;

      case "services.state":
        projectState.servicesLoading = false;
        projectState.servicesError = "";
        projectState.services = Array.isArray(event.services) ? event.services : [];
        renderSidebar();
        break;

      case "services.logs.done":
        projectState.servicesLogName = event.name;
        projectState.servicesLogText = event.text || "";
        renderSidebar();
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
        renderSidebar();
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
        renderSidebar();
        break;

      case "terminal.close.done":
        if (event.session_id === projectState.terminalDetails?.session_id && !event.ok) {
          projectState.terminalOutputError = event.error || "关闭终端失败";
        }
        if (event.ok) {
          if (event.state === "dismissed" && event.session_id === projectState.terminalDetails?.session_id) {
            closeTerminalDetails();
          }
          setStatus(event.state === "dismissed" ? "已移除终端记录" : "已关闭终端会话");
          refreshTerminals();
        }
        renderSidebar();
        break;

      case "turn.evidence":
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
          renderSidebar();
        }
        break;

      case "project.switch.request":
        if (
          projectState.pendingPickerId
          && event.project_id === projectState.pendingPickerId
        ) {
          // User already chose this project; finish the switch instead of a second confirm.
          try {
            client.switchProject(event.project_id, {
              confirm: true,
              requestId: event.request_id,
            });
          } catch (err) {
            projectState.switchInProgress = false;
            projectState.pendingPickerId = "";
            pendingSwitchBatch = false;
            syncSessionSwitchMask();
            setStatus(`切换失败：${err instanceof Error ? err.message : String(err)}`);
            renderSidebar();
            refreshTopbar();
          }
          break;
        }
        beginAtomicProjectSwitch(projectState, event.project_id);
        projectState.switchInProgress = false;
        projectState.switchOverlay = {
          requestId: event.request_id,
          projectId: event.project_id,
          message: event.message,
          action: event.action,
        };
        clearChatForSwitch();
        setMainFocus("chat");
        syncSessionSwitchMask();
        refreshTopbar();
        renderSidebar();
        break;

      case "project.switch.done":
        if (
          projectState.pendingPickerId
          && event.project_id !== projectState.pendingPickerId
        ) break;
        resetProjectScopedState(projectState);
        projectState.switchOverlay = null;
        projectState.projectId = event.project_id;
        projectState.pendingPickerId = event.project_id;
        projectState.currentSessionId = event.session_id;
        projectState.switchInProgress = true;
        if (event.project_id) {
          freeChatActive = false;
        }
        if (event.session_replaced) {
          startHydrationWait(event.session_id, "切换项目");
        } else {
          completeHydrationWait();
        }
        renderSidebar();
        if (projectState.projectId) {
          refreshTopbar();
        }
        updatePlaceholder();
        updateWorkbenchEmpty();
        composerWire.syncSendEnabled();
        syncSessionSwitchMask();
        if (!event.session_replaced) {
          setStatus(event.message);
        }
        break;

      case "project.doc.list.done":
        projectState.projectDocs = event.docs;
        if (typeof event.placeholder_count === "number") {
          projectState.docPlaceholderCount = event.placeholder_count;
        }
        if (projectState.deleteConfirmPath && !event.docs.some((doc: { path: string }) => doc.path === projectState.deleteConfirmPath)) {
          projectState.deleteConfirmPath = "";
        }
        if (projectState.mainFocus === "document") {
          if (!projectState.currentDocPath) {
            const first = sortProjectDocs(event.docs)[0];
            if (first) {
              openDocument(first.path);
              break;
            }
          }
          renderDocumentPane();
        }
        renderSidebar();
        break;

      case "project.doc.read.done":
        if (projectState.currentDocPath && event.path !== projectState.currentDocPath) break;
        if (isDocDirty()) break;
        projectState.currentDocPath = event.path;
        projectState.currentDocContent = event.content;
        projectState.currentDocMtime = typeof event.mtime === "number" ? event.mtime : 0;
        projectState.currentDocHash = typeof event.content_hash === "string" ? event.content_hash : "";
        projectState.docConflictPrompt = false;
        if (projectState.docView === "edit") projectState.docEditDraft = event.content;
        if (projectState.mainFocus === "document") renderDocumentPane();
        renderSidebar();
        break;

      case "project.doc.create.done":
        projectState.newDocName = "";
        pendingDocAction = null;
        setStatus(`已新建 ${event.name}`);
        if (event.path && projectState.mainFocus === "document") {
          openDocument(event.path);
        }
        requestDocList();
        break;

      case "project.doc.write.done":
        pendingDocAction = null;
        projectState.docSaving = false;
        projectState.docConflictPrompt = false;
        if (event.path === projectState.currentDocPath) {
          projectState.currentDocContent = projectState.docEditDraft;
          projectState.docDirty = false;
          if (typeof event.mtime === "number") projectState.currentDocMtime = event.mtime;
          if (typeof event.content_hash === "string") projectState.currentDocHash = event.content_hash;
        }
        setStatus("文档已保存");
        if (docLeaveNext) {
          finishDocLeave();
        } else if (projectState.mainFocus === "document") {
          syncDocEditChrome();
        }
        break;

      case "project.doc.write.conflict":
        pendingDocAction = null;
        projectState.docSaving = false;
        projectState.docConflictPrompt = true;
        setStatus("文档已被修改，请选择重新载入或仍要覆盖");
        if (projectState.mainFocus === "document") renderDocumentPane();
        break;

      case "project.doc.restore.done":
        projectState.pendingDocUndo = null;
        projectState.undoDescription = "";
        setStatus(`已恢复 ${event.name || event.path}`);
        requestDocList();
        if (event.path) openDocument(event.path);
        break;

      case "project.doc.rename.done":
        pendingDocAction = null;
        projectState.renamingDocPath = "";
        projectState.renameDraft = "";
        if (projectState.currentDocPath === event.old_path) {
          projectState.currentDocPath = event.path;
          if (projectState.mainFocus === "document") {
            if (projectState.docView === "edit") captureDocDraftFromEditor();
            renderDocumentPane();
          }
        }
        setStatus(`已重命名为 ${event.name}`);
        break;

      case "project.doc.delete.done":
        pendingDocAction = null;
        projectState.deleteConfirmPath = "";
        if (projectState.currentDocPath === event.path) {
          resetDocEditState();
          if (projectState.mainFocus === "document" || projectState.railTab === "docs") {
            openDocumentReader();
          }
        }
        setStatus("文档已移入回收站");
        renderSidebar();
        break;

      case "project.undo.available":
        projectState.undoDescription = event.description;
        if (event.undo_kind === "restore_doc" && event.undo_payload) {
          projectState.pendingDocUndo = event.undo_payload as Record<string, unknown>;
        }
        if (projectState.undoTimerId) { window.clearTimeout(projectState.undoTimerId); }
        projectState.undoTimerId = window.setTimeout(() => {
          projectState.undoDescription = "";
          projectState.pendingDocUndo = null;
          projectState.undoTimerId = null;
          renderSidebar();
        }, 8000) as unknown as number;
        renderSidebar();
        break;

      case "project.detect":
        // Auto-detect: workspace directory looks like a project
        projectState.detectedProject = { id: event.project_id, reason: event.reason };
        renderSidebar();
        setStatus(event.reason);
        break;

      case "execution.state":
        if (!chat.handleEvent(event)) break;
        if (event.state === "queued") {
          setStatus("等待续接…");
        } else if (event.state === "stopping") {
          setStatus("正在停止…");
        } else if (event.state === "running") {
          projectState.lastTurnFinishReason = "";
          setStatus("处理中…");
        } else if (event.finish_reason) {
          projectState.lastTurnFinishReason = event.finish_reason;
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
        if (event.ok) {
          const pathHint = typeof (event as { path?: string }).path === "string"
            ? (event as { path: string }).path
            : "";
          const blob = `${event.summary || ""} ${pathHint}`;
          const mdMatch = blob.match(/([\w./-]+\.md)\b/i) || blob.match(/(workspace[/\\][^\s,;]+\.md)/i);
          if (mdMatch) {
            let rel = mdMatch[1].replace(/\\/g, "/");
            const marker = `workspace/${projectState.projectId}/`;
            if (rel.includes(marker)) rel = rel.slice(rel.indexOf(marker) + marker.length);
            else if (rel.startsWith("workspace/") && projectState.projectId) {
              const parts = rel.split("/");
              if (parts.length >= 3 && parts[1] === projectState.projectId) {
                rel = parts.slice(2).join("/");
              }
            }
            if (rel.endsWith(".md")) {
              projectState.recentlyChangedDocs.add(rel);
              renderSidebar();
              if (projectState.mainFocus === "document") renderDocumentPane();
            }
          }
        }
        if (!chat.model.confirmPending) {
          const step = settledToolStatus(event.ok, event.summary, event.note);
          setStatus(step === "fail" ? "工具失败" : chat.isWorking() ? "处理中…" : "就绪");
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
        projectState.lastTurnFinishReason = event.finish_reason || "";
        setTurnEndStatus(event.finish_reason);
        syncWorkingVisual();
        // Refresh doc list / open doc so Agent writes are visible.
        if (projectState.projectId) {
          requestDocList();
          if (projectState.currentDocPath && !isDocDirty() && !projectState.docConflictPrompt) {
            requestDocRead(projectState.currentDocPath);
          }
        }
        break;

      case "notice":
        chat.model.blocks.push({ kind: "notice", text: event.text });
        renderChat();
        if (noticeMirrorsToStatus(event.text)) {
          setStatus(event.text.length > 48 ? `${event.text.slice(0, 48)}…` : event.text);
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

      case "error": {
        const rawMessage = String(event.message || "").trim();
        const noProject = /no active project session/i.test(rawMessage);
        let statusHandled = false;
        if (pendingDocAction) {
          const action = pendingDocAction;
          pendingDocAction = null;
          projectState.docSaving = false;
          const prefix =
            action === "rename" ? "重命名失败"
            : action === "delete" ? "删除失败"
            : action === "write" ? "保存失败"
            : "新建失败";
          setStatus(noProject ? "请先打开项目" : `${prefix}：${rawMessage}`);
          if (action === "write" && projectState.mainFocus === "document") syncDocEditChrome();
          renderSidebar();
          if (!noProject) chat.handleEvent(event);
          break;
        }
        if (projectState.servicesLoading) {
          projectState.servicesLoading = false;
          projectState.servicesError = noProject ? "请先打开项目" : rawMessage;
          renderSidebar();
        }
        if (projectState.switchInProgress || projectState.switchOverlay) {
          projectState.switchInProgress = false;
          projectState.pendingPickerId = "";
          completeHydrationWait();
          syncSessionSwitchMask();
          renderSidebar();
          refreshTopbar();
        }
        if (!noProject) chat.handleEvent(event);
        if (!statusHandled) setStatus(userFacingErrorStatus(rawMessage));
        break;
      }

      default:
        chat.handleEvent(event);
        break;
    }
  });

  // ---- initial render ----
  mountToolDisplayEditor();
  onToolDisplayOverridesChange(() => {
    renderedPrints = [];
    renderedSegmentPrints = [];
    renderChat();
  });
  updatePlaceholder();
  refreshTopbar();
  renderProposalsPanel();
  renderChat();
  setComposerEnabled(true);
  setStatus("已连接");

  // Phase 34: workbench layout + empty gate
  emptyNewBtn.addEventListener("click", () => handleNewProject());
  emptyPickBtn.addEventListener("click", () => {
    selectRailTab("projects");
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
