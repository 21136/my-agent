import { setAgentBusy } from "../../agent-busy";
import { wireComposerAttachments } from "../../composer-attachments";
import { mountFileDrop } from "../../file-drop";
import { renderMarkdown } from "../../markdown";
import { formatUserMessageHtml } from "../../user-message";
import { createChatSession, escapeHtml, turnEndStatusText, checkerVerdictStatusText, formatToolElapsed, isConfirmInProgressLabel, isThinkingBodyOpen, thinkingTitleLabel, type ChatBlock } from "../chat-state";
import { renderTopbar, type TopbarState } from "./topbar";
import { renderProposals, currentProposal, nextProposalIndex, type ProposalsState } from "./proposals";
import {
  setupProjectPanel,
  renderProjectSidebar,
  renderPlanTaskFlow,
  applyProjectStateEvent,
  applyProjectListEvent,
  applyProjectPlanState,
  applyProjectThreadsEvent,
  isViewingArchivedThread,
  parseTasksMarkdown,
  type OverlayPanel,
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
import "./unified.css";

export type Perspective = "default" | "project" | "night";

const FOCUS_TURNS = 2;
/** Process A-layer: show only the latest N tool lines; earlier ones fold (UX-023). */
const PROCESS_TOOL_LINES_CAP = 6;
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
    sessionCount: 0,
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

  function refreshTopbar(): void {
    renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
  }

  const proposalsState: ProposalsState = {
    proposals: [] as ProposalItem[],
    proposalIndex: 0,
    expandOpen: false,
  };

  const projectState: ProjectPanelState = {
    projectId: "",
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
    taskSnapshot: { lines: new Set(), lineTexts: new Map() },
    highlightChanges: false,
    highlightedLines: new Set(),
    projectDocs: [],
    currentDocPath: "",
    currentDocContent: "",
    newDocName: "",
    quickAddText: "",
    detectedProject: null,
    planWarnings: [],
    undoDescription: "",
    undoTimerId: null,
    degradationLevel: "L1",
    degradationLabel: "全功能",
    changesLevel: null,
    autoConfirmTimerId: null,
    externalChanges: false,
    suggestions: [],
    autoFixNotices: [],
    partnerNotices: [],
    partnerBusy: false,
    nextTask: null,
    nextTaskLine: null,
    services: [],
    servicesLoading: false,
    servicesError: "",
    servicesLogName: "",
    servicesLogText: "",
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
  };

  let suggestionAdoptFlashTimerId: number | null = null;
  let pendingAdoptAcceptTimeoutId: number | null = null;
  let pendingAdoptAccept: { sid: string; path: string } | null = null;
  const ADOPT_FLASH_MS = 1500;
  const ADOPT_PENDING_MS = 30000;

  let statusText = "连接中…";
  let cancelledStatusTimer: number | null = null;
  let cancelSafetyTimer: number | null = null;
  let destroyed = false;
  let thinkingStarted = 0;
  let thinkingTimer: number | null = null;
  let renderThrottleTimer: number | null = null;
  let renderedPrints: string[] = [];
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
        scrollChatToBottom();
      },
      onTurnStart: (event) => {
        resetTurnCacheStats();
        topbarState.intentLabel = event.intent_label;
        topbarState.checkerLabel = "";
        if (perspective === "night" && isRecallIntent(event.intent, event.intent_label)) {
          recallHighlightTurns = new Set(recentTurnIndices(chat.model.blocks, RECALL_TURNS));
          renderChat();
          requestAnimationFrame(() => scrollToRecallTurns());
        }
        renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
        setStatus(event.intent_label);
      },
      onCheckerVerdict: (event) => {
        topbarState.checkerLabel = checkerVerdictStatusText(event.verdict);
        renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
        setStatus(topbarState.checkerLabel);
      },
      onConfirmRequest: () => {
        setComposerEnabled(false);
        setStatus("等待确认…");
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
        <div class="sidebar-services" id="sidebar-services"></div>
        <div class="sidebar-footer" id="sidebar-footer">
          <div class="sidebar-change-banner hidden" id="sidebar-change-banner"></div>
          <div class="sidebar-icon-bar" id="sidebar-icon-bar">
            <button type="button" class="sidebar-icon-btn is-active" data-panel="tasks" title="当下"><span class="sidebar-icon">◎</span></button>
            <button type="button" class="sidebar-icon-btn" data-panel="plan" title="完整计划"><span class="sidebar-icon">☰</span></button>
            <button type="button" class="sidebar-icon-btn" data-panel="docs" title="文档"><span class="sidebar-icon">📄</span></button>
            <button type="button" class="sidebar-icon-btn" data-panel="threads" title="会话线" id="icon-btn-threads">
              <span class="sidebar-icon">⎇</span>
              <span class="sidebar-icon-badge" id="thread-count-badge">0</span>
            </button>
            <button type="button" class="sidebar-icon-btn" data-panel="projects" title="我的项目" id="icon-btn-projects">
              <span class="sidebar-icon">▢</span>
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
        <section class="unified-expand hidden" id="unified-expand"></section>
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
          <section class="unified-plan-review hidden" id="unified-plan-review" aria-label="计划审阅"></section>
          <section class="unified-plan-full hidden" id="unified-plan-full" aria-label="完整计划"></section>
        </div>
        <div class="unified-status" id="unified-status"></div>
        <div class="unified-token-bar hidden" id="unified-token-bar"></div>
        <footer class="unified-composer" id="unified-composer">
          <button type="button" class="unified-btn" id="unified-stop" hidden>停止</button>
          <textarea class="unified-input" id="unified-input" rows="1" placeholder="输入消息，或拖入文件…"></textarea>
          <button type="button" class="unified-btn unified-btn-accent" id="unified-send">发送</button>
        </footer>
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
  const workbenchEmptyEl = root.querySelector<HTMLElement>("#workbench-empty")!;
  const emptyNewBtn = root.querySelector<HTMLButtonElement>("#empty-new-project")!;
  const emptyPickBtn = root.querySelector<HTMLButtonElement>("#empty-pick-project")!;
  const emptyFreeChatBtn = root.querySelector<HTMLButtonElement>("#empty-free-chat")!;
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
      "输入消息或拖入文件；改计划会自动交给计划搭档";
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
    },
  });

  // ---- visual sync ----
  function syncWorkingVisual(): void {
    const working = chat.isWorking();
    projectState.turnInProgress = working;
    shellEl.classList.toggle("is-working", working);
    stopBtn.hidden = !(working || chat.model.confirmPending);
    stopBtn.disabled = chat.model.cancelRequested;
    setAgentBusy(working, perspective === "project" ? "project" : "grow");
  }

  function setStatus(text: string): void {
    statusText = text;
    if (text === "思考中…") {
      thinkingStarted = Date.now();
      if (thinkingTimer === null) {
        thinkingTimer = window.setInterval(() => {
          if (statusText === "思考中…" && thinkingStarted > 0) {
            const elapsed = Math.round((Date.now() - thinkingStarted) / 1000);
            statusEl.textContent = `思考中…（${elapsed}s）`;
          }
        }, 1000);
      }
    } else {
      if (thinkingTimer !== null) {
        window.clearInterval(thinkingTimer);
        thinkingTimer = null;
      }
      thinkingStarted = 0;
      statusEl.textContent = text;
    }
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
    lastTokenUsage = usage;
    lastTokenLimit = limit;
    if (usage === undefined || limit === undefined || limit <= 0) {
      if (lastLlmCacheRatio === undefined && turnCachePromptTotal <= 0) {
        tokenBar.classList.add("hidden");
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
        ? "暂无项目会话"
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
          <button type="button" class="unified-sessions-tab ${sessionsTab === "project" ? "is-active" : ""}" data-sessions-tab="project" role="tab">项目 (${projectItems.length})</button>
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
          setStatus(`打开会话 ${sid}…`);
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
    renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
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
      projectState.switchInProgress = true;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      try {
        client.switchProject(projectState.switchOverlay.projectId, {
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
        setStatus(`打开会话线 ${sid}…`);
      } catch (err) {
        setStatus(`打开会话线失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onReturnActiveThread: () => {
      const active = projectState.activeSessionId.trim();
      if (!active) return;
      projectCallbacks.onOpenThread(active);
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
    if (focus === "chat") {
      projectState.reviewFocusId = null;
    }
    syncMainFocusView();
  }

  function syncMainFocusView(): void {
    const focus = projectState.mainFocus;
    shellEl.dataset.mainFocus = focus;
    chatEl.classList.toggle("hidden", focus !== "chat");
    planReviewEl.classList.toggle("hidden", focus !== "plan_review");
    planFullEl.classList.toggle("hidden", focus !== "plan_full");
    if (focus === "plan_review") {
      renderPlanReviewPane();
    } else if (focus === "plan_full") {
      renderPlanFullPane();
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
    });
  }

  function renderPlanFullPane(): void {
    const highlight =
      projectState.highlightChanges && projectState.highlightedLines.size > 0
        ? projectState.highlightedLines
        : null;
    planFullEl.innerHTML = `${renderPlanFullHeader()}<div class="unified-plan-full-body">${renderPlanTaskFlow(projectState, highlight)}</div>`;
  }

  function openPlanReview(suggestionId?: string): void {
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
    setMainFocus("plan_review");
  }

  function openPlanFull(): void {
    projectState.overlayPanel = null;
    setMainFocus("plan_full");
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
    const { sid, path } = pendingAdoptAccept;
    if (projectState.suggestions.some((s) => s.id === sid)) return;

    const notices = projectState.partnerNotices.join("\n");
    clearPendingAdopt();

    if (/已撤回|无效提案/i.test(notices)) {
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      if (projectState.mainFocus === "plan_review") {
        renderPlanReviewPane();
      }
      return;
    }

    const match = notices.match(/已采纳写入\s+(\S+)/);
    const adoptPath = match?.[1] || path;
    const moreAfter = projectState.suggestions.some((s) => Boolean(s.action));
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

  function acceptSuggestionById(sid: string): void {
    if (projectState.suggestionAdoptFlash || pendingAdoptAccept) return;
    const sug = projectState.suggestions.find((s) => s.id === sid);
    if (!sug?.action) return;

    const path = adoptPathFromSuggestion(sug);
    pendingAdoptAccept = { sid, path };
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
      client.acceptPlanSuggestion(sid);
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

  function jumpToCurrentTurnProcess(): void {
    const blocks = chat.model.blocks;
    for (let i = blocks.length - 1; i >= 0; i--) {
      const block = blocks[i];
      if (block.kind !== "process") continue;
      if (block.collapsed) {
        chat.toggleProcessCollapsed(block.turnKey);
      }
      requestAnimationFrame(() => {
        const el = chatEl.querySelector<HTMLElement>(`.unified-process[data-turn="${block.turnKey}"]`);
        el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
      return;
    }
    scrollChatToBottom();
  }

  function jumpToReviewSummary(): void {
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

  // ---- chat rendering ----
  function isRecentTurnBlock(turnIndex: number): boolean {
    return turnIndex >= chat.currentTurnIndex() - (FOCUS_TURNS - 1);
  }

  function truncateToolHint(text: string, max = 72): string {
    const oneLine = text.replace(/\s+/g, " ").trim();
    if (oneLine.length <= max) return oneLine;
    return `${oneLine.slice(0, max - 1)}…`;
  }

  /** One-line tool rows (DESKTOP §3.2.2 A 层); fold older rows when many (UX-023). */
  function renderCompactToolLines(
    tools: NonNullable<Extract<ChatBlock, { kind: "process" }>["tools"]>,
    turnKey: string,
  ): string {
    if (!tools.length) return "";
    const now = Date.now();

    function renderOne(
      t: NonNullable<Extract<ChatBlock, { kind: "process" }>["tools"]>[number],
    ): string {
      const running = t.status === "running";
      const elapsedMs = running ? now - t.startedAt : (t.endedAt ?? now) - t.startedAt;
      const mark = running ? "·" : t.status === "ok" ? "✓" : "✗";
      const elapsedLabel = running
        ? `运行中… ${formatToolElapsed(elapsedMs)}`
        : formatToolElapsed(elapsedMs);
      const progressBit =
        running && t.progressText
          ? ` · ${escapeHtml(truncateToolHint(t.progressText, 48))}`
          : "";
      const failHint =
        !running && t.status === "fail" && t.endSummary
          ? ` · ${escapeHtml(truncateToolHint(t.endSummary, 48))}`
          : "";
      const logsBit =
        !running && t.status === "fail" && t.logsTail
          ? `<details class="unified-tool-logs-inline"><summary>日志</summary><pre>${escapeHtml(t.logsTail)}</pre></details>`
          : "";
      return `<div class="unified-process-line unified-tool-line is-${t.status}" data-tool-call="${escapeHtml(t.callId)}" data-started-at="${t.startedAt}" data-status="${t.status}">
        <span class="unified-tool-mark">${mark}</span>
        <span class="unified-tool-name">${escapeHtml(t.tool)}</span>
        <span class="unified-tool-hint">${escapeHtml(truncateToolHint(t.summary))}</span>
        <span class="unified-tool-elapsed">${escapeHtml(elapsedLabel)}</span>${progressBit}${failHint}${logsBit}
      </div>`;
    }

    const hiddenCount = Math.max(0, tools.length - PROCESS_TOOL_LINES_CAP);
    const hidden = hiddenCount > 0 ? tools.slice(0, hiddenCount) : [];
    const visible = hiddenCount > 0 ? tools.slice(hiddenCount) : tools;
    const failHidden = hidden.filter((t) => t.status === "fail").length;
    const foldOpen = toolsListExpanded.has(turnKey);
    const foldBit =
      hidden.length > 0
        ? `<details class="unified-tool-fold" data-tools-fold="${escapeHtml(turnKey)}"${foldOpen ? " open" : ""}>
            <summary>更早 ${hidden.length} 个工具${failHidden > 0 ? ` · ${failHidden} 失败` : ""}</summary>
            <div class="unified-tool-fold-body">${hidden.map(renderOne).join("")}</div>
          </details>`
        : "";
    return `<div class="unified-tool-lines">${foldBit}${visible.map(renderOne).join("")}</div>`;
  }

  function renderThinkingAccordion(block: Extract<ChatBlock, { kind: "process" }>): string {
    const waiting = Boolean(block.llmPending) && !block.reasoning.trim();
    if (!block.reasoning.trim() && !waiting) return "";
    if (waiting) {
      return `<div class="unified-thinking is-waiting is-streaming" data-turn="${escapeHtml(block.turnKey)}">
        <div class="unified-thinking-summary is-static" aria-expanded="true">思考中…</div>
      </div>`;
    }
    const open = isThinkingBodyOpen(block);
    const streaming = block.reasoningPhase === "streaming";
    const title = thinkingTitleLabel(block);
    const openCls = open ? " is-open" : "";
    const streamCls = streaming ? " is-streaming" : " is-pinned";
    const body = open
      ? `<div class="unified-thinking-body">${escapeHtml(block.reasoning)}</div>`
      : "";
    return `<div class="unified-thinking${streamCls}${openCls}" data-turn="${escapeHtml(block.turnKey)}">
      <button type="button" class="unified-thinking-summary" data-thinking-toggle="${escapeHtml(block.turnKey)}" aria-expanded="${open ? "true" : "false"}">${escapeHtml(title)}</button>
      ${body}
    </div>`;
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
    chatEl.querySelectorAll<HTMLElement>(".unified-thinking.is-streaming").forEach((el) => {
      const turnKey = el.dataset.turn;
      if (!turnKey) return;
      const block = chat.model.blocks.find(
        (b) => b.kind === "process" && b.turnKey === turnKey,
      );
      if (block?.kind !== "process") return;
      const summary = el.querySelector<HTMLElement>(".unified-thinking-summary");
      if (summary) summary.textContent = thinkingTitleLabel(block);
      const body = el.querySelector<HTMLElement>(".unified-thinking-body");
      if (body) body.scrollTop = body.scrollHeight;
    });
  }

  function scrollStreamingThinkingBodies(): void {
    chatEl.querySelectorAll<HTMLElement>(".unified-thinking.is-streaming .unified-thinking-body").forEach((body) => {
      body.scrollTop = body.scrollHeight;
    });
  }

  let toolElapsedTimer: number | null = null;
  function syncToolElapsedTimer(): void {
    const hasRunning = chat.model.blocks.some(
      (b) => b.kind === "process" && b.tools?.some((t) => t.status === "running"),
    );
    const hasStreamingThinking = chat.model.blocks.some(
      (b) => b.kind === "process" && (b.reasoningPhase === "streaming" || b.llmPending),
    );
    if ((hasRunning || hasStreamingThinking) && toolElapsedTimer === null) {
      toolElapsedTimer = window.setInterval(() => {
        tickRunningToolElapsed();
        tickStreamingThinking();
      }, 1000);
    } else if (!hasRunning && !hasStreamingThinking && toolElapsedTimer !== null) {
      window.clearInterval(toolElapsedTimer);
      toolElapsedTimer = null;
    }
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
      const verdictLabel =
        block.verdict && block.status === "done"
          ? ` · ${String(block.verdict).toUpperCase()}`
          : "";
      const blockers =
        block.status === "done" && (block.blockersCount ?? 0) > 0
          ? ` · ${block.blockersCount} 项阻塞`
          : "";
      const title =
        block.status === "running"
          ? "交付审查 · 进行中…"
          : `交付审查 · 完成${verdictLabel}${blockers}`;
      const detail =
        block.status === "running"
          ? escapeHtml(block.taskPreview || "正在审查交付物…")
          : escapeHtml(block.summary || block.taskPreview || "");
      return `<article class="unified-plan-subagent" data-turn-index="${block.turnIndex}" data-status="${block.status}">
        <div class="unified-plan-subagent-title">${escapeHtml(title)}</div>
        <div class="unified-plan-subagent-body">${detail}</div>
      </article>`;
    }
    if (block.kind === "assistant" || block.kind === "assistant-streaming") {
      let cls = "unified-turn unified-turn-assistant";
      if (perspective === "night") {
        if (!isRecentTurnBlock(block.turnIndex)) cls += " unified-turn-dim";
        if (recallHighlightTurns.has(block.turnIndex)) cls += " unified-turn-recall";
        if (block.kind === "assistant-streaming") cls += " unified-turn-streaming";
      }
      return `<article class="${cls}" data-turn-index="${block.turnIndex}">
        <div class="unified-turn-label">助手</div>
        <div class="unified-turn-body unified-markdown">${renderMarkdown(block.text)}</div>
      </article>`;
    }
    if (block.kind === "notice") {
      return `<p class="text-muted">${escapeHtml(block.text)}</p>`;
    }
    // night perspective: skip process and confirm blocks (use overlay instead)
    if (block.kind === "process") {
      if (perspective === "night") return "";
      const thinkingHtml = renderThinkingAccordion(block);
      const toolsHtml = renderCompactToolLines(block.tools ?? [], block.turnKey);
      const title = block.tools?.some((t) => t.status === "running") ? "执行中…" : "过程";
      const toggle = block.collapsed ? "展开" : "收起";
      return `
        <div class="unified-process ${block.collapsed ? "collapsed" : ""}" data-turn="${block.turnKey}">
          <div class="unified-process-header">
            <span>${title}</span>
            <button type="button" class="unified-btn" data-process-toggle="${block.turnKey}">${toggle}</button>
          </div>
          <div class="unified-process-body">
            ${thinkingHtml}
            ${toolsHtml}
          </div>
        </div>`;
    }
    if (block.kind === "confirm") {
      if (perspective === "night") return ""; // shown in overlay
      const disabled = block.resolved ? "disabled" : "";
      const inProgress = isConfirmInProgressLabel(block.resolved);
      const isTerminal = Boolean(block.resolved) && !inProgress;
      const resolvedCls = block.resolved
        ? inProgress
          ? "is-running"
          : "resolved is-compact"
        : "";
      // Resolved confirms are already reflected in the process tool lines (DESKTOP §3.2.2).
      if (isTerminal) return "";
      const resolved = block.resolved
        ? `<div class="text-muted unified-confirm-status">${escapeHtml(block.resolved)}</div>`
        : `
        <div class="unified-expand-actions">
          <button type="button" class="unified-btn unified-btn-accent" data-confirm="y" data-id="${block.requestId}" ${disabled}>同意</button>
          <button type="button" class="unified-btn unified-btn-danger" data-confirm="n" data-id="${block.requestId}" ${disabled}>拒绝</button>
          ${
            block.allowApproveAll
              ? `<button type="button" class="unified-btn" data-confirm="a" data-id="${block.requestId}" ${disabled}>本会话 workspace 均允许</button>`
              : ""
          }
        </div>`;
      return `
        <div class="unified-surface unified-confirm ${resolvedCls}">
          <div class="unified-expand-title">工具确认</div>
          <pre class="unified-confirm-preview">${escapeHtml(block.preview)}</pre>
          ${resolved}
        </div>`;
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
      case "process":
        return `P${block.turnKey}:${block.lines.length}:${block.reasoning.length}:${block.collapsed ? 1 : 0}:${block.reasoningPhase ?? "idle"}:${block.reasoningUserOpen ? 1 : 0}:${block.llmPending ? 1 : 0}:${(block.tools ?? [])
          .map((t) => `${t.callId}:${t.status}:${t.endSummary ?? ""}:${t.progressText ?? ""}:${(t.logsTail ?? "").length}`)
          .join(",")}`;
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
        if (turnKey) chat.toggleThinkingOpen(turnKey);
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
  }

  function scrollToBottomIfNear(): void {
    const distFromBottom = chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight;
    if (distFromBottom < 50) {
      chatEl.scrollTop = chatEl.scrollHeight;
    }
  }

  /** UX-025: after session.history — always land on latest messages. */
  function scrollChatToBottom(): void {
    requestAnimationFrame(() => {
      chatEl.scrollTop = chatEl.scrollHeight;
    });
  }

  function doRender(): void {
    const curPrints = chat.model.blocks.map(blockPrint);
    // A3-2: pure tail-append — insertAdjacentHTML instead of full innerHTML
    if (renderedPrints.length > 0 && curPrints.length > renderedPrints.length) {
      const isTailAppend = renderedPrints.every(
        (fp, i) => fp === curPrints[i],
      );
      if (isTailAppend) {
        const newBlocks = chat.model.blocks.slice(renderedPrints.length);
        const newHtml = newBlocks.map(renderBlock).join("");
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
          if (btn) {
            btn.textContent = el.classList.contains("collapsed") ? "展开" : "收起";
          }
          renderedPrints = curPrints;
          return;
        }
      }
    }

    // full render (initial, or escape hatch for unhandled change patterns)
    chatEl.innerHTML = chat.model.blocks.map(renderBlock).join("");
    bindNewProcessToggles(chatEl);
    setupFocusObserver();
    scrollToBottomIfNear();
    renderedPrints = curPrints;
  }

  const RENDER_THROTTLE_MS = 100;

  function renderChat(): void {
    // Immediate paint; coalesce bursty follow-ups (streaming deltas) into one trailing pass.
    doRender();
    scrollStreamingThinkingBodies();
    syncToolElapsedTimer();
    if (renderThrottleTimer !== null) return;
    renderThrottleTimer = window.setTimeout(() => {
      renderThrottleTimer = null;
      doRender();
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

    confirmGlass.innerHTML = `
      <div class="unified-confirm-glass-card ${confirm.resolved ? "resolved" : ""}">
        <div class="unified-confirm-glass-title">工具确认</div>
        <pre class="unified-confirm-glass-preview">${escapeHtml(confirm.preview)}</pre>
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

  // Icon bar: switch overlay panel
  projectEls.iconBar.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>(".sidebar-icon-btn");
    if (!btn?.dataset.panel) return;
    const panel = btn.dataset.panel;
    if (panel === "tasks") {
      projectState.overlayPanel = null;
      projectState.currentDocPath = "";
      projectState.currentDocContent = "";
    } else {
      projectState.switchConfirmTarget = null;
      projectState.projectSearchQuery = "";
      projectState.currentDocPath = "";
      projectState.currentDocContent = "";
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

  // Overlay back button
  projectEls.overlayBackBtn.addEventListener("click", () => {
    projectState.overlayPanel = null;
    projectState.switchConfirmTarget = null;
    projectState.currentDocPath = "";
    projectState.currentDocContent = "";
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
  });

  // Decision surface: plan overlay + suggestion stack + turn summary
  projectEls.taskFlow.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-action]");
    if (btn?.dataset.action) {
      switch (btn.dataset.action) {
        case "open-full-plan":
          openPlanFull();
          return;
        case "jump-turn-process":
          jumpToCurrentTurnProcess();
          return;
        case "jump-review-summary":
          jumpToReviewSummary();
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
      projectState.currentDocPath = docBtn.dataset.docPath;
      projectState.currentDocContent = "";
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      try { client.readDoc(projectState.currentDocPath); } catch { /* ignore */ }
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
      case "dismiss-partner-notice":
        projectState.partnerNotices = [];
        projectState.adoptedFooterMessage = null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "dismiss-warnings":
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
        chat.handleEvent(event);
        projectState.currentSessionId = event.session_id;
        if (event.project_id) {
          projectState.projectId = event.project_id;
          projectState.planStatus = event.project_plan_status ?? projectState.planStatus;
          projectState.tasksDone = event.project_tasks_done ?? projectState.tasksDone;
          projectState.tasksTotal = event.project_tasks_total ?? projectState.tasksTotal;
          const plan = event.project_plan_label ?? "计划待确认";
          topbarState.projectLabel = `项目 · ${event.project_id} · ${plan}`;
          syncFreeChatFromSession(true, event.session_id);
          setPerspective("project", "session");
        } else {
          projectState.projectId = "";
          topbarState.projectLabel = "";
          syncFreeChatFromSession(false, event.session_id);
          setPerspective("project", "session");
        }
        updatePlaceholder();
        updateWorkbenchEmpty();
        syncArchivedViewUi();
        setStatus(`会话 ${event.session_id} · ${event.llm_model_label || "Flash"} · ${event.turn_mode_label}`);
        renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        client.listSessions();
        if (event.project_id) {
          refreshProjectThreads();
        }
        break;

      case "session.list":
        sessionsDropdown = event.sessions;
        topbarState.sessionCount = event.sessions.length;
        renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
        if (sessionsOpen) renderSessionsDropdown();
        break;

      case "session.memory":
        topbarState.memoryLabel = `${event.message_count} 条 · ${event.memory_mode_label}`;
        renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
        updateTokenBar(event.token_usage, event.token_limit);
        break;

      case "llm.usage":
        recordLlmUsage(event.prompt_tokens, event.cached_tokens, event.cache_ratio);
        break;

      case "evolve.proposals":
        handleProposalsEvent(event.items);
        break;

      case "project.state":
        applyProjectStateEvent(projectState, event);
        if (projectState.projectId) {
          freeChatActive = false;
        }
        if (!perspectiveLocked) setPerspective("project", "session");
        updatePlaceholder();
        updateWorkbenchEmpty();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          topbarState.projectLabel = `项目 · ${projectState.projectId} · ${planStatusLabel()}`;
          renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
          refreshServices();
          refreshProjectThreads();
        }
        break;

      case "project.plan.state":
        applyProjectPlanState(projectState, event);
        resolvePendingAdopt();
        projectState.partnerBusy = false;
        if (!perspectiveLocked) setPerspective("project", "session");
        updatePlaceholder();
        if (projectState.mainFocus === "plan_review") {
          renderPlanReviewPane();
        } else if (projectState.mainFocus === "plan_full") {
          renderPlanFullPane();
        }
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          topbarState.projectLabel = `项目 · ${projectState.projectId} · ${planStatusLabel()}`;
          renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
        }

        // Auto-confirm timer for task-level changes
        if (projectState.autoConfirmTimerId !== null) {
          window.clearInterval(projectState.autoConfirmTimerId);
          projectState.autoConfirmTimerId = null;
        }
        if (projectState.changesLevel === "task") {
          let remaining = 30;
          const countdownInterval = window.setInterval(() => {
            remaining--;
            const el = sidebarEl.querySelector<HTMLElement>("[data-countdown]");
            if (el) el.textContent = String(remaining);
            if (remaining <= 0) {
              window.clearInterval(countdownInterval);
              if (projectState.autoConfirmTimerId === countdownInterval as unknown as number) {
                projectState.changesLevel = null;
                try { client.planConfirmChanges(); } catch { /* */ }
                renderProjectSidebar(projectEls, projectState, projectCallbacks);
              }
            }
          }, 1000);
          projectState.autoConfirmTimerId = countdownInterval as unknown as number;
        }
        break;

      case "plan.subagent.start":
        projectState.partnerBusy = true;
        chat.pushPlanSubagentCard("running", { taskPreview: event.task_preview });
        setStatus("计划搭档整理中…");
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "plan.subagent.done":
        projectState.partnerBusy = false;
        chat.updatePlanSubagentCard("proposals_ready", {
          summary: event.summary,
          proposalCount: event.proposal_count,
        });
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
        applyProjectThreadsEvent(projectState, event);
        projectState.currentSessionId = projectState.currentSessionId || event.active_session_id || "";
        syncArchivedViewUi();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.thread.new.done":
        projectState.currentSessionId = event.session_id;
        projectState.activeSessionId = event.session_id;
        if (event.session_replaced) {
          client.refreshSession();
        }
        client.refreshProject();
        refreshProjectThreads();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          topbarState.projectLabel = `项目 · ${projectState.projectId} · ${planStatusLabel()}`;
          renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
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
        projectState.switchInProgress = false;
        projectState.switchOverlay = null;
        projectState.pendingPickerId = "";
        projectState.projectId = event.project_id;
        if (event.project_id) {
          freeChatActive = false;
        }
        if (event.session_replaced) {
          client.refreshSession();
        }
        client.listProjects();
        client.refreshProject();
        refreshProjectThreads();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          topbarState.projectLabel = `项目 · ${projectState.projectId} · ${planStatusLabel()}`;
          renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
        }
        updatePlaceholder();
        updateWorkbenchEmpty();
        composerWire.syncSendEnabled();
        setStatus(event.message);
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
          topbarState.projectLabel = `项目 · ${projectState.projectId} · ${planStatusLabel()}`;
          renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
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
        client.refreshProject();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.doc.list.done":
        projectState.projectDocs = event.docs;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.doc.read.done":
        projectState.currentDocContent = event.content;
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

      case "tool.start":
        chat.handleEvent(event);
        setStatus(`· ${event.tool}`);
        break;

      case "llm.pending":
        chat.handleEvent(event);
        if (!chat.model.cancelRequested) {
          setStatus("思考中…");
        }
        break;

      case "tool.progress":
        chat.handleEvent(event);
        if (typeof event.text === "string" && event.text.trim()) {
          setStatus(event.text.trim());
        }
        break;

      case "tool.end":
        chat.handleEvent(event);
        if (!chat.model.confirmPending) {
          setStatus(event.ok ? (chat.isWorking() ? "处理中…" : "就绪") : "工具失败");
        }
        break;

      case "reasoning.delta":
        chat.handleEvent(event);
        if (!chat.model.cancelRequested) {
          setStatus("思考中…");
        }
        break;

      case "assistant.done":
        chat.handleEvent(event);
        if (perspective === "project") {
          client.refreshProject();
        }
        if (!chat.model.cancelRequested && !chat.model.confirmPending) {
          setStatus("就绪");
        }
        break;

      case "turn.end":
        chat.handleEvent(event);
        break;

      case "notice":
        chat.model.blocks.push({ kind: "notice", text: event.text });
        renderChat();
        if (
          projectState.planStatus !== "confirmed" &&
          /计划已确认|可以开始写代码/.test(event.text)
        ) {
          projectState.planStatus = "confirmed";
          projectState.planOverlay = null;
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
          if (projectState.projectId) {
            topbarState.projectLabel = `项目 · ${projectState.projectId} · ${planStatusLabel()}`;
            renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
          }
          client.refreshProject();
        }
        break;

      case "confirm.request":
      case "confirm.done":
        chat.handleEvent(event);
        break;

      case "error":
        if (projectState.servicesLoading) {
          projectState.servicesLoading = false;
          projectState.servicesError = event.message;
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
        if (projectState.switchInProgress || projectState.switchOverlay) {
          projectState.switchInProgress = false;
          projectState.pendingPickerId = "";
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
    if (projectState.planStatus === "confirmed") {
      if (projectState.tasksAllDone && projectState.tasksTotal > 0) return "全部完成";
      const open = Math.max(0, projectState.tasksTotal - projectState.tasksDone);
      return `${open}/${projectState.tasksTotal} 未完成`;
    }
    if (projectState.planStatus === "plan_dirty") return "计划已变更 · 待确认";
    return "计划待确认";
  }

  // ---- initial render ----
  updatePlaceholder();
  renderTopbar(topbarEl, topbarState, openProposals, handleNewChat, handleOpenSessions, handleNewProject, handleNewThread);
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

  return () => {
    destroyed = true;
    if (cancelledStatusTimer !== null) window.clearTimeout(cancelledStatusTimer);
    clearCancelSafety();
    if (thinkingTimer !== null) window.clearInterval(thinkingTimer);
    if (toolElapsedTimer !== null) window.clearInterval(toolElapsedTimer);
    if (renderThrottleTimer !== null) window.clearTimeout(renderThrottleTimer);
    renderedPrints = [];
    focusObserver?.disconnect();
    fileDrop.destroy();
    off();
    shellEl.classList.remove("is-working");
    setAgentBusy(false);
    root.innerHTML = "";
  };
}
